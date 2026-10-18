"""The manager-approved boundary: hard source rules versus model semantics."""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any, Literal

import pytest

from memoweft.integrations.hermes.recall import recall_world_snapshot
from test_hermes_world_worker import _initialize_database
from test_state_goals import form
from test_state_goals_revision import attempt

@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_reconciled_relationship_rewording_supports_current_occurrence(tmp_path: Path, lang: Literal['en', 'zh']) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    names = ['Aster', 'Clover', 'Aster', 'Aster'] if lang == 'en' else ['小禾', '小岚', '小禾', '小禾']
    words = (['Aster is my partner.', 'Clover is my partner.', 'Aster and I are partners again.', 'Aster is my partner.']
             if lang == 'en' else ['小禾是我的伴侣。', '小岚是我的伴侣。', '我和小禾又成为伴侣了。', '小禾是我的伴侣。'])
    ids: list[str] = []
    for n, raw in enumerate(words, 1):
        fields: dict[str, Any] = dict(target_entity=dict(canonical_name=names[n-1], kind='person'), relation_type='partner')
        ids.append(form(path, raw, n, kind='relationship', prior=ids[-1] if 1 < n < 4 else None, fields=fields))
    assert ids[3] == ids[2] and ids[3] != ids[0]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT relationship_id FROM relationship_evidence WHERE evidence_id='evidence-4'").fetchall() == [(ids[2],)]
        assert db.execute('SELECT count(*) FROM relationship').fetchone() == (3,)
        assert db.execute('SELECT revision FROM memory_state').fetchone() == (4,)
        assert db.execute('SELECT confidence FROM relationship WHERE id=?', (ids[2],)).fetchone() == (640,)
        assert db.execute("SELECT json_extract(content,'$.relationship_id') FROM evidence_ledger "
                          "WHERE json_extract(content,'$.evidence_id')='evidence-4' "
                          "AND json_extract(content,'$.relationship_id') IS NOT NULL").fetchall() == [(ids[2],)]
        assert db.execute('SELECT prior_relationship_id,replacement_relationship_id FROM relationship_transitions ORDER BY revision').fetchall() == list(zip(ids[:2], ids[1:3]))
        assert db.execute('SELECT raw_content FROM evidence ORDER BY id').fetchall() == [(s,) for s in words]
        assert db.execute('SELECT content FROM relationship WHERE id=?', (ids[2],)).fetchone() == (words[2].replace('我', '用户', 1) if words[2].startswith('我') else words[2],)
        snapshot = recall_world_snapshot(db, 'owner', 'partner' if lang == 'en' else '伴侣', lang=lang)
        assert snapshot is not None and snapshot.selected_item_ids == (('relationship', ids[2]),)


@pytest.mark.parametrize('raw', [
    '我不想成为飞行员。', '我朋友想成为飞行员。',
    'I never want to become a pilot.', 'My friend wants to become a pilot.',
])
def test_direct_want_and_possessive_subject_are_blocked(tmp_path: Path, raw: str) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    assert attempt(path, raw)['state'] == 'no_change'


@pytest.mark.parametrize('raw,quote', [
    ('I do not plan to quit my training; my long-term goal is to become a nurse.', 'I do not plan to quit my training;'),
    ('我不打算中断训练，我的长期目标是成为护士。', '我不打算中断训练，'),
])
def test_only_supported_affirmative_clause_can_form_a_goal(tmp_path: Path, raw: str, quote: str) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    # The negative span alone cannot support an affirmative goal. Full-source
    # support contains the independent affirmative clause and must pass.
    assert attempt(path, raw, quote=quote)['state'] == 'no_change'
    assert attempt(path, raw, n=2)['state'] == 'applied'


@pytest.mark.parametrize('raw,blocked', [
    ("My goal is to support my daughter's goal of studying architecture.", True),
    ('我的目标是支持我女儿的目标。', True),
    ("My daughter's goals are to qualify as an architect and teach design.", True),
    ("My colleague's plans include completing a doctorate.", True),
])
def test_possessive_anywhere_in_goal_clause_is_excluded_by_whitelist(tmp_path: Path, raw: str, blocked: bool) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    assert attempt(path, raw)['state'] == ('no_change' if blocked else 'applied')


@pytest.mark.parametrize('raw,quote', [
    ('小禾说“我的目标是开图书馆”，我的目标是成为护士。', '我的目标是成为护士。'),
    ('Aster said "My goal is to open a library", my goal is to become a nurse.', 'my goal is to become a nurse.'),
])
def test_selected_own_goal_after_foreign_quote_keeps_its_own_source(tmp_path: Path, raw: str, quote: str) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    result = attempt(path, raw, quote=quote)
    assert result['state'] == 'applied'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT content FROM cognition').fetchall() == [(quote.replace('我', '用户', 1) if quote.startswith('我') else quote,)]
        assert db.execute('SELECT raw_content FROM evidence').fetchall() == [(raw,)]


def test_an_earlier_foreign_quote_does_not_clear_a_later_foreign_speaker(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    raw = 'Aster said "My goal is to open a library", Clover said my goal is to become a nurse.'
    assert attempt(path, raw, quote='my goal is to become a nurse.')['state'] == 'no_change'
