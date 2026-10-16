"""Project unresolved explicit memory requests from the durable source jobs."""
from __future__ import annotations

import json
import re
import sqlite3
from hashlib import sha256
from typing import Any

from ...types import ModelTier
from ..trust.currentness import evidence_state
from .interactions import _model_eligible
from .recent import _CORRECTION, _related
from .reprocess import source_boundary_event

_REMEMBER = re.compile(r"记住|记着|记下来|记下|remember|keep in mind", re.I)
# Only complete operation-control clauses can be ignored. In particular, a
# prefix such as "please correct" cannot consume a following factual value.
_CORRECTION_CONTROL = re.compile(
    r"(?:请)?(?:调用|使用)记忆纠正工具(?:更新|纠正)(?:已有|现有)(?:条目|记忆)(?:后再回答)?"
    r"|(?:请)?(?:更新|纠正)(?:已有|现有)(?:条目|记忆)(?:后再回答)?"
    r"|(?:不能|不要)只(?:承诺|说)(?:稍后|以后)(?:整理|更新|纠正)"
)


def _completed_correction_scope(db: sqlite3.Connection, subject_id: str, evidence_id: str,
                                text: str) -> tuple[bool, set[tuple[int, int]]]:
    from ..hermes.batch_adapter import _stated_normalize, _stated_normalize_no_subject_prepend

    receipts = db.execute(
        "SELECT c.target_id,r.affected_ids_json FROM trust_command c "
        "JOIN trust_command_receipt r ON r.command_id=c.command_id "
        "WHERE c.subject_id=? AND c.operation='correct_world_item' AND c.target_kind='cognition' "
        "AND r.result_state='applied' AND EXISTS "
        "(SELECT 1 FROM json_each(r.affected_ids_json) WHERE value=?)", (subject_id, evidence_id)).fetchall()
    spans: set[tuple[int, int]] = set()
    for receipt in receipts:
        affected = json.loads(receipt['affected_ids_json'])
        rows = db.execute(
            "SELECT c.id,c.content,l.payload_json,te.kind AS target_kind FROM cognition c "
            "JOIN cognition_transitions t ON t.replacement_cognition_id=c.id AND t.prior_cognition_id=? "
            "JOIN cognition_evidence ce ON ce.cognition_id=c.id AND ce.evidence_id=? AND ce.relation='support' "
            "JOIN evidence_ledger l ON json_extract(l.content,'$.cognition_id')=c.id "
            "AND json_extract(l.content,'$.evidence_id')=ce.evidence_id AND json_extract(l.content,'$.relation')='support' "
            "LEFT JOIN cognition_target ct ON ct.cognition_id=c.id LEFT JOIN entity te ON te.id=ct.target_entity_id "
            "LEFT JOIN world_item_lifecycle w ON w.subject_id=c.subject_id AND w.object_kind='cognition' AND w.item_id=c.id "
            "WHERE c.subject_id=? AND c.invalid_at IS NULL AND c.archived_at IS NULL AND c.muted_at IS NULL "
            "AND w.archived_at IS NULL AND w.muted_at IS NULL",
            (receipt['target_id'], evidence_id, subject_id)).fetchall()
        for row in rows:
            if row['id'] not in affected:
                continue
            support = json.loads(row['payload_json'])
            start, end = support.get('start'), support.get('end')
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                    or support.get('span_hash') != sha256(text[start:end].encode()).hexdigest()):
                continue
            normalize = _stated_normalize if row['target_kind'] in (None, 'topic') else _stated_normalize_no_subject_prepend
            if row['content'] == normalize(text[start:end]):
                spans.add((start, end))
    return bool(receipts), spans


def _unfulfilled_correction_text(text: str, spans: set[tuple[int, int]]) -> str:
    from ..hermes.batch_adapter import _evidence_segments

    remaining = ''.join(char if not any(start <= i < end for start, end in spans) else ' '
                        for i, char in enumerate(text))
    return ''.join(segment['text'] for segment in _evidence_segments(remaining)
                   if not _CORRECTION_CONTROL.fullmatch(segment['text'].strip().rstrip('，。！？；,!?;')))


def formation_requests(db: sqlite3.Connection, subject_id: str, *, model_tier: ModelTier = 'local') -> list[dict[str, Any]]:
    # No recency cutoff: a failed correction stays visible after restart and
    # after the provisional quote window expires. Reprocessing reuses Evidence.
    jobs = db.execute('SELECT * FROM memory_world_job WHERE subject_id=? ORDER BY created_at DESC, rowid DESC', (subject_id,)).fetchall()
    seen: set[str] = set()
    requests = []
    for job in jobs:
        for eid in json.loads(job['evidence_ids_json']):
            if eid in seen:
                continue
            seen.add(eid)
            if job['state'] not in ('pending', 'processing', 'retry', 'no_change', 'dead'):
                continue
            boundary = source_boundary_event(db, subject_id, job['boundary_event_id'])
            if not _model_eligible(db, subject_id, boundary, model_tier):
                continue
            evidence = db.execute('SELECT * FROM evidence WHERE id=? AND subject_id=?', (eid, subject_id)).fetchone()
            if evidence is None or evidence_state(dict(evidence), surface='formation', model_tier=model_tier) is not None:
                continue
            text = evidence['raw_content']
            intent = 'correction' if _CORRECTION.search(text) else 'remember' if _REMEMBER.search(text) else None
            if intent is None:
                continue
            result = json.loads(job['world_result_json'] or '{}')
            # A replay that changed nothing because its successor already exists
            # has fulfilled the request. Compiler/model rejection has not.
            if job['state'] == 'no_change':
                source_correction, spans = _completed_correction_scope(db, subject_id, eid, text)
                if source_correction:
                    remaining = _unfulfilled_correction_text(text, spans) if spans else text
                    if not remaining.strip():
                        continue
                    # Unknown uncovered facts stay unfinished, including a
                    # remember request alongside an already applied correction.
                    intent = 'correction' if _CORRECTION.search(remaining) else 'remember'
                elif result.get('reason') == 'no_world_mutation':
                    continue
            requests.append(dict(job_id=job['job_id'], evidence_id=eid, text=text,
                                 session_id=job['parent_session_id'], created_at=job['created_at'],
                                 intent=intent, state=job['state'], reason=result.get('reason')))
    return requests


def correction_notices(db: sqlite3.Connection, subject_id: str, query: str, *, model_tier: ModelTier,
                       selected_item_ids: tuple[tuple[str, str], ...] = ()) -> list[dict[str, Any]]:
    notices = []
    for item in formation_requests(db, subject_id, model_tier=model_tier):
        if item['intent'] != 'correction':
            continue
        candidates = []
        for kind, item_id in selected_item_ids:
            if kind == 'cognition':
                prior = db.execute('SELECT content,created_at FROM cognition WHERE id=? AND subject_id=?',
                                   (item_id, subject_id)).fetchone()
                if prior is not None and prior[1] <= item['created_at']:
                    candidates.append((item_id, str(prior[0])))
        target_ids: set[str] = set()
        row = db.execute('SELECT model_result_json FROM memory_world_job WHERE job_id=? AND subject_id=?',
                         (item['job_id'], subject_id)).fetchone()
        checkpoint = json.loads(row[0] or '{}') if row else {}
        for result in (checkpoint, checkpoint.get('formation_rewrite', {}).get('first_result', {})):
            try:
                interpretation = json.loads(result.get('content') or '{}')
                target_ids.update(c['corrects_cognition_id'] for c in interpretation.get('cognitions', [])
                                  if isinstance(c, dict) and isinstance(c.get('corrects_cognition_id'), str))
            except (ValueError, TypeError, AttributeError):
                continue
        related = _related(query, item['text']) or any(
            item_id in target_ids or _related(text, item['text']) for item_id, text in candidates)
        if related and (not selected_item_ids or candidates):
            notices.append(item)
    return notices
