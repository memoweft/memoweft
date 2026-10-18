"""Third-review examples through the real worker, SQLite and recall path."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Literal, cast

import pytest

from memoweft.integrations.hermes.recall import recall_world_snapshot
from test_hermes_world_worker import _initialize_database
from test_state_goals_revision import attempt


CASES: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / 'fixtures/state-goals-policy-cases.json').read_text(encoding='utf-8')
)['cases']


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['expectation'] + '-' + case['id'])
def test_review_goal_whitelist(tmp_path: Path, case: dict[str, Any],
                               record_property: Callable[[str, object], None]) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    raw = case['raw']
    # Force a form recommendation: a scripted no_change cannot prove a
    # deterministic compiler guard. Evidence must survive either decision.
    result = attempt(path, raw, fields={'is_long_term_goal': True})
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT raw_content FROM evidence').fetchall() == [(raw,)]
        snapshot = recall_world_snapshot(db, 'owner', raw, lang=cast(Literal['en', 'zh'], case['lang']))
        rows = db.execute('SELECT id,content FROM cognition').fetchall()
        record_property('category', case['expectation'])
        record_property('formed', len(rows))
        record_property('known_limit', bool(case['known_limit']))
        if case['expectation'] == 'block' or case['known_limit']:
            assert result['state'] == 'no_change', result['world_result_json']
            assert rows == [], result['world_result_json']
            assert snapshot is None or not snapshot.selected_item_ids
        elif case['expectation'] == 'allow':
            assert result['state'] == 'applied', result['world_result_json']
            assert len(rows) == 1, result['world_result_json']
            assert snapshot is not None
            assert snapshot.selected_item_ids == (('cognition', rows[0][0]),)
        else:
            # 内核不判、由模型判断/已知限制。 Record the actual forced-form
            # outcome; it is not evidence that a real model understood it.
            assert case['expectation'] == 'model'
            assert result['state'] in {'applied', 'no_change'}
            assert len(rows) == int(result['state'] == 'applied')
        if rows:
            normalized = (raw.replace('我', '用户', 1) if raw.startswith('我') else
                          '用户' + raw if any('\u4e00' <= c <= '\u9fff' for c in raw) and '用户' not in raw else raw)
            assert rows[0][1] == normalized
        assert db.execute('SELECT count(*) FROM world_event').fetchone() == (0,)


@pytest.mark.parametrize('marker', [
    '今晚', '今天', '明天', '待会', '等会', '马上', '这周末',
    'tonight', 'today', 'tomorrow', 'this afternoon', 'this evening', 'later today', 'right now',
    '开玩笑的', '开个玩笑', '说着玩的', '逗你的', 'just kidding', 'jk', 'kidding',
])
def test_each_exact_exclusion_marker_blocks_goal_but_preserves_source(tmp_path: Path, marker: str) -> None:
    raw = f'我的目标是开图书馆，{marker}。' if any('\u4e00' <= c <= '\u9fff' for c in marker) else f'My goal is to open a library, {marker}.'
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    assert attempt(path, raw, fields={'is_long_term_goal': True})['state'] == 'no_change'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT raw_content FROM evidence').fetchall() == [(raw,)]
        assert db.execute('SELECT count(*) FROM cognition').fetchone() == (0,)


@pytest.mark.parametrize('raw', [
    'I have never intended to open a clinic.', 'I no longer plan to open a clinic.',
    'My goal is no longer to open a clinic.', '我不再打算开诊所。', '我的目标不是开诊所。',
    "My neighbours' goal is to open a clinic.", "My mentor's plan is to open a clinic.",
    '我的小榆的目标是开诊所。', '我外祖父的计划是开诊所。', '我朋友想开诊所。',
    'My goal is to support my mentor\'s career.', '我的目标是支持我的小榆的事业。',
    'My goal is to open a clinic?', '我的目标是开诊所吗？',
    'I will not plan to open a clinic.', "I won't intend to open a clinic.",
    "I'm no longer going to open a clinic.", "My goal isn't to open a clinic.",
    'Aster said ‘My goal is to open a clinic; my plan is to open a library.’',
    'Aster will establish a clinic.',
])
@pytest.mark.parametrize('annotated', [False, True])
def test_source_guard_and_explicit_goal_hint_are_fail_closed(tmp_path: Path, raw: str, annotated: bool) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    result = attempt(path, raw, fields={'is_long_term_goal': annotated})
    # The last case has no goal syntax; only an explicit goal recommendation
    # invokes the whitelist. Without it, source semantics belong to the model.
    if annotated or raw != 'Aster will establish a clinic.':
        assert result['state'] == 'no_change', result['world_result_json']
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT raw_content FROM evidence').fetchall() == [(raw,)]


@pytest.mark.parametrize('raw', [
    '三年内我要取得会计资格证。', 'Next year I intend to qualify as a paramedic.',
    'My goal is to learn to tell stories if my schedule allows.',
    '我的目标是学会用五种语言说你好，只要时间允许。',
    'My goal is to open a clinic if funding is approved.',
    '如果资金获批，我的目标是开诊所。',
])
def test_time_adjunct_conditions_and_goal_verbs_do_not_change_speaker(tmp_path: Path, raw: str) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    assert attempt(path, raw, fields={'is_long_term_goal': True})['state'] == 'applied'


@pytest.mark.parametrize('value', ['yes', 1, None])
def test_goal_hint_must_be_boolean(tmp_path: Path, value: object) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    result = attempt(path, 'My goal is to open a clinic.', fields={'is_long_term_goal': value})
    assert result['state'] == 'no_change'
    assert 'invalid_goal_intent' in str(result['world_result_json'])


@pytest.mark.parametrize('raw', [
    'My goal is to open a library, "just kidding".',
    '我的目标是开图书馆，“开玩笑的”。',
])
def test_quoted_exclusion_markers_remain_literal_exclusions(tmp_path: Path, raw: str) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    assert attempt(path, raw, fields={'is_long_term_goal': True})['state'] == 'no_change'
