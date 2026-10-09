from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.integrations.hermes.batch_adapter import _confirm_normalize
from memoweft.integrations.trust.query_service import QueryService
from test_formation_accuracy import _item, _run
from test_hermes_batch_adapter_v5 import _batch, _model, _run as run_job, _set_evidence
from test_hermes_world_worker import MutableClock, _job


def seed(path: Path, *, broad_relationship: bool = False) -> dict[str, str]:
    raw = '林遥修乐器很熟练，是我的朋友。'
    evaluation = _item('ignored')
    evaluation.update(statement_kind='attribute', entity={'canonical_name': '林遥', 'kind': 'person'})
    relationship = _item('ignored')
    relationship.update(statement_kind='relationship', relation_type='friend',
                        target_entity={'canonical_name': '林遥', 'kind': 'person'},
                        supports=[{'evidence_id': 'evidence-1', 'segment_id': 's1'}])
    if broad_relationship:
        relationship['supports'] = [{'evidence_id': 'evidence-1', 'sentence_id': 't0'}]
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
        sources = cast(list[dict[str, Any]], query.get_world_item_provenance(kind, item_id)['provenance'])  # type: ignore[arg-type]
        assert sources and all(s['evidence']['raw_content'] == '林遥修乐器很熟练，是我的朋友。' for s in sources)


@pytest.mark.parametrize('kind,raw,extra', [
    ('relationship', '更正：林遥是我的老师，不是朋友。',
     {'statement_kind': 'relationship', 'relation_type': 'teacher', 'target_entity': {'canonical_name': '林遥', 'kind': 'person'}}),
    ('cognition', '更正：林遥修乐器其实是初学水平。',
     {'statement_kind': 'attribute', 'entity': {'canonical_name': '林遥', 'kind': 'person'}}),
    ('entity', '更正：林遥的名字我说错了，其实叫林澜。',
     {'statement_kind': 'alias', 'entity': {'canonical_name': '林澜', 'kind': 'person'},
      'alias_of': {'canonical_name': '林遥', 'kind': 'person'}}),
    ('entity', '那个人名写错了，应该是林澜，不是林遥，以后按林澜这个名字来。',
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
    source = cast(dict[str, Any], query.get_world_item_provenance(kind, ids[kind]))  # type: ignore[arg-type]
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


def test_name_correction_rewrite_explains_alias_target_without_guessing_fields(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    ids = seed(path)
    raw = '那个人名写错了，应该是林澜，不是林遥。'
    wrong = dict(_item('ignored'), action='correct', statement_kind='naming',
                 entity={'canonical_name': '林澜', 'kind': 'person'}, corrects_cognition_id=ids['cognition'],
                 supports=[{'evidence_id': 'evidence-2', 'sentence_id': 't0'}])
    corrected = {**wrong, 'statement_kind': 'alias', 'alias_of': {'canonical_name': '林遥', 'kind': 'person'}}
    del corrected['corrects_cognition_id']
    run_job(path, MutableClock(), [_model(_batch(wrong)), _model(_batch(corrected))], ('evidence-2',),
            lambda p: _set_evidence(p, 'evidence-2', raw), job_id='job-2')
    row = dict(_job(path, 'job-2'))
    assert row['state'] == 'applied'
    rewrite = json.loads(str(row['model_result_json']))['formation_rewrite']
    assert rewrite['error']['code'] == 'invalid_cognition_action'
    assert 'omit ALL corrects_* ID fields' in rewrite['error']['instruction']
    assert rewrite['first_result']['content'] == _model(_batch(wrong))['content']
    assert rewrite['final_error'] is None


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
    proof = cast(dict[str, Any], QueryService(path, subject_id='owner').get_world_item_provenance('cognition', decision))
    assert proof['provenance'][0]['evidence']['raw_content'] == raw


@pytest.mark.parametrize('query', ['修乐器可以找谁？', '修乐器应该找谁？', '修乐器怎么安排？'])
def test_confirmed_decision_topic_with_a_modal_question_is_retrievable(tmp_path: Path, query: str) -> None:
    path = tmp_path / 'world.sqlite3'
    raw = '以后需要修乐器时，就提醒我找林遥。'
    item = _item('ignored')
    item.update(entity={'canonical_name': '修乐器', 'kind': 'topic'},
                supports=[{'evidence_id': 'evidence-1', 'sentence_id': 't0'}])
    row, _ = _run(path, raw, [_model(_batch(item))])
    assert row['state'] == 'applied'
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', query)
        assert snapshot and snapshot.count == 1 and '林遥' in snapshot.rendered_recall


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


@pytest.mark.parametrize('raw,query,topic', [
    ('好，以后我想修乐器时，就提醒我找林遥。', '修乐器可以找谁？', '修乐器'),
    ('今后需要校对文稿的时候，请提醒我找林遥。', '校对文稿应该找谁？', '校对文稿'),
    ('以后我们想校对文稿时，就提醒我们找林遥。', '校对文稿应该找谁？', '校对文稿'),
])
def test_explicit_conditional_decision_retains_topic_when_model_omits_it(
    tmp_path: Path, raw: str, query: str, topic: str,
) -> None:
    path = tmp_path / 'world.sqlite3'
    item = _item('ignored')
    item['supports'] = [{'evidence_id': 'evidence-1', 'sentence_id': 't0'}]
    row, _ = _run(path, raw, [_model(_batch(item))])
    assert row['state'] == 'applied'
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT canonical_name FROM entity WHERE kind='topic'").fetchone()[0] == topic
        snapshot = recall_world_snapshot(db, 'owner', query)
        assert snapshot and snapshot.count == 1 and '林遥' in snapshot.rendered_recall


def test_short_assent_confirms_a_situational_decision_without_inventing_evidence(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    claim = '以后你想修乐器时，我提醒你找林遥？'
    raw = '好'
    item = dict(_item(_confirm_normalize(claim)), formed_by='confirmed', assistant_claim=claim)
    item['supports'] = [{'evidence_id': 'evidence-1', 'start': 0, 'end': len(raw)}]
    def setup(p: Path) -> None:
        _set_evidence(p, 'evidence-1', raw)
        with sqlite3.connect(p) as db:
            db.execute("UPDATE evidence SET preceding_ai_context=? WHERE id='evidence-1'", (claim,))
    run_job(path, MutableClock(), [_model(_batch(item))], ('evidence-1',), setup)
    assert _job(path)['state'] == 'applied'
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', '修乐器可以找谁？')
        assert snapshot and snapshot.count == 1 and '林遥' in snapshot.rendered_recall
        assert db.execute("SELECT raw_content FROM evidence WHERE id='evidence-1'").fetchone()[0] == raw


@pytest.mark.parametrize('field', ['muted_at', 'archived_at'])
def test_replacement_explanation_respects_hidden_predecessors(tmp_path: Path, field: str) -> None:
    path = tmp_path / 'world.sqlite3'
    ids = seed(path)
    item = dict(_item('ignored'), action='correct', statement_kind='attribute',
                entity={'canonical_name': '林遥', 'kind': 'person'}, corrects_cognition_id=ids['cognition'])
    assert correct(path, '更正：林遥修乐器其实是初学水平。', item)['state'] == 'applied'
    with sqlite3.connect(path) as db:
        db.execute(f'INSERT INTO world_item_lifecycle(subject_id,object_kind,item_id,{field},updated_at) VALUES(?,?,?,?,?)',
                   ('owner', 'cognition', ids['cognition'], '2026-10-09', '2026-10-09'))
        snapshot = recall_world_snapshot(db, 'owner', '林遥修乐器，为什么以前的说法不算了？')
        assert snapshot and '初学水平' in snapshot.rendered_recall
        assert '很熟练' not in snapshot.rendered_recall


def test_name_corrected_relationship_is_retrievable_by_its_former_identity(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    item = dict(_item('ignored'), statement_kind='relationship', relation_type='friend',
                target_entity={'canonical_name': '林遥', 'kind': 'person'},
                supports=[{'evidence_id': 'evidence-1', 'sentence_id': 't0'}])
    row, _ = _run(path, '林遥是我的朋友。', [_model(_batch(item))])
    assert row['state'] == 'applied'
    correction = dict(_item('ignored'), action='correct', statement_kind='alias',
                      entity={'canonical_name': '林澜', 'kind': 'person'},
                      alias_of={'canonical_name': '林遥', 'kind': 'person'})
    assert correct(path, '更正：林遥的名字我说错了，其实叫林澜。', correction)['state'] == 'applied'
    with sqlite3.connect(path) as db:
        for tier in ('local', 'cloud'):
            snapshot = recall_world_snapshot(db, 'owner', '林遥是谁？', model_tier=tier)
            assert snapshot and snapshot.count == 1 and '林澜' in snapshot.rendered_recall


def test_broad_relationship_cannot_keep_an_obsolete_evaluation_current(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    ids = seed(path, broad_relationship=True)
    item = dict(_item('ignored'), action='correct', statement_kind='attribute',
                entity={'canonical_name': '林遥', 'kind': 'person'}, corrects_cognition_id=ids['cognition'])
    assert correct(path, '更正：林遥修乐器其实是初学水平。', item)['state'] == 'applied'
    with sqlite3.connect(path) as db:
        relationship = db.execute('SELECT content FROM relationship WHERE invalid_at IS NULL').fetchone()[0]
        assert relationship == '林遥是我的朋友。'
        snapshot = recall_world_snapshot(db, 'owner', '林遥修乐器现在怎么样？')
        assert snapshot and '初学水平' in snapshot.rendered_recall and '很熟练' not in snapshot.rendered_recall
