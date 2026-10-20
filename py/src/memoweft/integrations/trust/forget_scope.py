"""Bind a forget confirmation to the subject's read-only context snapshot."""
from __future__ import annotations

from hashlib import sha256
from dataclasses import replace
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
    # Evidence can acquire an assistant-context copy during boundary acceptance
    # without an interaction row or a World revision change. Snapshot only that
    # field, not the independent user's retained original Evidence.
    rows['evidence_context'] = {str(row[0]): list(row) for row in db.execute(
        'SELECT id,preceding_ai_context FROM evidence WHERE subject_id=? '
        'AND preceding_ai_context IS NOT NULL ORDER BY id', (subject_id,))}
    return rows


def cleared_evidence_contexts(before: dict[str, dict[str, list[object]]],
                              after: dict[str, dict[str, list[object]]]) -> dict[str, str]:
    return {item_id: str(row[1]) for item_id, row in before.get('evidence_context', {}).items()
            if item_id not in after.get('evidence_context', {})}


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
    from .forget_targets import scope_target
    before = context_snapshot(db, command['subject_id'])
    supplied = command['payload'].get('forget_scope')
    # The caller holds BEGIN IMMEDIATE. Tentative writes stay inside this
    # savepoint until the actual changed-ID/content set matches the preview.
    db.execute('SAVEPOINT forget_scope_guard')
    try:
        mutation = mutate(db, command, completed_at)
        if mutation.result_state == 'rejected' and mutation.rejection_code == 'formation_in_progress':
            # No cascade ran. Preserve the readiness reason rather than
            # comparing an empty mutation with the old, nonempty preview.
            db.execute('ROLLBACK TO forget_scope_guard')
            db.execute('RELEASE forget_scope_guard')
            return mutation
        after = context_snapshot(db, command['subject_id'])
        digest, changed = affected_scope(before, after)
        kind, item_id = scope_target(command['target_kind'], command['target_id'], command['payload'].get('additional_targets'))
        expected = scope_token(command['subject_id'], command['expected_world_revision'], kind, item_id, digest)
        reason = ('forget_scope_required' if supplied is None and (changed or 'additional_targets' in command['payload']) else
                  'forget_scope_changed' if supplied is not None and supplied != expected else None)
        if reason:
            db.execute('ROLLBACK TO forget_scope_guard')
            mutation = CommandMutation('rejected', rejection_code=reason)
        elif mutation.result_state == 'applied':
            # Report only after validation, without treating these Evidence IDs
            # as erased sources in the provenance/dependency cascade itself.
            mutation = replace(mutation, affected_ids=tuple(sorted(
                set(mutation.affected_ids) | cleared_evidence_contexts(before, after).keys())))
        db.execute('RELEASE forget_scope_guard')
        return mutation
    except BaseException:
        db.execute('ROLLBACK TO forget_scope_guard')
        db.execute('RELEASE forget_scope_guard')
        raise
