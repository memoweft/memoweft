"""Bounded targets for one preview and one existing Trust command transaction."""
from __future__ import annotations

import json
import sqlite3
from typing import cast

from .command_store import CommandMutation, TrustCommandError
from .model import CommandEnvelopeV1


def forget_targets(kind: str, item_id: str, additional: object = None) -> list[tuple[str, str]]:
    targets = [(kind, item_id)]
    if additional is not None:
        if not isinstance(additional, list) or not 1 <= len(additional) <= 31:
            raise TrustCommandError('invalid_forget_targets')
        for item in additional:
            if not isinstance(item, dict) or set(item) != {'target_kind', 'target_id'}:
                raise TrustCommandError('invalid_forget_targets')
            targets.append((item['target_kind'], item['target_id']))
    for target_kind, target_id in targets:
        if (not isinstance(target_kind, str) or target_kind not in {'cognition', 'entity', 'relationship', 'event', 'evidence'}
                or not isinstance(target_id, str) or not target_id or target_id != target_id.strip() or len(target_id) > 512):
            raise TrustCommandError('invalid_forget_targets')
    if len(set(targets)) != len(targets):
        raise TrustCommandError('invalid_forget_targets')
    return targets


def scope_target(kind: str, item_id: str, additional: object = None) -> tuple[str, str]:
    if additional is None:
        return kind, item_id
    return 'batch', json.dumps(forget_targets(kind, item_id, additional), separators=(',', ':'))


def delete_targets(db: sqlite3.Connection, command: CommandEnvelopeV1, completed_at: str) -> CommandMutation:
    from .true_delete import delete_evidence, delete_world_item
    if command['payload'].get('additional_targets') is None:
        raise TrustCommandError('invalid_forget_targets')
    targets = forget_targets(command['target_kind'], command['target_id'], command['payload'].get('additional_targets'))
    payload = {k: v for k, v in command['payload'].items() if k != 'additional_targets'}
    affected: set[str] = set()
    applied = False
    tables = {'cognition': ('cognition', 'subject_id'), 'entity': ('entity', 'world_id'),
              'relationship': ('relationship', 'world_id'), 'event': ('world_event', 'world_id'),
              'evidence': ('evidence', 'subject_id')}
    def exists(kind: str, item_id: str) -> bool:
        table, owner = tables[kind]
        return db.execute(f'SELECT 1 FROM {table} WHERE id=? AND {owner}=?',
                          (item_id, command['subject_id'])).fetchone() is not None
    if any(not exists(kind, item_id) for kind, item_id in targets):
        return CommandMutation('rejected', rejection_code='world_item_not_found')
    # A later target may reject. Roll back earlier targets too, including all
    # markers, dependencies and source bodies; only the outer command commits.
    db.execute('SAVEPOINT delete_confirmed_targets')
    try:
        for kind, item_id in targets:
            # An earlier cascade may already have removed an explicitly named
            # target. Only this transaction's actual affected set can prove it.
            if item_id in affected and not exists(kind, item_id):
                continue
            child = cast(CommandEnvelopeV1, {**command, 'target_kind': kind, 'target_id': item_id, 'payload': payload})
            result = (delete_evidence if kind == 'evidence' else delete_world_item)(
                db, child, completed_at, command['subject_id'])
            if result.result_state == 'rejected':
                db.execute('ROLLBACK TO delete_confirmed_targets')
                db.execute('RELEASE delete_confirmed_targets')
                return result
            applied = applied or result.result_state == 'applied'
            affected.update(result.affected_ids)
        db.execute('RELEASE delete_confirmed_targets')
        return CommandMutation('applied' if applied else 'no_change', tuple(sorted(affected)))
    except BaseException:
        db.execute('ROLLBACK TO delete_confirmed_targets')
        db.execute('RELEASE delete_confirmed_targets')
        raise
