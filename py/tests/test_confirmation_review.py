"""Independent PR #126 review's 32 probes, preserving every original case."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

import pytest

from test_distraction_sources import _exercise

NEG: list[tuple[str, str, str, str, str | None]] = [('overlap', 'en', 'I study art.', 'You enjoy cartography.', None),
 ('overlap', 'zh', '我喜欢红茶。', '你喜欢攀岩。', None),
 ('no', 'en', 'No.', 'You live in Bern.', None),
 ('no', 'zh', '不是。', '你住在北港。', None),
 ('not-really', 'en', 'Not really.', 'You like coffee.', None),
 ('not-really', 'zh', '不对。', '你喜欢咖啡。', None),
 ('cropped-denial', 'en', 'Yes, but no.', 'You live in Bern.', 'Yes'),
 ('cropped-denial', 'zh', '对，但不是这样的。', '你住在北港。', '对'),
 ('other-topic', 'en', 'Yes, Quito.', 'You live in Bern.', 'Yes'),
 ('other-topic', 'zh', '是的，苏州。', '你住在福州。', '是的'),
 ('partial', 'en', 'Yes, oolong tea only.', 'You like oolong tea and hiking.', 'Yes'),
 ('partial', 'zh', '对，只确认乌龙茶。', '你喜欢乌龙茶和攀岩。', '对'),
 ('withdrawn', 'en', 'Yes. Actually, no.', 'You live in Bern.', 'Yes'),
 ('withdrawn', 'zh', '是的。后来想想，不是。', '你住在北港。', '是的'),
 ('other-fact', 'en', 'I prefer birds.', 'You prefer boats.', None),
 ('other-fact', 'zh', '我住在西港。', '你住在北港。', None),
 ('value-boundary', 'en', 'Yes, car.', 'You enjoy cartography.', None),
 ('value-mismatch', 'zh', '对，就是白茶。', '你喜欢红茶。', None)]

POS: list[tuple[str, str, str, str]] = [('bare-yep', 'en', 'Yep.', 'You like Darjeeling tea.'),
 ('bare-indeed', 'en', 'Indeed!', 'You live in Quito.'),
 ('action', 'en', 'Sure, do that.', 'When you choose headphones, keep the volume low.'),
 ('literal-value', 'en', 'Yes, Darjeeling tea.', 'You like Darjeeling tea.'),
 ('case-value', 'en', 'Correct, BLUE.', 'Your favourite colour is blue.'),
 ('person-value', 'en', 'Yes, Sam.', 'You work with Sam.'),
 ('bare', 'zh', '行。', '你住在南港。'),
 ('bare-no-problem', 'zh', '没问题。', '你喜欢乌龙茶。'),
 ('action', 'zh', '好，就这么办。', '以后选耳机时保持低音量。'),
 ('literal-value', 'zh', '对，就是G8。', '你开的是小鹏G8。'),
 ('literal-value-2', 'zh', '是的，正是乌龙茶。', '你喜欢乌龙茶。'),
 ('city-value', 'zh', '对，南京。', '你住在南京。')]


@pytest.mark.parametrize('case', NEG, ids=lambda c: c[0] + '-' + c[1])
def test_review_rejects_false_confirmation(tmp_path: Path, case: tuple[str, str, str, str, str | None]) -> None:
    _, lang, user, assistant, quote = case
    state, rows, _ = _exercise(tmp_path, user, assistant, quote or user,
                               lang=cast(Literal['en', 'zh'], lang), confirmed=True)
    assert state == 'no_change' and rows == []


@pytest.mark.parametrize('case', POS, ids=lambda c: c[0] + '-' + c[1])
def test_review_keeps_true_confirmation(tmp_path: Path, case: tuple[str, str, str, str]) -> None:
    _, lang, user, assistant = case
    state, rows, _ = _exercise(tmp_path, user, assistant, user,
                               lang=cast(Literal['en', 'zh'], lang), confirmed=True)
    assert state == 'applied' and len(rows) == 1


@pytest.mark.parametrize(('lang', 'user', 'assistant'), [
    ('en', 'I only confirm that I drink oolong tea.', 'You drink oolong tea and own a red bicycle.'),
    ('zh', '我只确认我喝乌龙茶。', '你喝乌龙茶而且有一辆红色自行车。'),
])
def test_review_keeps_partial_fact_as_stated(
    tmp_path: Path, lang: Literal['en', 'zh'], user: str, assistant: str,
) -> None:
    state, rows, rendered = _exercise(tmp_path, user, assistant, user, lang=lang)
    assert state == 'applied' and len(rows) == 1
    assert 'bicycle' not in rendered and '自行车' not in rendered
