from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.integrations.trust.query_service import QueryService
from test_formation_accuracy import _item, _run
from test_hermes_batch_adapter_v5 import _batch, _model, _run as run_job, _set_evidence
from test_hermes_world_worker import MutableClock, _job


def seed(path: Path) -> dict[str, str]:
    raw = '林遥修乐器很熟练，是我的朋友。'
    evaluation = _item('ignored')
    evaluation.update(statement_kind='attribute', entity={'canonical_name': '林遥', 'kind': 'person'})
    relationship = _item('ignored')
    relationship.update(statement_kind='relationship', relation_type='friend',
                        target_entity={'canonical_name': '林遥', 'kind': 'person'},
                        supports=[{'evidence_id': 'evidence-1', 'segment_id': 's1'}])
    row, _ = _run(path, raw, [_model(_batch(evaluation, relationship))])
    assert row['state'] == 'applied', row
    with sqlite3.connect(path) as db:
        return {kind: str(db.execute(f'SELECT id FROM {kind}' + (" WHERE canonical_name='林遥'" if kind == 'entity' else '')).fetchone()[0])
                for kind in ('entity', 'relationship', 'cognition')}


def correct(path: Path, raw: str, item: dict[str, Any]) -> dict[str, Any]:
    item['supports'] = [{'evidence_id': 'evidence-2', 'sentence_id': 't0'}]
    run_job(path, MutableClock(), [_model(_batch(item)), _model(_batch(item))], ('evidence-2',),
            lambda p: _set_evidence(p, 'evidence-2', raw), job_id='job-2')
    return dict(_job(path, 'job-2'))


def test_independent_relationship_evaluation_and_entity_sources(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    ids = seed(path)
    query = QueryService(path, subject_id='owner')
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT content FROM cognition').fetchone()[0] == '林遥修乐器很熟练，'
        assert db.execute('SELECT content FROM relationship').fetchone()[0] == '林遥是我的朋友。'
    for kind, item_id in ids.items():
        sources = query.get_world_item_provenance(kind, item_id)['provenance']  # type: ignore[arg-type]
        assert sources and all(s['evidence']['raw_content'] == '林遥修乐器很熟练，是我的朋友。' for s in sources)


@pytest.mark.parametrize('kind,raw,extra', [
    ('relationship', '更正：林遥是我的老师，不是朋友。',
     {'statement_kind': 'relationship', 'relation_type': 'teacher', 'target_entity': {'canonical_name': '林遥', 'kind': 'person'}}),
    ('cognition', '更正：林遥修乐器其实是初学水平。',
     {'statement_kind': 'attribute', 'entity': {'canonical_name': '林遥', 'kind': 'person'}}),
    ('entity', '更正：林遥的名字我说错了，其实叫林澜。',
     {'statement_kind': 'alias', 'entity': {'canonical_name': '林澜', 'kind': 'person'},
      'alias_of': {'canonical_name': '林遥', 'kind': 'person'}}),
])
def test_three_corrections_keep_sources_and_formal_reason_chains(
    tmp_path: Path, kind: str, raw: str, extra: dict[str, Any],
) -> None:
    path = tmp_path / 'world.sqlite3'
    ids = seed(path)
    item = dict(_item('ignored'), action='correct', **extra)
    if kind != 'entity':
        item[f'corrects_{kind}_id'] = ids[kind]
    row = correct(path, raw, item)
    assert row['state'] == 'applied', row
    query = QueryService(path, subject_id='owner')
    source = query.get_world_item_provenance(kind, ids[kind])  # type: ignore[arg-type]
    assert source['transition_history']
    assert all(s['currentness_state'] == 'not_current' for s in source['provenance'])
    assert source['provenance'][0]['evidence']['raw_content'] == '林遥修乐器很熟练，是我的朋友。'
    with sqlite3.connect(path) as db:
        assert db.execute(f'SELECT invalid_at FROM {kind} WHERE id=?', (ids[kind],)).fetchone()[0]
        snapshot = recall_world_snapshot(db, 'owner', '林澜修乐器怎样？' if kind == 'entity' else '林遥是什么关系，为什么以前的说法不算了？')
        assert snapshot and snapshot.count
        assert '取代原因' in snapshot.rendered_recall if kind != 'entity' else '林澜' in snapshot.rendered_recall
        if kind == 'entity':
            assert not db.execute("SELECT content FROM cognition WHERE invalid_at IS NULL AND content LIKE '%林遥%'").fetchall()
            assert not db.execute("SELECT content FROM relationship WHERE invalid_at IS NULL AND content LIKE '%林遥%'").fetchall()


def test_ordinary_alias_cannot_be_used_as_a_name_correction(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    ids = seed(path)
    item = dict(_item('ignored'), action='correct', statement_kind='alias',
                entity={'canonical_name': '林澜', 'kind': 'person'}, alias_of={'canonical_name': '林遥', 'kind': 'person'})
    assert correct(path, '林遥也被叫做林澜。', item)['state'] == 'no_change'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT invalid_at FROM entity WHERE id=?', (ids['entity'],)).fetchone()[0] is None


def test_confirmation_is_an_independent_decision_with_its_own_exact_source(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    seed(path)
    raw = '好，以后需要修乐器时，就提醒我找林遥。'
    item = _item('ignored')
    item['supports'] = [{'evidence_id': 'evidence-2', 'sentence_id': 't0'}]
    run_job(path, MutableClock(), [_model(_batch(item))], ('evidence-2',),
            lambda p: _set_evidence(p, 'evidence-2', raw), job_id='job-2')
    with sqlite3.connect(path) as db:
        decision = db.execute("SELECT id FROM cognition WHERE content_type='preference'").fetchone()[0]
    proof = QueryService(path, subject_id='owner').get_world_item_provenance('cognition', decision)
    assert proof['provenance'][0]['evidence']['raw_content'] == raw


def test_replacement_explanation_cannot_read_revoked_predecessor(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    ids = seed(path)
    item = dict(_item('ignored'), action='correct', statement_kind='attribute',
                entity={'canonical_name': '林遥', 'kind': 'person'}, corrects_cognition_id=ids['cognition'])
    assert correct(path, '更正：林遥修乐器其实是初学水平。', item)['state'] == 'applied'
    with sqlite3.connect(path) as db:
        db.execute("UPDATE evidence SET allow_cloud_read=0 WHERE id='evidence-1'")
        snapshot = recall_world_snapshot(db, 'owner', '林遥修乐器，为什么旧说法不算了？', model_tier='cloud')
        assert snapshot and '初学水平' in snapshot.rendered_recall
        assert '很熟练' not in snapshot.rendered_recall
