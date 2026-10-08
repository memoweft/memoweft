"""Atomic name correction over formal entities and their current derivatives."""
from __future__ import annotations

import json
import re
import sqlite3
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from ..trust.currentness import CurrentnessSurface, world_item_visible

if TYPE_CHECKING:
    from .batch_adapter import BatchItem, HermesBatchAdapterProcessor
    from .world_worker import ClaimedWorldJob


def _id(kind: str, *values: str) -> str:
    return kind + '-' + sha256(json.dumps(values).encode()).hexdigest()[:32]


def _clone(db: sqlite3.Connection, table: str, prior: str, new: str,
           changes: dict[str, Any]) -> None:
    columns = [str(row[1]) for row in db.execute(f'PRAGMA table_info({table})')]
    values = dict(zip(columns, db.execute(f'SELECT * FROM {table} WHERE id=?', (prior,)).fetchone()))
    values.update(changes, id=new, invalid_at=None)
    db.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
               tuple(values[column] for column in columns))


def apply_name_correction(processor: HermesBatchAdapterProcessor, db: sqlite3.Connection,
                          job: ClaimedWorldJob, item: BatchItem, now: str) -> tuple[dict[str, object], bool]:
    from .batch_adapter import _ZeroWriteError, entity_id_for, relationship_id_for

    assert item.alias_of_canonical_name and item.entity_canonical_name and item.entity_kind
    old_name, new_name = item.alias_of_canonical_name, item.entity_canonical_name
    old_id = entity_id_for(job.subject_id, old_name)
    new_id = entity_id_for(job.subject_id, new_name)
    ledger_id = _id('evidence-ledger', 'name_corrected', old_id, new_id)
    surface: CurrentnessSurface = 'model_cloud' if processor.model_tier == 'cloud' else 'recall'
    ledger = db.execute('SELECT payload_json FROM evidence_ledger WHERE id=?', (ledger_id,)).fetchone()
    if ledger and json.loads(ledger[0]).get('evidence_ids') == [s[0] for s in item.supports] and world_item_visible(
        db, job.subject_id, 'entity', new_id, surface=surface
    ):
        return dict(action='correct', statement_kind='alias', prior_entity_id=old_id,
                    replacement_entity_id=new_id, reason='name_corrected'), False
    if not world_item_visible(db, job.subject_id, 'entity', old_id, surface=surface):
        raise _ZeroWriteError('name_correction_target_not_current')
    if not any(old_name in text and new_name in text for _, _, _, text in item.supports):
        raise _ZeroWriteError('name_correction_not_grounded')
    if not any(re.search(r'更正|纠正|说错|记错|名字.*(?:不是|改|错)|改名|correction|wrong name|renamed', text, re.I)
               for _, _, _, text in item.supports):
        raise _ZeroWriteError('name_correction_not_explicit')
    new_id, _ = processor._resolve_target_entity(db, job, new_name, item.entity_kind, now)
    if new_id == old_id:
        raise _ZeroWriteError('correction_identical_proposition')
    old_kind = db.execute('SELECT kind FROM entity WHERE id=?', (old_id,)).fetchone()[0]
    if old_kind != item.entity_kind:
        raise _ZeroWriteError('name_correction_kind_mismatch')
    for evidence, start, end, _ in item.supports:
        processor._write_entity_ledger(db, new_id, evidence, start, end)
    revision = processor._current_revision(db) + 1
    relationships = db.execute('SELECT id, source_entity_id, target_entity_id, relation_type, content '
                               'FROM relationship WHERE world_id=? AND invalid_at IS NULL '
                               'AND (source_entity_id=? OR target_entity_id=?)',
                               (job.subject_id, old_id, old_id)).fetchall()
    for prior, source, target, relation, content in relationships:
        if not world_item_visible(db, job.subject_id, 'relationship', prior, surface=surface):
            continue
        source = new_id if source == old_id else source
        target = new_id if target == old_id else target
        successor = relationship_id_for(job.subject_id, source, relation, target)
        if db.execute('SELECT 1 FROM relationship WHERE id=?', (successor,)).fetchone():
            raise _ZeroWriteError('name_correction_relationship_conflict')
        _clone(db, 'relationship', prior, successor, dict(source_entity_id=source,
               target_entity_id=target, content=content.replace(old_name, new_name), updated_at=now))
        db.execute('UPDATE relationship SET invalid_at=?, updated_at=? WHERE id=?', (now, now, prior))
        db.execute('INSERT INTO relationship_evidence SELECT ?, evidence_id, relation '
                   'FROM relationship_evidence WHERE relationship_id=?', (successor, prior))
        for evidence, start, end, _ in item.supports:
            db.execute('INSERT OR IGNORE INTO relationship_evidence VALUES (?, ?, ?)', (successor, evidence, 'support'))
            processor._write_relationship_ledger(db, successor, evidence, start, end)
        processor._write_relationship_correction_ledger(db, job, prior, successor)
        db.execute("UPDATE relationship_transitions SET reason='name_corrected' WHERE prior_relationship_id=?", (prior,))
    cognitions = db.execute('SELECT id, content FROM cognition WHERE subject_id=? AND invalid_at IS NULL',
                            (job.subject_id,)).fetchall()
    for prior, content in cognitions:
        if old_name not in content or not world_item_visible(db, job.subject_id, 'cognition', prior, surface=surface):
            continue
        successor = _id('cognition', prior, new_id, job.boundary_event_id)
        _clone(db, 'cognition', prior, successor, dict(content=content.replace(old_name, new_name), updated_at=now))
        db.execute('UPDATE cognition SET invalid_at=?, updated_at=? WHERE id=?', (now, now, prior))
        db.execute('INSERT INTO cognition_evidence SELECT ?, evidence_id, relation FROM cognition_evidence WHERE cognition_id=?', (successor, prior))
        db.execute('INSERT INTO cognition_target SELECT ?, CASE WHEN target_entity_id=? THEN ? ELSE target_entity_id END, '
                   'CASE WHEN perspective_entity_id=? THEN ? ELSE perspective_entity_id END FROM cognition_target WHERE cognition_id=?',
                   (successor, old_id, new_id, old_id, new_id, prior))
        for evidence, _, _, _ in item.supports:
            db.execute('INSERT OR IGNORE INTO cognition_evidence VALUES (?, ?, ?)', (successor, evidence, 'support'))
        processor._write_transition(db, prior, successor, revision)
        db.execute("UPDATE cognition_transitions SET reason='name_corrected' WHERE prior_cognition_id=?", (prior,))
    db.execute('UPDATE entity SET invalid_at=?, updated_at=? WHERE id=?', (now, now, old_id))
    db.execute('INSERT OR IGNORE INTO evidence_ledger VALUES (?, ?, ?)', (
        ledger_id,
        json.dumps(dict(relation='corrects', prior_entity_id=old_id, replacement_entity_id=new_id, reason='name_corrected')),
        json.dumps(dict(schema_version=1, evidence_ids=[s[0] for s in item.supports], revision=revision)),
    ))
    return dict(action='correct', statement_kind='alias', prior_entity_id=old_id,
                replacement_entity_id=new_id, reason='name_corrected'), True
