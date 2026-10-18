"""Validate temporal successors without treating yesterday's truth as an error.

Uses the existing transition tables and permissions. No inferred transitions:
the interpreter must explicitly select a typed predecessor from its payload.
"""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Literal

from ...types import ModelTier
from ..trust.currentness import world_item_visible

if TYPE_CHECKING:
    from .batch_adapter import BatchItem


def is_superseded(db: sqlite3.Connection, kind: Literal['cognition', 'relationship'], item_id: str) -> bool:
    table = kind + '_transitions'
    if not db.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone():
        return False
    return db.execute(f'SELECT 1 FROM {table} WHERE prior_{kind}_id=?', (item_id,)).fetchone() is not None


def validate_successor(db: sqlite3.Connection, subject_id: str, item: BatchItem,
                       model_tier: ModelTier) -> str | None:
    """Run inside fenced Apply, before writing the successor or any old row."""
    cognition_id, relationship_id = item.supersedes_cognition_id, item.supersedes_relationship_id
    if cognition_id is None and relationship_id is None:
        return None
    if item.action != 'form' or item.formed_by != 'stated' or item.retract:
        return 'invalid_supersession_action'
    if cognition_id is not None and relationship_id is not None:
        return 'ambiguous_supersession_target'
    kind: Literal['cognition', 'relationship'] = 'relationship' if relationship_id else 'cognition'
    prior = str(relationship_id or cognition_id)
    if ((kind == 'relationship' and item.statement_kind != 'relationship')
            or (kind == 'cognition' and item.statement_kind not in {'attribute', 'preference'})):
        return 'invalid_supersession_kind'
    if prior == item.apply_identity(subject_id):
        return 'supersession_target_is_itself'
    table, owner_column = ('relationship', 'world_id') if kind == 'relationship' else ('cognition', 'subject_id')
    row = db.execute(f'SELECT {owner_column} FROM {table} WHERE id=?', (prior,)).fetchone()
    if row is None:
        return 'supersession_target_unknown'
    if str(row[0]) != subject_id:
        return 'supersession_target_subject_mismatch'
    if is_superseded(db, kind, prior) or not world_item_visible(
        db, subject_id, kind, prior, surface='formation', model_tier=model_tier,
    ):
        return 'supersession_target_not_current'
    # Deterministic identities may not revive a past node or merge unrelated
    # current states. Otherwise A -> B -> A could hide every member of the chain.
    if db.execute(f'SELECT 1 FROM {table} WHERE id=?', (item.apply_identity(subject_id),)).fetchone():
        return 'supersession_successor_exists'
    from .batch_adapter import entity_id_for, owner_entity_id_for
    if kind == 'relationship':
        source = entity_id_for(subject_id, item.source_canonical_name) if item.source_canonical_name else owner_entity_id_for(subject_id)
        if db.execute('SELECT source_entity_id FROM relationship WHERE id=?', (prior,)).fetchone() != (source,):
            return 'supersession_target_entity_mismatch'
    else:
        target = db.execute('SELECT t.target_entity_id,t.perspective_entity_id,e.kind FROM cognition_target t '
                            'JOIN entity e ON e.id=t.target_entity_id WHERE t.cognition_id=?', (prior,)).fetchone()
        old_target, old_holder = (target[0], target[1]) if target else (None, None)
        new_target = entity_id_for(subject_id, item.entity_canonical_name) if item.entity_canonical_name else None
        new_holder = entity_id_for(subject_id, item.perspective_holder_name) if item.perspective_holder_name else None
        # A short successor can omit a topic, retaining it through predecessor
        # context. A person or a different viewpoint can never be substituted.
        omitted_topic = target is not None and target[2] == 'topic' and new_target is None
        if old_holder != new_holder or (old_target != new_target and not omitted_topic):
            return 'supersession_target_entity_mismatch'
    return None
