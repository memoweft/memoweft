"""Read-only date projection and the WeftMate #474 English wire contract.

No schema, source text, formation, matching, or ranking is changed here.
Legacy Chinese rendering remains in recall.format_recall.
"""
from __future__ import annotations

from datetime import datetime
import sqlite3
from typing import Mapping, Sequence

from ...types import ModelTier
from ..trust.correction_source import pending_correction_source_metadata
from ..trust.currentness import evidence_state


_RESERVED = ('Memory:', 'Memory (past):', '记忆：', '记忆（过往）：', '\\')


def _escape_body(body: str) -> str:
    # Split on LF only: CRLF inside a body and a trailing CR are source bytes.
    return '\n'.join('\\' + line if line.startswith(_RESERVED) else line for line in body.split('\n'))


def render_english_entry(item: Mapping[str, object]) -> str:
    past = item.get('is_superseded') or int(str(item.get('confidence', 600))) < 300
    header = 'Memory (past):' if past else 'Memory:'
    return header + _escape_body(str(item.get('recall_date', '')) + str(item['content']))


def render_english_entries(
    items: Sequence[dict[str, object]], *, max_items: int, max_chars: int,
) -> tuple[list[dict[str, object]], str]:
    selected: list[dict[str, object]] = []
    lines: list[str] = []
    size = 0
    for item in items:
        if len(selected) >= max_items:
            break
        line = render_english_entry(item)
        cost = len(line) + (1 if lines else 0)
        if size + cost > max_chars:
            break
        selected.append(item)
        lines.append(line)
        size += cost
    return selected, '\n'.join(lines)


def _date(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    # Validate without normalizing precision, inventing a timezone or a day.
    return value


def recall_date(db: sqlite3.Connection, subject_id: str, item: Mapping[str, object], model_tier: ModelTier) -> str:
    kind, item_id = str(item['kind']), str(item['id'])
    labels: list[str] = []
    if kind == 'event':
        row = db.execute('SELECT occurred_at,time_expression FROM world_event WHERE id=? AND world_id=?',
                         (item_id, subject_id)).fetchone()
        if row is not None:
            if occurred := _date(row[0]):
                labels.append(f'event at: {occurred}')
            if isinstance(row[1], str) and row[1].strip():
                labels.append(f'event time: {row[1]}')
    elif kind in {'cognition', 'relationship'}:
        table, column = ('cognition_evidence', 'cognition_id') if kind == 'cognition' else ('relationship_evidence', 'relationship_id')
        rows = db.execute(
            f'SELECT e.id,e.occurred_at,e.recorded_at,e.deleted_at,e.allow_local_read,e.allow_cloud_read,e.allow_inference '
            f'FROM evidence e JOIN {table} l ON e.id=l.evidence_id '
            f"WHERE l.{column}=? AND l.relation='support' AND e.subject_id=? AND e.source_kind='spoken' "
            'ORDER BY e.occurred_at,e.recorded_at,e.id', (item_id, subject_id),
        )
        columns = [column[0] for column in rows.description]
        for raw in rows.fetchall():
            source = dict(zip(columns, raw))
            if evidence_state(source, surface='model_cloud' if model_tier == 'cloud' else 'recall', model_tier=model_tier) is not None:
                continue
            occurred, recorded = _date(source['occurred_at']), _date(source['recorded_at'])
            # The existing store fills missing occurred_at with recorded_at.
            # Without proof of a distinct source time, label it recording time;
            # equality is deliberately conservative, never a spoken-time claim.
            pending = pending_correction_source_metadata(db, str(source['id']))
            if occurred and occurred != recorded and 'timestamp' not in pending:
                labels.append(f'said at: {occurred}')
            elif recorded:
                labels.append(f'recorded at: {recorded}')
    return '[' + '; '.join(dict.fromkeys(labels)) + '] ' if labels else ''
