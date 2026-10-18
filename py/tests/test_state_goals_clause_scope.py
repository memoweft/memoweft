"""Clause-local whitelist checks through worker, SQLite, provenance and recall."""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any, Literal

import pytest

from memoweft.integrations.hermes.recall import recall_world_snapshot
from test_hermes_world_worker import _initialize_database
from test_state_goals_revision import attempt
from test_state_goals_whitelist import CASES


CLAUSE_CASES = [c for c in CASES if 'goal_quote' in c]
CONNECTORS = [('zh', word) for word in ['但', '但是', '可是', '不过', '然而', '而', '只是', '而且', '并且']]
CONNECTORS += [('en', word) for word in ['but', 'however', 'though', 'and']]


def _check(path: Path, raw: str, quote: str | None, lang: Literal['en', 'zh'],
           *, applied: bool = True, annotated: bool = True) -> None:
    _initialize_database(path)
    result = attempt(path, raw, quote=quote, fields={'is_long_term_goal': annotated})
    assert result['state'] == ('applied' if applied else 'no_change'), result['world_result_json']
    selected = quote or raw
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT raw_content FROM evidence').fetchall() == [(raw,)]
        rows = db.execute('SELECT id,content FROM cognition').fetchall()
        snapshot = recall_world_snapshot(db, 'owner', selected, lang=lang)
        if applied:
            assert len(rows) == 1
            normalized = (selected.replace('我', '用户', 1) if selected.startswith('我') else
                          '用户' + selected if any('\u4e00' <= c <= '\u9fff' for c in selected) else selected)
            assert rows[0][1] == normalized
            assert snapshot is not None and snapshot.selected_item_ids == (('cognition', rows[0][0]),)
            start = raw.index(selected)
            assert db.execute("SELECT json_extract(payload_json,'$.start'),json_extract(payload_json,'$.end') "
                              "FROM evidence_ledger WHERE json_extract(content,'$.cognition_id')=?",
                              (rows[0][0],)).fetchall() == [(start, start + len(selected))]
        else:
            assert rows == []
            assert snapshot is None or not snapshot.selected_item_ids
        assert db.execute('SELECT count(*) FROM world_event').fetchone() == (0,)


@pytest.mark.parametrize('case', CLAUSE_CASES, ids=lambda case: case['id'])
@pytest.mark.parametrize('annotated', [False, True])
def test_only_selected_goal_clause_is_used(tmp_path: Path, case: dict[str, Any], annotated: bool) -> None:
    _check(tmp_path / 'world.sqlite', case['raw'], case['goal_quote'], 'zh', annotated=annotated)


@pytest.mark.parametrize('lang,connector', CONNECTORS)
@pytest.mark.parametrize('position', ['start', 'comma'])
@pytest.mark.parametrize('selected_only', [False, True])
def test_all_clause_connectors_at_start_or_after_comma(
    tmp_path: Path, lang: Literal['en', 'zh'], connector: str, position: str, selected_only: bool,
) -> None:
    goal = '我的目标是成为气象学家。' if lang == 'zh' else 'My goal is to become a meteorologist.'
    prefix = '我不知道别的，' if lang == 'zh' else "I don't know about others, "
    raw = (prefix if position == 'comma' else '') + connector + ('' if lang == 'zh' else ' ') + goal
    _check(tmp_path / 'world.sqlite', raw, goal if selected_only else None, lang)


@pytest.mark.parametrize('bad', [
    '我并不真的想成为飞行员',
    '我知道我朋友想开一家咖啡店',
    '我的目标是成为飞行员吗？',
    '我的目标是今天取快递',
])
@pytest.mark.parametrize('direction', ['before', 'after'])
@pytest.mark.parametrize('selection', ['whole', 'goal', 'bad'])
def test_other_clause_cannot_veto_or_rescue_selected_goal(
    tmp_path: Path, bad: str, direction: str, selection: str,
) -> None:
    goal = '我的目标是成为气象学家'
    first, last = (bad, goal) if direction == 'before' else (goal, bad)
    raw = first + '，但是' + last + ('' if last.endswith('？') else '。')
    quote = None if selection == 'whole' else goal if selection == 'goal' else bad
    _check(tmp_path / 'world.sqlite', raw, quote, 'zh', applied=selection != 'bad')
