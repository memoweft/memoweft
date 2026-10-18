"""Independent-review reproductions through the real formation and recall path."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Literal

import pytest

from memoweft.integrations.hermes.batch_adapter import HermesBatchAdapterProcessor
from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from test_hermes_batch_adapter_v5 import _batch, _model, _set_evidence
from test_hermes_world_worker import MutableClock, _initialize_database, _insert_job, _job, _policy
from test_state_goals import form
from test_trust_command_service import _service, _command

NEGATIVES: list[dict[str, str]] = json.loads(
    (Path(__file__).parent / 'fixtures/state-goals-negatives.json').read_text(encoding='utf-8')
)['cases']


def attempt(path: Path, raw: str, *, fields: dict[str, Any] | None = None,
            quote: str | None = None, n: int = 1) -> dict[str, object]:
    clock = MutableClock()
    eid, jid = f'evidence-{n}', f'job-{n}'
    _insert_job(path, clock, job_id=jid, evidence_ids=(eid,))
    _set_evidence(path, eid, raw)
    item = dict(action='form', statement_kind='preference', formed_by='stated', proposition='placeholder',
                supports=[dict(evidence_id=eid, quote=quote or raw)])
    item.update(fields or {})
    worker = WorldJobWorker(path, processor=HermesBatchAdapterProcessor(
        str(path), lambda *a, **kw: _model(_batch(item)), clock=clock), policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    return _job(path, jid)


@pytest.mark.parametrize('case', NEGATIVES, ids=lambda c: c['id'])
def test_review_negative_is_not_an_owner_goal(tmp_path: Path, case: dict[str, str]) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    result = attempt(path, case['raw'])
    assert result['state'] == 'no_change', (case['id'], result['world_result_json'])
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM cognition').fetchone() == (0,)
        assert db.execute('SELECT count(*) FROM world_event').fetchone() == (0,)
        assert db.execute('SELECT raw_content FROM evidence').fetchall() == [(case['raw'],)]


@pytest.mark.parametrize('lang', ['en', 'zh'])
@pytest.mark.parametrize('topic', ['home', 'relationship', 'job', 'preference'])
@pytest.mark.parametrize('sequence', ['ABA', 'ABAB', 'ABCA'])
def test_return_to_past_state_preserves_each_occurrence(
    tmp_path: Path, lang: Literal['en', 'zh'], topic: str, sequence: str,
) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    words = {
        'en': {'home': ['I live in Northport.', 'I now live in Southport.', 'I now live in Eastport.'],
               'job': ['I work at Cedar Labs.', 'I now work at Birch Labs.', 'I now work at Elm Labs.'],
               'preference': ['I prefer green tea.', 'I now prefer black tea.', 'I now prefer white tea.']},
        'zh': {'home': ['我住在北港。', '我现在住在南港。', '我现在住在东港。'],
               'job': ['我在青杉公司工作。', '我现在在白桦公司工作。', '我现在在榆树公司工作。'],
               'preference': ['我偏好绿茶。', '我现在偏好红茶。', '我现在偏好白茶。']},
    }
    names = ['Aster', 'Clover', 'Birch'] if lang == 'en' else ['小禾', '小岚', '小青']
    kind = 'relationship' if topic == 'relationship' else 'preference' if topic == 'preference' else 'attribute'
    table = 'relationship' if topic == 'relationship' else 'cognition'
    ids: list[str] = []
    originals: list[str] = []
    for n, letter in enumerate(sequence, 1):
        index = ord(letter) - ord('A')
        fields: dict[str, Any] = {}
        if topic == 'relationship':
            name = names[index]
            raw = f'{name} is my partner.' if lang == 'en' else f'{name}是我的伴侣。'
            if n > 2 and letter == 'A':
                raw = 'Aster and I are partners again.' if lang == 'en' else '我和小禾又成为伴侣了。'
            fields = dict(target_entity=dict(canonical_name=name, kind='person'), relation_type='partner')
        else:
            raw = words[lang][topic][index]
        current = form(path, raw, n, kind=kind, prior=ids[-1] if ids else None, fields=fields)
        ids.append(current)
        originals.append(raw)
        with sqlite3.connect(path) as db:
            snapshot = recall_world_snapshot(db, 'owner', raw, lang=lang)
            assert snapshot is not None and snapshot.selected_item_ids == ((table, current),)
            assert not set(ids[:-1]).intersection(i for _, i in snapshot.selected_item_ids)
    with sqlite3.connect(path) as db:
        assert len(set(ids)) == len(sequence)
        transitions = db.execute(f'SELECT prior_{table}_id,replacement_{table}_id FROM {table}_transitions ORDER BY revision').fetchall()
        assert transitions == list(zip(ids, ids[1:]))
        # One Evidence per actual user turn, including repeated utterances;
        # recurrence must not synthesize copies or rewrite any prior source.
        assert db.execute('SELECT raw_content FROM evidence ORDER BY id').fetchall() == [(s,) for s in originals]
        for n, identity in enumerate(ids, 1):
            link_table, column = ('relationship_evidence', 'relationship_id') if table == 'relationship' else ('cognition_evidence', 'cognition_id')
            assert db.execute(f'SELECT 1 FROM {link_table} WHERE {column}=? AND evidence_id=?', (identity, f'evidence-{n}')).fetchone()
        assert db.execute(f'SELECT count(*) FROM {table}').fetchone() == (len(sequence),)
        assert db.execute(f'SELECT confidence FROM {table} WHERE id=?', (ids[-1],)).fetchone()[0] >= 600
        if table == 'cognition':
            past = recall_world_snapshot(db, 'owner', ('past ' if lang == 'en' else '以前的') + originals[0], lang=lang)
            assert past is not None and ids[0] in {i for _, i in past.selected_item_ids}
            assert ('Memory (past):' if lang == 'en' else '记忆（过往）：') in past.rendered_recall


@pytest.mark.parametrize('raw,quote', [
    ('如果中了大奖，我的长期目标会是建图书馆。', '我的长期目标会是建图书馆。'),
    ('小禾说：“我的长期目标是开图书馆。”', '我的长期目标是开图书馆。'),
    ('My friend said, "My goal is to build a library."', 'My goal is to build a library.'),
    ('If I were wealthy, my goal would be to build a library.', 'my goal would be to build a library.'),
    ("'My goal is to build a library.'", 'My goal is to build a library.'),
])
def test_clipping_support_cannot_remove_hypothetical_or_speaker(tmp_path: Path, raw: str, quote: str) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    assert attempt(path, raw, quote=quote)['state'] == 'no_change'


@pytest.mark.parametrize('topic', ['home', 'relationship'])
def test_recurrence_restatement_provenance_and_whole_chain_forget(tmp_path: Path, topic: str) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    kind = 'attribute' if topic == 'home' else 'relationship'
    words = ['我住在北港。', '我住在南港。', '我住在北港。'] if topic == 'home' else ['小禾是我的伴侣。', '小岚是我的伴侣。', '我和小禾又成为伴侣了。']
    fields = ([{}, {}, {}] if topic == 'home' else
              [dict(target_entity=dict(canonical_name=name, kind='person'), relation_type='partner')
               for name in ['小禾', '小岚', '小禾']])
    ids: list[str] = []
    for n, raw in enumerate(words, 1):
        ids.append(form(path, raw, n, kind=kind, prior=ids[-1] if ids else None, fields=fields[n - 1]))
    assert form(path, words[-1], 4, kind=kind, fields=fields[-1]) == ids[-1]
    table = 'cognition' if topic == 'home' else 'relationship'
    with sqlite3.connect(path) as db:
        assert db.execute(f'SELECT count(*) FROM {table}').fetchone() == (3,)
        revision = int(db.execute('SELECT revision FROM memory_state').fetchone()[0])
    receipt = _service(path).submit_command(_command('forget-recurrence', revision, 'delete_world_item', table, ids[-1]))
    assert receipt['result_state'] == 'applied'
    with sqlite3.connect(path) as db:
        assert db.execute(f'SELECT count(*) FROM {table}').fetchone() == (0,)
        assert db.execute(f'SELECT count(*) FROM {table}_transitions').fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM evidence WHERE raw_content<>'' OR deleted_at IS NULL OR allow_inference<>0").fetchone() == (0,)


@pytest.mark.parametrize('same_topic', [True, False])
def test_parsed_topic_cannot_silently_change_during_supersession(tmp_path: Path, same_topic: bool) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    old = form(path, '我偏好纸质笔记本。', 1, fields={'entity': {'canonical_name': '笔记本', 'kind': 'topic'}})
    result = attempt(path, '我现在偏好电子笔记本。' if same_topic else '我现在偏好清淡早餐。', n=2,
                     fields={'supersedes_cognition_id': old,
                             'entity': {'canonical_name': '笔记本' if same_topic else '早餐', 'kind': 'topic'}})
    assert result['state'] == ('applied' if same_topic else 'no_change')


def test_attribute_cannot_be_superseded_by_unrelated_preference(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    old = form(path, '我住在北港。', 1, kind='attribute')
    assert attempt(path, '我偏好绿茶。', n=2, fields={'supersedes_cognition_id': old})['state'] == 'no_change'


@pytest.mark.parametrize('raw', [
    "I'm not planning to build a library.", '我不打算建图书馆。',
    'If I were rich, I would live in Northport.', '如果我有钱，我会住在北港。',
    'Do I live in Northport?', '我住在北港吗？',
    'Aster said my long-term goal is to open a library.', '小禾说我的长期目标是开图书馆。',
    'If I were a doctor, I would help patients.', '如果我成了医生，我会救人。',
])
@pytest.mark.parametrize('kind', ['attribute', 'preference'])
def test_explicit_nonassertion_cannot_be_promoted_to_a_state(tmp_path: Path, raw: str, kind: str) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    assert attempt(path, raw, fields={'statement_kind': kind})['state'] == 'no_change'


@pytest.mark.parametrize('raw', [
    '我的长期目标是写一本小说。', '我的长期目标是成为翻译。',
    'My long-term goal is to write a novel.', 'My long-term goal is to translate a book.',
])
def test_real_writing_or_translation_goal_is_not_fictional_context(tmp_path: Path, raw: str) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    assert attempt(path, raw)['state'] == 'applied'
