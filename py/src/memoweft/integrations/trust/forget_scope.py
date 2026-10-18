"""Bind a forget confirmation to the subject's read-only context snapshot."""
from __future__ import annotations

from hashlib import sha256
import json
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .command_store import CommandMutation, Mutation
    from .model import CommandEnvelopeV1


def context_snapshot(db: sqlite3.Connection, subject_id: str) -> dict[str, dict[str, list[object]]]:
    rows: dict[str, dict[str, list[object]]] = {}
    for table in ('interaction_context', 'interaction_commitment'):
        if db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
            rows[table] = {str(row[0]): list(row) for row in db.execute(
                f'SELECT * FROM {table} WHERE subject_id=? ORDER BY id', (subject_id,))}
    return rows


def affected_scope(before: dict[str, dict[str, list[object]]],
                   after: dict[str, dict[str, list[object]]]) -> tuple[str, bool]:
    # Bind only the rows this exact cascade changes, including their content and
    # dependency metadata. An unrelated new conversation must not invalidate it.
    changes = [[table, item_id, before.get(table, {}).get(item_id), after.get(table, {}).get(item_id)]
               for table in sorted(before.keys() | after.keys())
               for item_id in sorted(before.get(table, {}).keys() | after.get(table, {}).keys())
               if before.get(table, {}).get(item_id) != after.get(table, {}).get(item_id)]
    digest = sha256(json.dumps(changes, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
    return digest, bool(changes)


def scope_token(subject_id: str, revision: int, kind: str, item_id: str, snapshot: str) -> str:
    value = ['forget-scope-v1', subject_id, revision, kind, item_id, snapshot]
    return 'forget-scope-v1:' + sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


def guarded_forget(db: sqlite3.Connection, command: CommandEnvelopeV1,
                   completed_at: str, mutate: Mutation) -> CommandMutation:
    from .command_store import CommandMutation
    before = context_snapshot(db, command['subject_id'])
    supplied = command['payload'].get('forget_scope')
    # The caller holds BEGIN IMMEDIATE. Tentative writes stay inside this
    # savepoint until the actual changed-ID/content set matches the preview.
    db.execute('SAVEPOINT forget_scope_guard')
    try:
        mutation = mutate(db, command, completed_at)
        digest, changed = affected_scope(before, context_snapshot(db, command['subject_id']))
        expected = scope_token(command['subject_id'], command['expected_world_revision'],
                               command['target_kind'], command['target_id'], digest)
        reason = ('forget_scope_required' if supplied is None and changed else
                  'forget_scope_changed' if supplied is not None and supplied != expected else None)
        if reason:
            db.execute('ROLLBACK TO forget_scope_guard')
            mutation = CommandMutation('rejected', rejection_code=reason)
        db.execute('RELEASE forget_scope_guard')
        return mutation
    except BaseException:
        db.execute('ROLLBACK TO forget_scope_guard')
        db.execute('RELEASE forget_scope_guard')
        raise
