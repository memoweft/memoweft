"""Validate temporal successors without treating yesterday's truth as an error.

Uses the existing transition tables and permissions. No inferred transitions:
the interpreter must explicitly select a typed predecessor from its payload.
"""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from ...types import ModelTier
from ..trust.currentness import world_item_visible

if TYPE_CHECKING:
    from .batch_adapter import BatchItem


def _ancestors(db: sqlite3.Connection, kind: Literal['cognition', 'relationship'], item_id: str) -> set[str]:
    if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (kind + '_transitions',)).fetchone():
        return set()
    return {str(row[0]) for row in db.execute(
        f'WITH RECURSIVE history(id) AS (SELECT prior_{kind}_id FROM {kind}_transitions '
        f'WHERE replacement_{kind}_id=? UNION SELECT t.prior_{kind}_id FROM {kind}_transitions t '
        f'JOIN history h ON t.replacement_{kind}_id=h.id) SELECT id FROM history', (item_id,),
    )}


def resolve_state_version(db: sqlite3.Connection, subject_id: str, item: BatchItem,
                          model_tier: ModelTier) -> BatchItem:
    """A repeated value is a new occurrence, never a cycle or copied Evidence.

    Keep the old identity contract for first occurrences. Only a value already
    present in this predecessor's history gets an occurrence ID, derived from
    the real new support references. The normal support writer owns its source.
    No rows, raw text or schema are created by this identity resolution.
    """
    if item.action != 'form' or item.statement_kind not in {'attribute', 'preference', 'relationship'}:
        return item
    kind: Literal['cognition', 'relationship'] = 'relationship' if item.statement_kind == 'relationship' else 'cognition'
    base = item.apply_identity(subject_id)
    owner_column = 'world_id' if kind == 'relationship' else 'subject_id'
    if not db.execute(f'SELECT 1 FROM {kind} WHERE id=? AND {owner_column}=?', (base, subject_id)).fetchone():
        return item
    prior = item.supersedes_relationship_id if kind == 'relationship' else item.supersedes_cognition_id
    if prior is not None:
        if base not in _ancestors(db, kind, prior):
            return item
        from .batch_adapter import _hash_text, _canonical
        occurrence = kind + '-' + _hash_text(_canonical([
            'memoweft_state_occurrence_v1', subject_id, base, prior,
            [(eid, start, end) for eid, start, end, _ in item.supports],
        ]))
        return replace(item, state_version_id=occurrence)
    # A plain restatement after A -> B -> A must support the current occurrence,
    # rather than silently attach its source to the first (historical) A.
    if not is_superseded(db, kind, base):
        return item
    matches = [str(row[0]) for row in db.execute(
        f'SELECT id FROM {kind} WHERE {owner_column}=? AND content=? AND invalid_at IS NULL',
        (subject_id, item.proposition),
    ) if base in _ancestors(db, kind, str(row[0]))
        and not is_superseded(db, kind, str(row[0]))
        and world_item_visible(db, subject_id, kind, str(row[0]), surface='formation', model_tier=model_tier)]
    return replace(item, state_version_id=matches[0]) if len(matches) == 1 else item


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
    columns = owner_column + (',content_type' if kind == 'cognition' else '')
    row = db.execute(f'SELECT {columns} FROM {table} WHERE id=?', (prior,)).fetchone()
    if row is None:
        return 'supersession_target_unknown'
    if str(row[0]) != subject_id:
        return 'supersession_target_subject_mismatch'
    if kind == 'cognition' and row[1] != item.statement_kind:
        return 'supersession_target_kind_mismatch'
    if is_superseded(db, kind, prior) or not world_item_visible(
        db, subject_id, kind, prior, surface='formation', model_tier=model_tier,
    ):
        return 'supersession_target_not_current'
    # A legitimate recurrence was resolved to a fresh occurrence above. Still
    # reject a collision with another current object or an unrelated history.
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
        if item.topic_canonical_name:
            if target is not None and target[2] == 'topic':
                new_target = entity_id_for(subject_id, item.topic_canonical_name)
            elif old_target is None:
                # New topic fields are retrieval cues, not cognition_target
                # sidecars. Validate them against the actual predecessor text
                # and its permitted context instead of treating them as omitted.
                from .recall import _predecessor_match_texts
                content = str(db.execute('SELECT content FROM cognition WHERE id=?', (prior,)).fetchone()[0])
                context = _predecessor_match_texts(db, subject_id, prior, model_tier, surface='formation')
                if not any(item.topic_canonical_name in text for text in (content, *context)):
                    return 'supersession_target_entity_mismatch'
        new_holder = entity_id_for(subject_id, item.perspective_holder_name) if item.perspective_holder_name else None
        # A short successor can omit a topic, retaining it through predecessor
        # context. A person or a different viewpoint can never be substituted.
        omitted_topic = target is not None and target[2] == 'topic' and new_target is None
        if old_holder != new_holder or (old_target != new_target and not omitted_topic):
            return 'supersession_target_entity_mismatch'
    return None
