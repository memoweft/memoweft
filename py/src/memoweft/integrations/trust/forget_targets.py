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
    # Earlier batch previews could omit a later target's independent sources.
    # Their credentials cannot authorize the corrected, larger preview.
    return 'batch-complete-sources-v2', json.dumps(forget_targets(kind, item_id, additional), separators=(',', ':'))


def delete_targets(db: sqlite3.Connection, command: CommandEnvelopeV1, completed_at: str) -> CommandMutation:
    from .true_delete import delete_evidence, _world_item_delete_scope, _formation_in_progress
    if command['payload'].get('additional_targets') is None:
        raise TrustCommandError('invalid_forget_targets')
    targets = forget_targets(command['target_kind'], command['target_id'], command['payload'].get('additional_targets'))
    payload = {k: v for k, v in command['payload'].items() if k != 'additional_targets'}
    # Freeze every explicit target's chain and complete source set before the
    # first deletion can remove another target or its provenance links.
    markers: set[tuple[str, str]] = set()
    sources: set[str] = set()
    world_sources: set[str] = set()
    for kind, item_id in targets:
        if kind == 'evidence':
            if not db.execute('SELECT 1 FROM evidence WHERE id=? AND subject_id=?',
                              (item_id, command['subject_id'])).fetchone():
                return CommandMutation('rejected', rejection_code='world_item_not_found')
            sources.add(item_id)
        else:
            scope = _world_item_delete_scope(db, command['subject_id'], kind, item_id)
            if isinstance(scope, CommandMutation):
                return scope
            chain, target_sources = scope
            markers.update((kind, linked_id) for linked_id in chain)
            sources.update(target_sources)
            world_sources.update(target_sources)
    if markers and _formation_in_progress(db, command['subject_id'], sources):
        return CommandMutation('rejected', rejection_code='formation_in_progress')
    affected = {item_id for _, item_id in markers}
    applied = False
    # A later target may reject. Roll back earlier targets too, including all
    # markers, dependencies and source bodies; only the outer command commits.
    db.execute('SAVEPOINT delete_confirmed_targets')
    try:
        db.executemany(
            'INSERT OR IGNORE INTO world_delete_marker (subject_id, object_kind, item_id, deleted_at) VALUES (?,?,?,?)',
            [(command['subject_id'], kind, item_id, completed_at) for kind, item_id in sorted(markers)])
        # Deduplicate sources, not vanished World objects. Every frozen source
        # still reaches the existing origin and cascade checks.
        for evidence_id in sorted(sources):
            child = cast(CommandEnvelopeV1, {**command, 'target_kind': 'evidence', 'target_id': evidence_id, 'payload': payload})
            result = delete_evidence(db, child, completed_at, command['subject_id'])
            if result.result_state == 'rejected' or (evidence_id in world_sources and result.result_state != 'applied'):
                db.execute('ROLLBACK TO delete_confirmed_targets')
                db.execute('RELEASE delete_confirmed_targets')
                return CommandMutation('rejected', rejection_code=result.rejection_code or 'source_already_deleted')
            applied = applied or result.result_state == 'applied'
            affected.update(result.affected_ids)
        db.execute('RELEASE delete_confirmed_targets')
        return CommandMutation('applied' if applied else 'no_change', tuple(sorted(affected)))
    except BaseException:
        db.execute('ROLLBACK TO delete_confirmed_targets')
        db.execute('RELEASE delete_confirmed_targets')
        raise
