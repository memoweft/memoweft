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


@pytest.mark.parametrize("operation", ["delete_world_item", "delete_evidence", "retract_world_item"])
@pytest.mark.parametrize("tier", ["local", "cloud"])
def test_real_forget_or_retract_removes_profile_and_original_windows(tmp_path: Path, operation: str, tier: str) -> None:
    from typing import cast
    from memoweft.types import ModelTier
    from memoweft.store import open_db
    from test_trust_command_service import _seed_cognition, _seed_revision, _service, _command

    path = tmp_path / 'erase.sqlite3'
    raw = 'My monthly earnings are 4300 credits.'
    with closing(open_db(str(path))) as db:
        _seed_cognition(db, content=raw)
        db.execute("UPDATE cognition SET content_type='attribute'")
        _seed_revision(db)
        db.commit()
    provider = SourceProvider([{'id': 'cog-coffee', 'content': raw}])
    with closing(sqlite3.connect(path)) as db:
        before = recall_world_snapshot(db, 'owner', 'What are my resources?',
            model_tier=cast(ModelTier, tier), candidate_provider=provider, lang='en')
        assert before is not None
        assert 'User profile' in before.rendered_recall
        assert 'User original' in before.rendered_recall
        assert raw in before.rendered_recall
    kind, target = ('evidence', 'e-coffee') if operation == 'delete_evidence' else ('cognition', 'cog-coffee')
    receipt = _service(path).submit_command(_command('erase-context', 1, operation, kind, target))
    assert receipt['result_state'] == 'applied', receipt
    with closing(sqlite3.connect(path)) as db:
        after = recall_world_snapshot(db, 'owner', 'What are my resources?',
            model_tier=cast(ModelTier, tier), candidate_provider=provider, lang='en')
        assert after is not None
        assert after.rendered_recall == ''
        assert after.selected_item_ids == ()
        assert after.recall_snapshot_token != before.recall_snapshot_token


@pytest.mark.parametrize('formed', [False, True])
def test_quoted_source_dependencies_erase_recalled_reply_and_fence_late_copy(tmp_path: Path, formed: bool) -> None:
    from memoweft.store import open_db
    from memoweft.store.interaction_context import SqliteInteractionContextStore, erased_context
    from memoweft.types import InteractionContextInput, VisibleTurn
    from memoweft.integrations.trust.forget_preview import preview_forget
    from test_trust_command_service import _seed_cognition, _seed_revision, _service, _command

    path = tmp_path / 'quote-dependency.sqlite3'
    raw = 'I use cobalt glaze on my ceramic pieces.'
    with closing(open_db(str(path))) as db:
        _seed_cognition(db, content=raw)
        if not formed:
            db.execute('DELETE FROM cognition_evidence')
            db.execute('DELETE FROM cognition')
        _seed_revision(db)
        db.commit()
    with closing(sqlite3.connect(path)) as db:
        # No semantic formal nominee; the quote can be the only returned text.
        snapshot = recall_world_snapshot(db, 'owner', '我需要哪些用品？',
            candidate_provider=SourceProvider([]), lang='en')
        assert snapshot is not None and raw in snapshot.rendered_recall
        assert 'Memory [' not in snapshot.rendered_recall
        assert ('evidence', 'e-coffee') in snapshot.selected_item_ids
        dependencies: dict[str, object] = {'schema_version': 1,
            'capture_status': 'complete' if snapshot.selected_item_ids else 'complete_empty',
            'world_items': [{'object_kind': kind, 'item_id': item_id} for kind, item_id in snapshot.selected_item_ids],
            'interaction_ids': []}
        store = SqliteInteractionContextStore(db)
        reply = VisibleTurn(role='assistant', content='You use cobalt glaze.', model_context_dependencies=dependencies)
        context = store.record(InteractionContextInput('owner', 'answer', 'answer', [reply]))
        foreign = store.record(InteractionContextInput('other', 'answer', 'answer', [reply]))
        db.commit()
    from memoweft.integrations.trust.portable_service import PortableService
    portable = PortableService(path, subject_id='owner', host_id='host')
    old_bundle = portable.export_bundle()
    if formed:
        from memoweft.integrations.dsh_bridge.interactions import _dependency_state_for_turn
        receipt = _service(path).submit_command(_command('retract-quoted-fact', 1, 'retract_world_item', 'cognition', 'cog-coffee'))
        assert receipt['result_state'] == 'applied', receipt
        with closing(sqlite3.connect(path)) as db:
            assert _dependency_state_for_turn(db, 'owner', context, reply, {context.id: context}) == 'stale'
            retracted = recall_world_snapshot(db, 'owner', '我需要哪些用品？', candidate_provider=SourceProvider([]), lang='en')
            assert retracted is not None and 'cobalt' not in retracted.rendered_recall
    preview = preview_forget(str(path), 'owner', target_kind='evidence', target_id='e-coffee')
    revision = preview['world_revision']
    assert isinstance(revision, int)
    receipt = _service(path).submit_command(_command('forget-quoted-source', revision, 'delete_evidence', 'evidence', 'e-coffee',
        {'forget_scope': preview['forget_scope']}))
    assert receipt['result_state'] == 'applied', receipt
    with closing(sqlite3.connect(path)) as db:
        store = SqliteInteractionContextStore(db)
        current = store.get(context.id)
        assert current is not None and erased_context(current.context)
        foreign_current = store.get(foreign.id)
        assert foreign_current is not None and not erased_context(foreign_current.context)
        late = store.record(InteractionContextInput('owner', 'late', 'late', [reply]))
        assert erased_context(late.context)
        after = recall_world_snapshot(db, 'owner', '我需要哪些用品？', candidate_provider=SourceProvider([]), lang='en')
        assert after is not None and 'cobalt' not in after.rendered_recall
        db.commit()
    assert 'cobalt' not in str(portable.export_bundle())
    plan = portable.plan_import(old_bundle)
    if plan['valid']:
        portable.apply_import(old_bundle, plan_hash=str(plan['plan_hash']))
    assert 'cobalt' not in str(portable.export_bundle())


@pytest.mark.parametrize('state,tier,expected', [
    ('live', 'local', 'visible'), ('live', 'cloud', 'visible'),
    ('foreign', 'local', 'missing'), ('local_denied', 'local', 'stale'),
    ('cloud_denied', 'cloud', 'stale'), ('deleted', 'local', 'stale'),
])
def test_quoted_evidence_dependencies_keep_account_and_permission_gates(tmp_path: Path, state: str, tier: str, expected: str) -> None:
    from typing import cast
    from memoweft.types import InteractionContextInput, VisibleTurn, ModelTier
    from memoweft.store import open_db
    from memoweft.store.interaction_context import SqliteInteractionContextStore
    from memoweft.integrations.dsh_bridge.interactions import _dependency_state_for_turn
    from test_trust_command_service import _seed_evidence

    with closing(open_db(str(tmp_path / 'dependency-gates.sqlite3'))) as db:
        _seed_evidence(db, 'quoted', 'I use cobalt glaze.')
        reply = VisibleTurn(role='assistant', content='You use cobalt glaze.', model_context_dependencies={
            'schema_version': 1, 'capture_status': 'complete',
            'world_items': [{'object_kind': 'evidence', 'item_id': 'quoted'}], 'interaction_ids': []})
        context = SqliteInteractionContextStore(db).record(InteractionContextInput('owner', 'reply', 'reply', [reply]))
        if state == 'foreign':
            db.execute("UPDATE evidence SET subject_id='other'")
        elif state == 'local_denied':
            db.execute('UPDATE evidence SET allow_local_read=0')
        elif state == 'cloud_denied':
            db.execute('UPDATE evidence SET allow_cloud_read=0')
        elif state == 'deleted':
            db.execute("UPDATE evidence SET deleted_at='2026-01-01'")
        assert _dependency_state_for_turn(db, 'owner', context, reply, {context.id: context}, cast(ModelTier, tier)) == expected
