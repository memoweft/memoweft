"""Project unresolved explicit memory requests from the durable source jobs."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ...types import ModelTier
from ..trust.currentness import evidence_state
from .interactions import _model_eligible
from .recent import _CORRECTION, _related
from .reprocess import source_boundary_event

_REMEMBER = re.compile(r"记住|记着|记下来|记下|remember|keep in mind", re.I)


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
            if job['state'] == 'no_change' and result.get('reason') == 'no_world_mutation':
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
