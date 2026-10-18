"""Real worker/compiler/SQLite/recall path; only the interpreter is scripted."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Literal

import pytest

from memoweft.integrations.hermes.batch_adapter import HermesBatchAdapterProcessor
from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from test_hermes_batch_adapter_v5 import _batch, _model, _set_evidence
from test_hermes_world_worker import MutableClock, _initialize_database, _insert_job, _job, _policy


EXAM = json.loads((Path(__file__).parent / 'fixtures/state-goals-exam.json').read_text(encoding='utf-8'))['cases']


def form(path: Path, raw: str, n: int, *, kind: str = 'preference', prior: str | None = None,
         fields: dict[str, Any] | None = None,
         observe: Callable[[dict[str, Any]], None] | None = None) -> str:
    clock = MutableClock()
    eid = f'evidence-{n}'
    _insert_job(path, clock, job_id=f'job-{n}', evidence_ids=(eid,))
    _set_evidence(path, eid, raw)
    item: dict[str, Any] = {'action': 'form', 'statement_kind': kind, 'formed_by': 'stated',
                           'proposition': 'placeholder', 'supports': [{'evidence_id': eid, 'quote': raw}]}
    if prior:
        item['supersedes_relationship_id' if kind == 'relationship' else 'supersedes_cognition_id'] = prior
    item.update(fields or {})

    def route(messages: list[dict[str, str]], *args: Any, **kwargs: Any) -> dict[str, object]:
        if observe:
            observe(json.loads(messages[1]['content']))
        return _model(_batch(item))

    worker = WorldJobWorker(path, processor=HermesBatchAdapterProcessor(str(path), route, clock=clock),
                            policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    result = _job(path, f'job-{n}')
    assert result['state'] == 'applied', result
    outcomes = json.loads(str(result['world_result_json']))['cognitions']
    return str(outcomes[0].get('relationship_id') or outcomes[0]['cognition_id'])


@pytest.mark.parametrize('lang', ['en', 'zh'])
@pytest.mark.parametrize('topic', ['job', 'home', 'preference', 'goal-completed', 'goal-abandoned'])
def test_state_change_keeps_current_and_past_distinct(tmp_path: Path, lang: Literal['en', 'zh'], topic: str) -> None:
    pairs = {
        'en': {
            'job': ('I work at Cedar Labs.', 'I now work at Birch Labs.', 'work'),
            'home': ('I live in Northport.', 'I now live in Southport.', 'live'),
            'preference': ('I prefer paper notebooks.', 'I now prefer digital notebooks.', 'notebooks'),
            'goal-completed': ('My long-term goal is to build a community library.',
                               'I have completed my goal of building a community library.', 'community library'),
            'goal-abandoned': ('My long-term goal is to build a community library.',
                               'I have abandoned my goal of building a community library.', 'community library'),
        },
        'zh': {
            'job': ('我在青杉公司工作。', '我现在在白桦公司工作。', '工作'),
            'home': ('我住在北港。', '我现在住在南港。', '现在住在'),
            'preference': ('我偏好纸质笔记本。', '我现在偏好电子笔记本。', '笔记本'),
            'goal-completed': ('我的长期目标是建一座社区图书馆。', '我已经完成建社区图书馆的目标。', '社区图书馆'),
            'goal-abandoned': ('我的长期目标是建一座社区图书馆。', '我已经放弃建社区图书馆的目标。', '社区图书馆'),
        },
    }
    old, new, query = pairs[lang][topic]
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    prior = form(path, old, 1)
    successor = form(path, new, 2, prior=prior)
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', query, lang=lang)
        assert snapshot is not None and snapshot.selected_item_ids == (('cognition', successor),)
        assert new.replace('我', '用户', 1) in snapshot.rendered_recall
        history = recall_world_snapshot(db, 'owner', ('past ' if lang == 'en' else '以前的') + query,
                                        lang=lang)
        assert history is not None and prior in {i for _, i in history.selected_item_ids}
        assert ('Memory (past)' if lang == 'en' else '记忆（过往）') in history.rendered_recall
        assert ('corrected by the user' if lang == 'en' else '经用户纠正') not in history.rendered_recall
        assert db.execute('SELECT reason FROM cognition_transitions').fetchone() == ('supersedes',)
        assert db.execute('SELECT invalid_at FROM cognition WHERE id=?', (prior,)).fetchone() == (None,)
        assert db.execute('SELECT raw_content FROM evidence ORDER BY id').fetchall() == [(old,), (new,)]

    def observe(payload: dict[str, Any]) -> None:
        assert [c['id'] for c in payload['current_cognitions']] == [successor]
        assert payload['current_cognitions'][0]['predecessor_context']

    form(path, 'I prefer quiet rooms.' if lang == 'en' else '我偏好安静房间。', 3, observe=observe)


@pytest.mark.parametrize('case', EXAM, ids=lambda c: str(c['id']))
@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_complete_exam_claim_is_formed_and_recalled_verbatim(tmp_path: Path, case: dict[str, Any], lang: Literal['en', 'zh']) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    raw = str(case[lang])
    cid = form(path, raw, 1)
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', raw, lang=lang)
        assert snapshot is not None and snapshot.selected_item_ids == (('cognition', cid),)
        assert raw.replace('我', '用户', 1) in snapshot.rendered_recall
        assert db.execute('SELECT count(*) FROM world_event').fetchone() == (0,)


@pytest.mark.parametrize('mode', ['unknown', 'other-owner', 'revoked', 'already-replaced', 'self'])
def test_invalid_supersession_cannot_mutate_world(tmp_path: Path, mode: str) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    old = 'I prefer paper notebooks.'
    prior = form(path, old, 1)
    with sqlite3.connect(path) as db:
        if mode == 'other-owner':
            db.execute("UPDATE cognition SET subject_id='another-owner' WHERE id=?", (prior,))
        if mode == 'revoked':
            db.execute('UPDATE evidence SET allow_inference=0 WHERE id=\'evidence-1\'')
    if mode == 'already-replaced':
        form(path, 'I now prefer digital notebooks.', 2, prior=prior)
    if mode == 'unknown':
        prior = 'cognition-missing'
    with sqlite3.connect(path) as db:
        before = db.execute('SELECT id,content,confidence FROM cognition ORDER BY id').fetchall()
    clock = MutableClock()
    _insert_job(path, clock, job_id='rejected', evidence_ids=('new-evidence',))
    raw = old if mode == 'self' else 'I now prefer audio notebooks.'
    _set_evidence(path, 'new-evidence', raw)
    item = {'action': 'form', 'statement_kind': 'preference', 'proposition': 'placeholder',
            'supports': [{'evidence_id': 'new-evidence', 'quote': raw}], 'supersedes_cognition_id': prior}
    worker = WorldJobWorker(path, processor=HermesBatchAdapterProcessor(
        str(path), lambda *args, **kw: _model(_batch(item)), clock=clock), policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    assert _job(path, 'rejected')['state'] == 'no_change'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT id,content,confidence FROM cognition ORDER BY id').fetchall() == before


@pytest.mark.parametrize('case', EXAM[1:], ids=lambda c: str(c['id']))
@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_exam_change_supersedes_old_preference_with_complete_reason(
    tmp_path: Path, case: dict[str, Any], lang: Literal['en', 'zh'],
) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    if case['id'] == 'travel-change':
        old = ('I like wellness retreats because they offer relaxation and activities that promote physical and mental health, aligning with my lifestyle.'
               if lang == 'en' else '我喜欢健康度假，因为能放松身心，符合我的生活方式。')
    else:
        old = ('I like documentaries because they are informative and provide real-world insights, which complement my thirst for knowledge.'
               if lang == 'en' else '我喜欢纪录片，因为它们提供现实知识，满足我的求知欲。')
    prior = form(path, old, 1)
    successor = form(path, str(case[lang]), 2, prior=prior)
    with sqlite3.connect(path) as db:
        query = str(case[lang]).split('.' if lang == 'en' else '。', 1)[0]
        snapshot = recall_world_snapshot(db, 'owner', query, lang=lang)
        assert snapshot is not None and snapshot.selected_item_ids == (('cognition', successor),)
        assert str(case[lang]).replace('我', '用户', 1) in snapshot.rendered_recall
        assert db.execute('SELECT prior_cognition_id,replacement_cognition_id,reason FROM cognition_transitions').fetchall() == [(prior, successor, 'supersedes')]


@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_person_change_cannot_replace_another_persons_state(tmp_path: Path, lang: Literal['en', 'zh']) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    first, second = ('Aster', 'Clover') if lang == 'en' else ('小禾', '小岚')
    raw = f'{first} prefers paper notebooks.' if lang == 'en' else f'{first}喜欢纸质笔记本。'
    old = form(path, raw, 1, fields={'entity': {'canonical_name': first, 'kind': 'person'}})
    clock = MutableClock()
    _insert_job(path, clock, job_id='wrong-person', evidence_ids=('new-evidence',))
    new_raw = f'{second} prefers digital notebooks.' if lang == 'en' else f'{second}喜欢电子笔记本。'
    _set_evidence(path, 'new-evidence', new_raw)
    item = {'action': 'form', 'statement_kind': 'preference', 'proposition': 'placeholder',
            'supports': [{'evidence_id': 'new-evidence', 'quote': new_raw}],
            'entity': {'canonical_name': second, 'kind': 'person'}, 'supersedes_cognition_id': old}
    worker = WorldJobWorker(path, processor=HermesBatchAdapterProcessor(
        str(path), lambda *args, **kw: _model(_batch(item)), clock=clock), policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    assert _job(path, 'wrong-person')['state'] == 'no_change'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM cognition').fetchone() == (1,)
        assert db.execute('SELECT confidence FROM cognition WHERE id=?', (old,)).fetchone() == (600,)
        assert db.execute('SELECT count(*) FROM cognition_transitions').fetchone() == (0,)


@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_relationship_successor_is_current_in_later_formation(tmp_path: Path, lang: Literal['en', 'zh']) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    names = ('Aster', 'Clover') if lang == 'en' else ('小禾', '小岚')
    words = [f'{name} is my partner.' if lang == 'en' else f'{name}是我的伴侣。' for name in names]

    def fields(name: str) -> dict[str, Any]:
        return {'target_entity': {'canonical_name': name, 'kind': 'person'}, 'relation_type': 'partner'}

    old = form(path, words[0], 1, kind='relationship', fields=fields(names[0]))
    new = form(path, words[1], 2, kind='relationship', fields=fields(names[1]), prior=old)

    def observe(payload: dict[str, Any]) -> None:
        assert [r['id'] for r in payload['current_relationships']] == [new]

    form(path, 'I prefer quiet rooms.' if lang == 'en' else '我偏好安静房间。', 3, observe=observe)
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', 'my partner' if lang == 'en' else '我的伴侣', lang=lang)
        assert snapshot is not None and snapshot.selected_item_ids == (('relationship', new),)
        history = recall_world_snapshot(db, 'owner', 'past partner' if lang == 'en' else '以前的伴侣', lang=lang)
        assert history is not None and old in {i for _, i in history.selected_item_ids}
        assert ('corrected by the user' if lang == 'en' else '经用户纠正') not in history.rendered_recall
        assert db.execute('SELECT reason,revision FROM relationship_transitions').fetchone() == ('superseded', 2)


def test_three_state_chain_preserves_history_and_omitted_topic(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    old = form(path, 'My long-term library goal is to open a reading room by 2030.', 1)
    mid = form(path, 'My long-term library goal is now to open a reading room by 2032.', 2, prior=old)
    new = form(path, 'I have abandoned this goal.', 3, prior=mid)
    with sqlite3.connect(path) as db:
        current = recall_world_snapshot(db, 'owner', 'long-term library goal', lang='en')
        assert current is not None and current.selected_item_ids == (('cognition', new),)
        assert 'abandoned' in current.rendered_recall
        assert '2030' not in current.rendered_recall and '2032' not in current.rendered_recall
        past = recall_world_snapshot(db, 'owner', 'previous long-term library goal', lang='en')
        assert past is not None and '2030' in past.rendered_recall and '2032' in past.rendered_recall
        assert 'corrected by the user' not in past.rendered_recall
        assert db.execute('SELECT count(*) FROM cognition_transitions').fetchone() == (2,)


def test_permission_change_during_interpretation_rolls_back_successor(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    old = form(path, 'I live in Northport.', 1, kind='attribute')
    clock = MutableClock()
    _insert_job(path, clock, job_id='racing', evidence_ids=('new-evidence',))
    raw = 'I now live in Southport.'
    _set_evidence(path, 'new-evidence', raw)
    calls = 0

    def route(*args: Any, **kwargs: Any) -> dict[str, object]:
        nonlocal calls
        calls += 1
        with sqlite3.connect(path) as db:
            db.execute("UPDATE evidence SET allow_inference=0 WHERE id='evidence-1'")
        return _model(_batch({'action': 'form', 'statement_kind': 'attribute', 'proposition': 'placeholder',
                             'supports': [{'evidence_id': 'new-evidence', 'quote': raw}],
                             'supersedes_cognition_id': old}))

    worker = WorldJobWorker(path, processor=HermesBatchAdapterProcessor(str(path), route, clock=clock),
                            policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1 and calls == 1
    result = _job(path, 'racing')
    assert result['state'] == 'no_change'
    assert json.loads(str(result['world_result_json']))['reason'] == 'supersession_target_not_current'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM cognition').fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM cognition_transitions').fetchone() == (0,)
        assert db.execute('SELECT confidence FROM cognition WHERE id=?', (old,)).fetchone() == (600,)


@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_goal_remains_an_intention_without_evidence_of_progress(tmp_path: Path, lang: Literal['en', 'zh']) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    raw = 'My goal is to build a community library by 2020.' if lang == 'en' else '我的目标是在2020年前建一座社区图书馆。'
    goal = form(path, raw, 1)
    form(path, 'I prefer tea.' if lang == 'en' else '我偏好喝茶。', 2)
    with sqlite3.connect(path) as db:
        query = 'community library' if lang == 'en' else '社区图书馆'
        one = recall_world_snapshot(db, 'owner', query, lang=lang)
        two = recall_world_snapshot(db, 'owner', query, lang=lang)
        assert one is not None and one == two and one.selected_item_ids == (('cognition', goal),)
        assert raw.replace('我', '用户', 1) in one.rendered_recall
        assert db.execute('SELECT count(*) FROM world_event').fetchone() == (0,)
        assert db.execute('SELECT count(*) FROM cognition_transitions').fetchone() == (0,)


@pytest.mark.parametrize('reverse', [False, True])
def test_same_predecessor_cannot_be_corrected_and_superseded_in_one_batch(tmp_path: Path, reverse: bool) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    old = form(path, 'I prefer paper notebooks.', 1)
    clock = MutableClock()
    _insert_job(path, clock, job_id='competing', evidence_ids=('new-evidence',))
    raw = 'I now prefer digital notebooks. I misremembered; I prefer audio notebooks.'
    _set_evidence(path, 'new-evidence', raw)
    changes = [
        {'action': 'form', 'statement_kind': 'preference', 'proposition': 'placeholder',
         'supersedes_cognition_id': old, 'supports': [{'evidence_id': 'new-evidence', 'sentence_id': 't0'}]},
        {'action': 'correct', 'statement_kind': 'preference', 'proposition': 'placeholder',
         'corrects_cognition_id': old, 'supports': [{'evidence_id': 'new-evidence', 'quote': 'I prefer audio notebooks.'}]},
    ]
    if reverse:
        changes.reverse()
    worker = WorldJobWorker(path, processor=HermesBatchAdapterProcessor(
        str(path), lambda *args, **kw: _model(_batch(*changes)), clock=clock), policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    assert _job(path, 'competing')['state'] == 'no_change'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM cognition').fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM cognition_transitions').fetchone() == (0,)
