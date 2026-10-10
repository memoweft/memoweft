"""Replay QA4-01's actual interpretations against a synthetic World."""
import json
import sqlite3
from pathlib import Path

import pytest

from test_hermes_batch_adapter_v2 import _run, _set_evidence, _model
from test_hermes_world_worker import MutableClock, _job


@pytest.mark.parametrize('variant', ['first', 'rewrite', 'unrelated_topic', 'duplicate_target'])
def test_qa4_one_correction_replaces_preference_and_decision(tmp_path: Path, variant: str) -> None:
    db_path = tmp_path / 'memory.sqlite3'
    clock = MutableClock()
    originals = ['我喝第903种花茶时偏好加一小撮肉桂粉。',
                 '好，以后我喝第903种花茶时，就提醒我加一小撮肉桂粉。']
    ids = []
    for i, raw in enumerate(originals):
        eid = f'evidence-{i+1}'
        item = dict(action='form', target='owner_self', statement_kind='preference',
                    formed_by='stated', proposition=raw,
                    supports=[dict(evidence_id=eid, sentence_id='t0')],
                    entity=dict(canonical_name='喝第903种花茶', kind='topic', aliases=['第903种花茶']))
        _run(db_path, clock, [_model(dict(schema_version=8, result='cognitions', cognitions=[item]))],
             (eid,), lambda p: _set_evidence(p, eid, raw), job_id=f'old-{i}')
        assert _job(db_path, job_id=f'old-{i}')['state'] == 'applied'
        with sqlite3.connect(db_path) as db:
            ids.append(db.execute('SELECT id FROM cognition WHERE id NOT IN (' + ','.join('?' for _ in ids) + ')', ids).fetchone()[0])
    replay = json.loads((Path(__file__).parent / 'fixtures/fx17-correction-replay.json').read_text(encoding='utf-8'))['first' if variant == 'unrelated_topic' else 'rewrite' if variant == 'duplicate_target' else variant]
    for item, prior in zip(replay['cognitions'], ids):
        item['corrects_cognition_id'] = prior
        item['supports'][0]['evidence_id'] = 'evidence-3'
    if variant == 'unrelated_topic':
        replay['cognitions'][0]['entity']['canonical_name'] = '从未说过的咖啡主题'
    if variant == 'duplicate_target':
        replay['cognitions'][1]['corrects_cognition_id'] = ids[0]
    raw = replay['cognitions'][0]['proposition']
    _run(db_path, clock, [_model(replay), _model(replay)], ('evidence-3',),
         lambda p: _set_evidence(p, 'evidence-3', raw), job_id='correction')
    outcome = _job(db_path, job_id='correction')
    if variant in ('unrelated_topic', 'duplicate_target'):
        assert outcome['state'] == 'no_change'
        assert json.loads(outcome['world_result_json'])['reason'] == ('topic_name_not_in_span' if variant == 'unrelated_topic' else 'duplicate_cognition_in_batch')
        with sqlite3.connect(db_path) as db:
            assert db.execute('SELECT count(*) FROM cognition WHERE invalid_at IS NOT NULL').fetchone()[0] == 0
        return
    assert outcome['state'] == 'applied', outcome['world_result_json']
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT count(*) FROM cognition WHERE invalid_at IS NOT NULL').fetchone()[0] == 2
        current = db.execute('SELECT id,content FROM cognition WHERE invalid_at IS NULL').fetchall()
        assert len(current) == 1 and '柠檬' in current[0][1]
        transitions = db.execute('SELECT prior_cognition_id,replacement_cognition_id FROM cognition_transitions').fetchall()
        assert set(transitions) == {(prior, current[0][0]) for prior in ids}


def test_explicit_confirmed_restatement_keeps_user_sentence(tmp_path: Path) -> None:
    db_path = tmp_path / 'memory.sqlite3'
    raw = '好，以后我喝第903种花茶时，就提醒我加一小撮肉桂粉。'
    claim = '以后你在对话里提到喝第903种花茶时，我可以主动提醒你加一小撮肉桂粉，需要我这样提醒吗？'
    item = dict(action='form', target='owner_self', statement_kind='preference', formed_by='confirmed',
                proposition='用户以后喝第903种花茶时，就提醒用户加一小撮肉桂粉。', assistant_claim=claim,
                supports=[dict(evidence_id='evidence-1', sentence_id='t0')])
    _run(db_path, MutableClock(), [_model(dict(schema_version=8,result='cognitions',cognitions=[item]))],
         ('evidence-1',), lambda p: _set_evidence(p,'evidence-1',raw,claim))
    assert _job(db_path)['state'] == 'applied'
    with sqlite3.connect(db_path) as db:
        row = db.execute('SELECT content,formed_by FROM cognition').fetchone()
        assert row == ('用户' + raw, 'stated')


def test_short_confirmation_paraphrase_compiles_from_verified_proposal(tmp_path: Path) -> None:
    db_path = tmp_path / 'memory.sqlite3'
    raw = '行，就这么办'
    claim = '以后你想找人组队开黑时，我可以提醒你找王小明。'
    item = dict(action='form', target='owner_self', statement_kind='preference', formed_by='confirmed',
                proposition='以后用户想找人组队开黑时，助手可以提醒用户找王小明。', assistant_claim=claim,
                supports=[dict(evidence_id='evidence-1', sentence_id='t0')])
    _run(db_path, MutableClock(), [_model(dict(schema_version=8,result='cognitions',cognitions=[item]))],
         ('evidence-1',), lambda p: _set_evidence(p,'evidence-1',raw,claim))
    assert _job(db_path)['state'] == 'applied'
    with sqlite3.connect(db_path) as db:
        content, basis = db.execute('SELECT content,formed_by FROM cognition').fetchone()
        assert '王小明' in content and '组队' in content and basis == 'confirmed'
