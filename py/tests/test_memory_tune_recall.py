"""Synthetic profile, source-window and erasure regressions for E2."""
from contextlib import closing
from pathlib import Path
import sqlite3
import pytest
from memoweft.integrations.hermes.recall import recall_world_snapshot
from test_semantic_recall import ForcedProvider,fixture


class SourceProvider(ForcedProvider):
    def score_texts(self,query: str,texts: list[str]) -> list[float]:
        return [0.98 if 'cobalt' in t else 0.80 for t in texts]


def test_owner_profile_reaches_nonliteral_attribute_without_encoder(tmp_path: Path) -> None:
    memories=[{'id':'person-fact','content':'My name is Ada Vale.'},
              {'id':'resources','content':'My monthly earnings are 4300 credits.'}]
    path=tmp_path/'profile.sqlite3'
    fixture.seed(path,{'memories':memories,'person_name':'Ada Vale'})
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE cognition SET content_type=CASE WHEN id='person-fact' THEN 'naming' ELSE 'attribute' END")
        db.commit()
        result=recall_world_snapshot(db,'fictional-owner','What resources does Ada Vale have?',candidate_provider=ForcedProvider([]),lang='en')
        assert result is not None and '4300 credits' in result.rendered_recall
        assert 'User profile' in result.rendered_recall
        assert ('cognition','resources') in result.selected_item_ids
        other=recall_world_snapshot(db,'fictional-owner','What resources does Bea have?',candidate_provider=ForcedProvider([]),lang='en')
        assert other is not None and 'User profile' not in other.rendered_recall


def test_unformed_live_source_and_late_source_window_are_retrievable(tmp_path: Path) -> None:
    raw='I keep a notebook. '+('Its pages are blank. '*35)+'I use cobalt glaze on my ceramic pieces.'
    memories=[{'id':'raw','content':raw}]
    path=tmp_path/'raw.sqlite3'
    fixture.seed(path,{'memories':memories})
    with closing(sqlite3.connect(path)) as db:
        db.execute("DELETE FROM cognition_evidence")
        db.execute("DELETE FROM cognition")
        db.commit()
        result=recall_world_snapshot(db,'fictional-owner','What glaze do I use?',candidate_provider=SourceProvider([]),lang='en')
        assert result is not None and 'cobalt glaze' in result.rendered_recall
        assert 'User original' in result.rendered_recall
        db.execute("UPDATE evidence SET deleted_at='2026-01-02'")
        db.commit()
        result=recall_world_snapshot(db,'fictional-owner','What glaze do I use?',candidate_provider=SourceProvider([]),lang='en')
        assert result is not None and 'cobalt' not in result.rendered_recall


@pytest.mark.parametrize('state',['forgotten','archived','muted','invalid','local_denied','cloud_denied','expired','future','cross_evidence','other_account'])
def test_profile_and_source_expansion_keep_visibility_gates(tmp_path: Path,state: str) -> None:
    memories=[{'id':'blocked','content':'My monthly earnings are 4300 credits.','state':state}]
    path=tmp_path/'gate.sqlite3'
    fixture.seed(path,{'memories':memories})
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE cognition SET content_type='attribute'")
        db.commit()
        result=recall_world_snapshot(db,'fictional-owner','What are my resources?',
            model_tier='cloud' if state=='cloud_denied' else 'local',candidate_provider=SourceProvider(memories),lang='en')
        assert result is not None and '4300' not in result.rendered_recall
