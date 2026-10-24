"""Behavioral reproductions of the three E2 privacy/deletion review findings."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Literal

import pytest

from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.integrations.dsh_bridge.interactions import _dependency_state_for_turn
from memoweft.store import open_db
from memoweft.store.interaction_context import SqliteInteractionContextStore
from memoweft.types import InteractionContextInput, VisibleTurn
from test_memory_tune_recall import SourceProvider
from test_semantic_recall import fixture
from test_state_goals import form
from test_trust_command_service import _command, _seed_evidence, _seed_revision, _service


@pytest.mark.parametrize('formed', [False, True])
@pytest.mark.parametrize('tier', ['local', 'cloud'])
@pytest.mark.parametrize('private', [
    '临时登录密码是 synthetic-pass-42，密码别记。',
    'My password is synthetic-pass-42.',
    'My password is\nsynthetic-pass-42.',
    'My API key is synthetic-key-value.',
    'My verification code is 123456.',
    '我的证件号是 110101200001010000。',
    "My private nickname is HiddenCedar; don't remember that.",
])
def test_source_window_cannot_expand_accepted_fact_into_private_content(
    tmp_path: Path, tier: Literal['local', 'cloud'], private: str, formed: bool,
) -> None:
    path = tmp_path / 'private.sqlite3'
    fact = 'I use cobalt glaze.'
    memories = [{'id': 'glaze', 'content': fact}]
    fixture.seed(path, {'memories': memories})
    with closing(sqlite3.connect(path)) as db:
        db.execute('UPDATE evidence SET raw_content=?', (fact + ' ' + private,))
        if not formed:
            db.execute('DELETE FROM cognition_evidence')
            db.execute('DELETE FROM cognition')
        db.commit()
        snapshot = recall_world_snapshot(db, 'fictional-owner', 'What glaze do I use?',
            model_tier=tier, candidate_provider=SourceProvider(memories), lang='en')
        assert snapshot is not None
        if formed:
            assert fact in snapshot.rendered_recall
        assert private not in snapshot.rendered_recall
        assert 'synthetic-pass-42' not in snapshot.rendered_recall
        assert 'HiddenCedar' not in snapshot.rendered_recall


@pytest.mark.parametrize('change', ['retract', 'correct', 'supersede'])
def test_preformation_source_dependency_follows_later_formal_state(tmp_path: Path, change: str) -> None:
    path = tmp_path / 'later-state.sqlite3'
    old = 'I use cobalt glaze.'
    with closing(open_db(str(path))) as db:
        _seed_evidence(db, 'evidence-1', old)
        db.execute("UPDATE evidence SET host_id='hermes:test'")
        _seed_revision(db, 0)
        db.commit()
    with closing(sqlite3.connect(path)) as db:
        snapshot = recall_world_snapshot(db, 'owner', '我用什么釉料？', candidate_provider=SourceProvider([]), lang='en')
        assert snapshot is not None and snapshot.selected_item_ids == (('evidence', 'evidence-1'),)
        reply = VisibleTurn(role='assistant', content='You use cobalt glaze.', model_context_dependencies={
            'schema_version': 1, 'capture_status': 'complete',
            'world_items': [{'object_kind': k, 'item_id': i} for k, i in snapshot.selected_item_ids],
            'interaction_ids': []})
        context = SqliteInteractionContextStore(db).record(InteractionContextInput('owner', 'answer', 'answer', [reply]))
        db.commit()
    cognition_id = form(path, old, 1)
    with closing(sqlite3.connect(path)) as db:
        assert _dependency_state_for_turn(db, 'owner', context, reply, {context.id: context}) == 'visible'
    if change == 'retract':
        receipt = _service(path).submit_command(_command('retract-later-formed', 1, 'retract_world_item', 'cognition', cognition_id))
        assert receipt['result_state'] == 'applied', receipt
    elif change == 'correct':
        receipt = _service(path).submit_command(_command('correct-later-formed', 1, 'correct_world_item', 'cognition', cognition_id, {'correction_text': 'I actually use copper glaze.'}))
        assert receipt['result_state'] == 'applied', receipt
    else:
        form(path, 'I now use copper glaze.', 2, prior=cognition_id)
    # Reopen the database and reload the persisted dependency, as after restart.
    with closing(sqlite3.connect(path)) as db:
        persisted = SqliteInteractionContextStore(db).get(context.id)
        assert persisted is not None
        assert _dependency_state_for_turn(db, 'owner', persisted, persisted.context[0], {persisted.id: persisted}) == 'stale'


@pytest.mark.parametrize('copies', [50, 150])
@pytest.mark.parametrize('intro', [
    'I am pasting an assistant response: ',
    "I am pasting my colleague's words: ",
    '我把助手的话贴在这里：',
    '我把同事的话贴在这里：',
])
def test_long_pasted_speech_cannot_lose_original_attribution(tmp_path: Path, intro: str, copies: int) -> None:
    class TailOnlyProvider(SourceProvider):
        def score_texts(self, query: str, texts: list[str]) -> list[float]:
            return [0.98 if 'cobalt' in text else 0.0 for text in texts]

    raw = intro + ('These are copied notes. ' * copies) + 'I use cobalt glaze.'
    path = tmp_path / 'attribution.sqlite3'
    fixture.seed(path, {'memories': [{'id': 'pasted', 'content': raw}]})
    with closing(sqlite3.connect(path)) as db:
        db.execute('DELETE FROM cognition_evidence')
        db.execute('DELETE FROM cognition')
        db.commit()
        snapshot = recall_world_snapshot(db, 'fictional-owner', 'What glaze do I use?',
            candidate_provider=TailOnlyProvider([]), lang='en')
        assert snapshot is not None
        if 'cobalt glaze' in snapshot.rendered_recall:
            assert intro in snapshot.rendered_recall


@pytest.mark.parametrize('private', [
    'My password is synthetic-pass-42.',
    'My password is\nsynthetic-pass-42.',
    'My API key is synthetic-key-value.',
    'My verification code is 123456.',
    '我的证件号是 110101200001010000。',
    "My private nickname is HiddenCedar, don't remember that.",
])
def test_formation_and_quote_window_share_source_privacy_exclusion(tmp_path: Path, private: str) -> None:
    from test_hermes_batch_adapter_v5 import _run, _set_evidence, _model, _batch
    from test_hermes_world_worker import MutableClock, _job

    path = tmp_path / 'formation-private.sqlite3'
    fact = 'I use cobalt glaze.'
    raw = fact + ' ' + private
    secret_claim = private.split(',')[0] if "don't remember" in private else private
    def item(quote: str) -> dict[str, object]:
        return {'action': 'form', 'statement_kind': 'attribute', 'formed_by': 'stated',
            'proposition': 'placeholder', 'supports': [{'evidence_id': 'evidence-1', 'quote': quote}]}
    _run(path, MutableClock(), [_model(_batch(item(fact), item(secret_claim)))], ('evidence-1',),
        lambda p: _set_evidence(p, 'evidence-1', raw))
    assert _job(path)['state'] == 'applied'
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT content FROM cognition').fetchall() == [(fact,)]
        snapshot = recall_world_snapshot(db, 'owner', 'What glaze do I use?', candidate_provider=SourceProvider([]), lang='en')
        assert snapshot is not None
        assert private not in snapshot.rendered_recall
        assert 'HiddenCedar' not in snapshot.rendered_recall
