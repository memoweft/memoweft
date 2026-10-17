"""Bind a correction to one actual DSH message and its verbatim fact span."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import sqlite3
from typing import Any, Mapping

from ...clock import Clock
from ...store import SqliteEvidenceStore
from ...types import EvidenceInput
from .command_store import TrustCommandError


@dataclass(frozen=True)
class CorrectionSource:
    origin_id: str
    content: str
    quote: str
    start: int
    end: int
    occurred_at: str | None
    preceding_ai_context: str | None


def parse_correction_source(value: object, *, subject_id: str, host_id: str) -> CorrectionSource:
    from ..dsh_bridge import _origin_id, _occurred_at
    from ..hermes.batch_adapter import _evidence_segments, _evidence_sentences

    if not isinstance(value, dict) or set(value) - {'message_id', 'session_id', 'content', 'quote', 'timestamp', 'preceding_ai_context'}:
        raise TrustCommandError('invalid_correction_source')
    for field, maximum in [('message_id', 160), ('session_id', 128), ('content', 16000), ('quote', 16000)]:
        item = value.get(field)
        if not isinstance(item, str) or not item.strip() or len(item) > maximum:
            raise TrustCommandError('invalid_correction_source')
    content, quote = str(value['content']), str(value['quote'])
    if content.count(quote) != 1:
        raise TrustCommandError('invalid_correction_source_quote')
    spans = [row for row in [*_evidence_segments(content), *_evidence_sentences(content)] if row['text'] == quote]
    if not spans:
        raise TrustCommandError('invalid_correction_source_quote')
    previous = value.get('preceding_ai_context')
    if previous is not None and (not isinstance(previous, str) or len(previous) > 128000):
        raise TrustCommandError('invalid_correction_source')
    occurred = _occurred_at(value.get('timestamp'))
    if value.get('timestamp') is not None and occurred is None:
        raise TrustCommandError('invalid_correction_source')
    origin = _origin_id(message={'message_id': value['message_id']}, content=content,
        session_id=str(value['session_id']), message_index=0, subject_id=subject_id, host_id=host_id, boundary_id='')
    return CorrectionSource(origin, content, quote, spans[0]['start'], spans[0]['end'], occurred, previous)


def _pending_metadata_rows_batch(db: sqlite3.Connection, evidence_ids: set[str]) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    from ..hermes.batch_adapter import _canonical, _hash_text

    rows: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    if not evidence_ids:
        return rows
    for ledger_id, content, raw in db.execute("SELECT id,content,payload_json FROM evidence_ledger WHERE id LIKE 'evidence-ledger-%' "
                                             "AND payload_json LIKE '%\"source_metadata_pending\"%'"):
        try:
            payload, link = json.loads(raw), json.loads(content)
        except (TypeError, ValueError):
            continue
        if (not isinstance(payload, dict) or type(payload.get('schema_version')) is not int or payload['schema_version'] != 1
                or not isinstance(link, dict) or not isinstance(link.get('evidence_id'), str)
                or link['evidence_id'] not in evidence_ids or link.get('relation') != 'support'
                or not isinstance(payload.get('source_metadata_pending'), list)
                or not all(isinstance(field, str) and field in ('timestamp', 'preceding_ai_context') for field in payload['source_metadata_pending'])):
            continue
        evidence_id = link['evidence_id']
        for key, kind in [('cognition_id', 'formation'), ('relationship_id', 'relationship_formation'), ('world_event_id', 'world_event')]:
            if (isinstance(link.get(key), str) and type(payload.get('start')) is int and type(payload.get('end')) is int
                    and ledger_id == 'evidence-ledger-' + _hash_text(_canonical([kind, link[key], evidence_id, payload['start'], payload['end']]))):
                rows.setdefault(evidence_id, []).append((str(ledger_id), payload))
                break
    return rows


def _pending_metadata_rows(db: sqlite3.Connection, evidence_id: str) -> list[tuple[str, dict[str, Any]]]:
    return _pending_metadata_rows_batch(db, {evidence_id}).get(evidence_id, [])


def pending_correction_source_metadata_batch(db: sqlite3.Connection, evidence_ids: set[str]) -> dict[str, set[str]]:
    """Validate requested evidence annotations in one ledger scan, without caching."""
    return {evidence_id: {field for _, payload in rows for field in payload['source_metadata_pending']}
            for evidence_id, rows in _pending_metadata_rows_batch(db, evidence_ids).items()}


def pending_correction_source_metadata(db: sqlite3.Connection, evidence_id: str) -> set[str]:
    return {field for _, payload in _pending_metadata_rows(db, evidence_id)
            for field in ('timestamp', 'preceding_ai_context') if field in payload['source_metadata_pending']}


def complete_correction_source_metadata(db: sqlite3.Connection, evidence_id: str, *, occurred_at: str | None,
                                        preceding_ai_context: str | None) -> None:
    completed = ({'timestamp'} if occurred_at is not None else set()) | ({'preceding_ai_context'} if preceding_ai_context is not None else set())
    pending = pending_correction_source_metadata(db, evidence_id)
    if 'timestamp' in pending and occurred_at is not None:
        db.execute('UPDATE evidence SET occurred_at=? WHERE id=?', (occurred_at, evidence_id))
    if preceding_ai_context is not None:
        db.execute('UPDATE evidence SET preceding_ai_context=COALESCE(preceding_ai_context,?) WHERE id=?', (preceding_ai_context, evidence_id))
    for ledger_id, payload in _pending_metadata_rows(db, evidence_id):
        remaining = sorted(set(payload['source_metadata_pending']) - completed)
        if remaining:
            payload['source_metadata_pending'] = remaining
        else:
            del payload['source_metadata_pending']
        db.execute('UPDATE evidence_ledger SET payload_json=? WHERE id=?', (json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':')), ledger_id))


def mark_pending_correction_source_metadata(db: sqlite3.Connection, source: CorrectionSource, evidence_id: str,
                                           kind: str, replacement_id: str, pending: set[str]) -> None:
    from ..hermes.batch_adapter import _canonical, _hash_text

    if not pending:
        return
    tag = {'cognition': 'formation', 'relationship': 'relationship_formation', 'event': 'world_event'}[kind]
    ledger_id = 'evidence-ledger-' + _hash_text(_canonical([tag, replacement_id, evidence_id, source.start, source.end]))
    row = db.execute('SELECT payload_json FROM evidence_ledger WHERE id=?', (ledger_id,)).fetchone()
    if row is None:
        raise TrustCommandError('correction_source_support_missing')
    payload = json.loads(row[0])
    # Field names only: retain the store's valid recording-time fallback, but
    # do not mistake it for a supplied message time. The command SAVEPOINT and
    # boundary transaction own both this annotation and later enrichment.
    payload['source_metadata_pending'] = sorted(pending)
    db.execute('UPDATE evidence_ledger SET payload_json=? WHERE id=?', (_canonical(payload), ledger_id))


def store_correction_source(db: sqlite3.Connection, source: CorrectionSource, *, subject_id: str,
                            host_id: str, clock: Clock, permissions: Mapping[str, bool]) -> tuple[str, set[str]]:
    if db.execute('SELECT 1 FROM hard_deleted_origin WHERE origin_hash=?', (sha256(source.origin_id.encode()).hexdigest(),)).fetchone():
        raise TrustCommandError('hard_deleted_source')
    row = db.execute('SELECT id,subject_id,source_kind,host_id,raw_content,occurred_at,preceding_ai_context,deleted_at,corrects_evidence_id,'
                     'allow_local_read,allow_cloud_read,allow_inference '
                     'FROM evidence WHERE origin_id=?', (source.origin_id,)).fetchone()
    if row is not None:
        pending = pending_correction_source_metadata(db, str(row[0]))
        if (row[1] != subject_id or row[2] != 'spoken' or row[3] != host_id or row[4] != source.content or row[7] is not None or row[8] is not None
                or source.occurred_at is not None and row[5] != source.occurred_at and 'timestamp' not in pending
                or source.preceding_ai_context is not None and row[6] is not None and row[6] != source.preceding_ai_context):
            raise TrustCommandError('correction_source_conflict')
        complete_correction_source_metadata(db, str(row[0]), occurred_at=source.occurred_at,
                                            preceding_ai_context=source.preceding_ai_context)
        # The same real message must keep the stricter permissions regardless
        # of which path accepted it first. Correction defaults never widen it.
        SqliteEvidenceStore(db, clock=clock).update(str(row[0]),
            allow_local_read=row[9] == 1 and permissions['allow_local_read'],
            allow_cloud_read=row[10] == 1 and permissions['allow_cloud_read'],
            allow_inference=row[11] == 1 and permissions['allow_inference'])
        return str(row[0]), pending_correction_source_metadata(db, str(row[0]))
    evidence_id = SqliteEvidenceStore(db, clock=clock).put(EvidenceInput(subject_id=subject_id, source_kind='spoken',
        host_id=host_id, origin_id=source.origin_id, occurred_at=source.occurred_at, raw_content=source.content,
        preceding_ai_context=source.preceding_ai_context, allow_local_read=permissions['allow_local_read'],
        allow_cloud_read=permissions['allow_cloud_read'], allow_inference=permissions['allow_inference'])).id
    db.execute('INSERT INTO boundary_evidence_content (evidence_id,raw_content_hash) VALUES (?,?)',
               (evidence_id, sha256(source.content.encode()).hexdigest()))
    return evidence_id, ({'timestamp'} if source.occurred_at is None else set()) | ({'preceding_ai_context'} if source.preceding_ai_context is None else set())
