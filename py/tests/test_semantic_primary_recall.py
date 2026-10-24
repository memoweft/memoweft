"""Experiment C: ranking and exact-source safety on synthetic data."""
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.semantic.runtime import SemanticCandidate
from memoweft.semantic.store import content_hash
from test_semantic_recall import ForcedProvider, fixture


class RankedProvider(ForcedProvider):
    def score_texts(self, query: str, texts: list[str]) -> list[float]:
        return [0.96 if 'orchards' in text else 0.88 for text in texts]


def test_semantics_outrank_literal_without_overlap(tmp_path: Path) -> None:
    memories = [{'id': 'literal', 'content': 'I spend weekends organizing weekend calendars.'},
                {'id': 'meaning', 'content': 'I visit orchards to pick ripe fruit.'}]
    path = tmp_path / 'rank.sqlite3'
    fixture.seed(path, {'memories': memories})
    provider = RankedProvider([])
    provider.items = tuple(SemanticCandidate('cognition', m['id'], score, content_hash(m['content']))
                           for m, score in zip(memories, (0.88, 0.96)))
    with closing(sqlite3.connect(path)) as db:
        result = recall_world_snapshot(db, 'fictional-owner', 'How do I spend weekends?',
                                       candidate_provider=provider, lang='en')
        assert result is not None
        assert result.selected_item_ids[0] == ('cognition', 'meaning')
        assert 'User original' in result.rendered_recall
        assert '2026-01-01' in result.rendered_recall


@pytest.mark.parametrize('state', ['forgotten', 'archived', 'muted', 'invalid', 'local_denied',
                                 'cloud_denied', 'expired', 'future', 'cross_evidence', 'other_account'])
def test_original_quote_cannot_bypass_visibility(tmp_path: Path, state: str) -> None:
    memories = [{'id': 'blocked', 'content': 'I visit orchards to pick ripe fruit.', 'state': state}]
    path = tmp_path / 'quote.sqlite3'
    fixture.seed(path, {'memories': memories})
    with closing(sqlite3.connect(path)) as db:
        result = recall_world_snapshot(db, 'fictional-owner', 'Where do I harvest produce?',
            model_tier='cloud' if state == 'cloud_denied' else 'local',
            candidate_provider=RankedProvider(memories), lang='en')
        assert result is not None
        assert 'orchards' not in result.rendered_recall


def test_exact_quote_budget_and_deleted_source(tmp_path: Path) -> None:
    memories = [{'id': f'm-{i}', 'content': 'I visit orchards. ' + 'fruit ' * 80} for i in range(8)]
    path = tmp_path / 'budget.sqlite3'
    fixture.seed(path, {'memories': memories})
    with closing(sqlite3.connect(path)) as db:
        result = recall_world_snapshot(db, 'fictional-owner', 'Where do I harvest produce?',
                                       candidate_provider=RankedProvider(memories), lang='en')
        assert result is not None
        assert 0 < result.rendered_recall.count('User original [') <= 3
        assert len(result.rendered_recall) <= 2400
        db.execute("UPDATE evidence SET deleted_at='2026-01-02'")
        db.commit()
        result = recall_world_snapshot(db, 'fictional-owner', 'Where do I harvest produce?',
                                       candidate_provider=RankedProvider(memories), lang='en')
        assert result is not None and result.rendered_recall == ''


def test_owner_name_reaches_other_direct_self_predicates_only(tmp_path: Path) -> None:
    memories = [{'id': 'person-fact', 'content': 'My name is Ada Vale.'},
                {'id': 'self', 'content': 'I keep a sketchbook in my travel bag.'},
                {'id': 'other', 'content': 'I think they keep a sketchbook in their travel bag.'}]
    path = tmp_path / 'owner.sqlite3'
    fixture.seed(path, {'memories': memories, 'person_name': 'Ada Vale'})
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE cognition SET content_type='naming' WHERE id='person-fact'")
        db.commit()
        result = recall_world_snapshot(db, 'fictional-owner', 'What does Ada Vale carry when traveling?',
                                       candidate_provider=RankedProvider(memories), lang='en')
        assert result is not None
        assert ('cognition', 'self') in result.selected_item_ids
        assert ('cognition', 'other') not in result.selected_item_ids
        assert 'they keep' not in result.rendered_recall
