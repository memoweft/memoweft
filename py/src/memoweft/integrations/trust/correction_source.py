"""Bind a correction to one actual DSH message and its verbatim fact span."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import sqlite3
from typing import Mapping

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


def store_correction_source(db: sqlite3.Connection, source: CorrectionSource, *, subject_id: str,
                            host_id: str, clock: Clock, permissions: Mapping[str, bool]) -> str:
    if db.execute('SELECT 1 FROM hard_deleted_origin WHERE origin_hash=?', (sha256(source.origin_id.encode()).hexdigest(),)).fetchone():
        raise TrustCommandError('hard_deleted_source')
    row = db.execute('SELECT id,subject_id,source_kind,host_id,raw_content,occurred_at,preceding_ai_context,deleted_at,corrects_evidence_id,'
                     'allow_local_read,allow_cloud_read,allow_inference '
                     'FROM evidence WHERE origin_id=?', (source.origin_id,)).fetchone()
    if row is not None:
        if (row[1] != subject_id or row[2] != 'spoken' or row[3] != host_id or row[4] != source.content or row[7] is not None or row[8] is not None
                or source.occurred_at is not None and row[5] != source.occurred_at or row[6] != source.preceding_ai_context):
            raise TrustCommandError('correction_source_conflict')
        # The same real message must keep the stricter permissions regardless
        # of which path accepted it first. Correction defaults never widen it.
        SqliteEvidenceStore(db, clock=clock).update(str(row[0]),
            allow_local_read=row[9] == 1 and permissions['allow_local_read'],
            allow_cloud_read=row[10] == 1 and permissions['allow_cloud_read'],
            allow_inference=row[11] == 1 and permissions['allow_inference'])
        return str(row[0])
    evidence_id = SqliteEvidenceStore(db, clock=clock).put(EvidenceInput(subject_id=subject_id, source_kind='spoken',
        host_id=host_id, origin_id=source.origin_id, occurred_at=source.occurred_at, raw_content=source.content,
        preceding_ai_context=source.preceding_ai_context, allow_local_read=permissions['allow_local_read'],
        allow_cloud_read=permissions['allow_cloud_read'], allow_inference=permissions['allow_inference'])).id
    db.execute('INSERT INTO boundary_evidence_content (evidence_id,raw_content_hash) VALUES (?,?)',
               (evidence_id, sha256(source.content.encode()).hexdigest()))
    return evidence_id
