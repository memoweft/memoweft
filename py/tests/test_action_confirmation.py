"""Action assent binds to the selected proposal, never a nearby factual guess."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from test_distraction_sources import _exercise


@pytest.mark.parametrize(('lang', 'user', 'fact', 'proposal'), [
    ('en', 'Yes, do that.', 'You live in Bern.', 'I can show you a map of Quito.'),
    ('en', 'Yes, do that.', 'You live in Bern.', 'I can save that.'),
    ('zh', '好。', '你住在北港。', '我可以展示基多的地图。'),
    ('zh', '好。', '你住在北港。', '我可以保存这条信息。'),
])
def test_action_assent_cannot_confirm_the_selected_factual_guess(
    tmp_path: Path, lang: Literal['en', 'zh'], user: str, fact: str, proposal: str,
) -> None:
    state, rows, rendered = _exercise(
        tmp_path, user, fact + ' ' + proposal, user, lang=lang,
        confirmed=True, selected_claim=fact,
    )
    assert state == 'no_change'
    assert rows == []
    assert rendered == ''

@pytest.mark.parametrize(('lang', 'user', 'fact', 'proposal'), [
    ('en', 'Yes, do that.', 'You live in Bern.', 'I can show you a map of Quito.'),
    ('en', 'Go ahead.', 'You live in Bern.', 'I can save that.'),
    ('zh', '好。', '你住在北港。', '我可以展示基多的地图。'),
    ('zh', '就这么做。', '你住在北港。', '我可以保存这条信息。'),
])
def test_action_assent_confirms_only_the_selected_proposal(
    tmp_path: Path, lang: Literal['en', 'zh'], user: str, fact: str, proposal: str,
) -> None:
    state, rows, rendered = _exercise(
        tmp_path, user, fact + ' ' + proposal, user, lang=lang,
        confirmed=True, selected_claim=proposal, prior_turn=True,
    )
    assert state == 'applied'
    assert len(rows) == 1
    expected = proposal.replace('你', '用户') if lang == 'zh' else proposal
    assert rows[0][1] == (expected.rstrip('。！?？ ') if lang == 'zh' else expected)
    # The unselected guess must not be relabeled as confirmed in the source
    # explanation either; the original complete turn remains in provenance.
    assert 'Bern' not in rendered and '北港' not in rendered

@pytest.mark.parametrize(('lang', 'user', 'claim'), [
    ('en', 'Yes, do that.', 'You live in Bern.'),
    ('en', 'Go ahead.', 'Your goal is to save old maps.'),
    ('en', 'Yes, do that.', 'I can show a map. You live in Bern.'),
    ('en', 'Go ahead.', 'You live in Bern. I can save that.'),
    ('en', 'Go ahead.', 'Aster said "I can save that."'),
    ('en', 'Yes, do that.', 'When I show a map, you live in Bern.'),
    ('zh', '就这么做。', '你住在北港。'),
    ('zh', '好，就这么办。', '你的目标是保存旧地图。'),
    ('zh', '好。', '我可以展示地图。你住在北港。'),
    ('zh', '就这么做。', '你住在北港。我可以保存这条信息。'),
    ('zh', '好。', '小禾说“我可以保存这条信息”。'),
    ('zh', '好。', '我每天保存旧地图。'),
])
def test_action_words_elsewhere_do_not_make_a_selected_fact_a_proposal(
    tmp_path: Path, lang: Literal['en', 'zh'], user: str, claim: str,
) -> None:
    state, rows, _ = _exercise(tmp_path, user, claim, user, lang=lang, confirmed=True)
    assert state == 'no_change' and rows == []


@pytest.mark.parametrize(('lang', 'user', 'quote', 'claim'), [
    ('en', 'Yes, do that. Actually, no.', 'Yes, do that.', 'I can save that.'),
    ('zh', '好，但是不要保存。', '好', '我可以保存这条信息。'),
])
def test_cropping_action_assent_cannot_discard_denial(
    tmp_path: Path, lang: Literal['en', 'zh'], user: str, quote: str, claim: str,
) -> None:
    state, rows, _ = _exercise(tmp_path, user, claim, quote, lang=lang, confirmed=True)
    assert state == 'no_change' and rows == []
