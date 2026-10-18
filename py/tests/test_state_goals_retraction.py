"""Past predicates and later self-retraction, with real worker/provenance/recall."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

import pytest

from test_state_goals_clause_scope import _check
from test_state_goals_whitelist import CASES


REVIEW6 = [c for c in CASES if 'review_quote' in c]


@pytest.mark.parametrize('case', REVIEW6, ids=lambda c: c['id'])
@pytest.mark.parametrize('selected_only', [False, True])
def test_sixth_review_whole_and_second_clause(tmp_path: Path, case: dict[str, Any], selected_only: bool) -> None:
    _check(tmp_path / 'world.sqlite', case['raw'], case['review_quote'] if selected_only else None,
           'zh', applied=case['expectation'] == 'allow')


@pytest.mark.parametrize('raw', [
    '我想过开店。', '我考虑过开店。', '我准备过考潜水证。',
    '我曾经想创办书店。', '我本来想成为老师。', '我原本打算申请建筑师资格。',
    'I thought about opening a library.', 'I considered opening a library.',
    'I used to want to open a library.', 'I was going to open a library.',
    'I had planned to open a library.',
])
def test_each_explicit_past_intention_is_not_an_ongoing_goal(tmp_path: Path, raw: str) -> None:
    lang = 'zh' if raw.startswith('我') else 'en'
    _check(tmp_path / 'world.sqlite', raw, None, cast(Literal['en', 'zh'], lang), applied=False)


@pytest.mark.parametrize('lang,withdrawal', [
    ('zh', '我不打算做了。'), ('zh', '我不想做了。'), ('zh', '我不准备做了。'),
    ('zh', '我不会去做。'), ('zh', '算了。'),
    ('zh', '我其实没有这个打算。'), ('zh', '我并不真的想做。'),
    ('en', "I'm not going to do it."), ('en', "I don't plan to do it."),
    ('en', "I don't want to do it."), ('en', 'Never mind.'),
])
@pytest.mark.parametrize('selected_only', [False, True])
def test_current_goal_followed_by_explicit_retraction_is_not_formed(
    tmp_path: Path, lang: Literal['en', 'zh'], withdrawal: str, selected_only: bool,
) -> None:
    goal = '我的目标是开一间图书馆。' if lang == 'zh' else 'My goal is to open a library.'
    raw = goal + withdrawal
    # The complete supporting utterance retains the subsequent counterevidence;
    # clipping its goal clause cannot erase the explicit retraction.
    _check(tmp_path / 'world.sqlite', raw, goal if selected_only else None, lang, applied=False)


@pytest.mark.parametrize('lang,raw', [
    ('zh', '我的目标是开图书馆，但是我知道我朋友不打算帮忙。'),
    ('en', "My goal is to open a library, but my parents don't plan to help."),
    ('zh', '我的目标是完成一本名叫《我想过的事》的书。'),
    ('en', 'My goal is to write a book about what I considered as a student.'),
    ('zh', '我的目标是开图书馆，但是小禾说“算了”。'),
    ('en', 'My goal is to open a library, but Rowan said "never mind".'),
])
def test_other_subject_and_goal_content_do_not_retract_owner_goal(
    tmp_path: Path, lang: Literal['en', 'zh'], raw: str,
) -> None:
    _check(tmp_path / 'world.sqlite', raw, None, lang)
