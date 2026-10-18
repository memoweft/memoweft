"""A scoped reference to an earlier action must keep its literal scope."""
from __future__ import annotations

from pathlib import Path

import pytest

from test_distraction_sources import _exercise


CLAIM = '以后用户想组队时，我提醒用户找王小明。'


@pytest.mark.parametrize('user', [
    '刚才你说的组队提醒，行，就这么办。',
    '之前你说的组队提醒，好，就这么做。',
])
def test_referenced_action_is_formed_and_recalled(tmp_path: Path, user: str) -> None:
    state, rows, recalled = _exercise(
        tmp_path, user, CLAIM, user, lang='zh', confirmed=True, prior_turn=True,
    )
    assert state == 'applied'
    assert len(rows) == 1
    assert '王小明' in rows[0][1] and '王小明' in recalled


@pytest.mark.parametrize(('user', 'claim', 'quote'), [
    ('刚才你说的散步提醒，行，就这么办。', CLAIM, None),
    ('刚才你说的组队保存，行，就这么办。', CLAIM, None),
    ('刚才你说的组队提醒，行，就这么办。', '你喜欢组队提醒。', None),
    ('刚才你说的组队提醒，行，就这么办。', '你住在北港。我提醒用户组队。', None),
    ('刚才你说的组队提醒，不，别这么办。', CLAIM, None),
    ('刚才你说的组队提醒，行，就这么办？', CLAIM, None),
    ('刚才你说的组队提醒，行，就这么办。但不要提醒。', CLAIM, '行，就这么办'),
    ('朋友说刚才你说的组队提醒，行，就这么办。', CLAIM, None),
    ('刚才你说的组队提醒，行，就这么办。我住在北港。', CLAIM, None),
    ('刚才你说的组队提醒，行，就这么办。', '我保存组队提醒。', None),
    ('刚才你说的组队提醒，行，就这么办。', '我提醒用户散步。 The quoted note says "组队".', None),
])
def test_reference_cannot_authorize_a_different_action_or_drop_denial(
    tmp_path: Path, user: str, claim: str, quote: str | None,
) -> None:
    state, rows, _ = _exercise(
        tmp_path, user, claim, quote or user, lang='zh', confirmed=True, prior_turn=True,
    )
    assert state == 'no_change'
    assert rows == []


def test_reference_matches_the_complete_topic_and_selected_verb(tmp_path: Path) -> None:
    user = '刚才你说的地图展示，好，就这么做。'
    claim = '我可以展示地图。'
    state, rows, recalled = _exercise(
        tmp_path, user, claim, user, lang='zh', confirmed=True, prior_turn=True,
    )
    assert state == 'applied'
    assert len(rows) == 1 and '地图' in recalled
