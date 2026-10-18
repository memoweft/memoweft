"""Independent deletion review #131: reproduce the three concrete failures."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
import pytest

from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from memoweft.integrations.trust.command_service import CommandService
from memoweft.integrations.trust.forget_preview import preview_forget
from memoweft.integrations.trust.portable_service import PortableService
from memoweft.integrations.trust.true_delete import _redact_observed_dependencies
from memoweft.store import open_db
from memoweft.store.interaction_context import SqliteInteractionContextStore, erased_context
from memoweft.types import InteractionContextInput, VisibleTurn
from test_hermes_world_worker import _initialize_database
from test_state_goals import form


def command(preview: dict[str, Any], target: str, *, command_id: str = 'forget') -> dict[str, Any]:
    return dict(schema_version=1, command_id=command_id, subject_id='owner', actor='owner',
        expected_world_revision=preview['world_revision'], operation='delete_world_item',
        target_kind='cognition', target_id=target, submitted_at='2026-10-19T00:00:00.000Z',
        payload={'forget_scope': preview['forget_scope']} if 'forget_scope' in preview else {})


def test_review_york_does_not_erase_yorkshire(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    prior = form(path, 'I live in York.', 1, kind='attribute')
    db = open_db(str(path))
    store = SqliteInteractionContextStore(db)
    unrelated = store.record(InteractionContextInput('owner', 's', 'e', [VisibleTurn(role='assistant', content='You live in Yorkshire.')]))
    db.commit()
    preview = preview_forget(str(path), 'owner', target_kind='cognition', target_id=prior)
    receipt = CommandService(path, subject_id='owner', host_id='h').submit_command(command(preview, prior))
    assert receipt['result_state'] == 'applied'
    assert store.get(unrelated.id) == unrelated
    items = preview['items']
    assert isinstance(items, list)
    assert unrelated.id not in {item['item_id'] for item in items}
    db.close()


def test_review_new_context_invalidates_preview_without_world_revision_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(WorldJobWorker, 'start', lambda self: None)
    monkeypatch.setattr(WorldJobWorker, 'kick', lambda self: False)
    runtime = DshMemoWeftRuntime()
    runtime.initialize('s', dsh_home=str(tmp_path), subject_id='owner', auto_route=False)
    try:
        assert runtime.db_path is not None and runtime._ingestor is not None
        path = runtime.db_path
        prior = form(path, 'I live in York.', 1, kind='attribute')
        preview = preview_forget(str(path), 'owner', target_kind='cognition', target_id=prior)
        runtime._ingestor.record_interaction(conversation_id='late', episode_id='late', messages=[
            {'role': 'assistant', 'content': 'You live in York.', 'source_ref': 'a'}])
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT revision FROM memory_state').fetchone()[0] == preview['world_revision']
            before = db.execute('SELECT id,context_json FROM interaction_context').fetchall()
        service = CommandService(path, subject_id='owner', host_id='h')
        receipt = service.submit_command(command(preview, prior))
        assert receipt['result_state'] == 'rejected'
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT id,context_json FROM interaction_context').fetchall() == before
            assert db.execute('SELECT 1 FROM cognition WHERE id=?', (prior,)).fetchone()
            assert db.execute('SELECT revision FROM memory_state').fetchone()[0] == preview['world_revision']
        refreshed = preview_forget(str(path), 'owner', target_kind='cognition', target_id=prior)
        assert service.submit_command(command(refreshed, prior, command_id='fresh'))['result_state'] == 'applied'
    finally:
        runtime.shutdown()


@pytest.mark.parametrize('same_copy', [True, False])
@pytest.mark.parametrize('different_message', [False, True])
def test_review_portable_same_episode_cascades_all_matched_local_ids(tmp_path: Path, same_copy: bool, different_message: bool) -> None:
    source_path, target_path = tmp_path / 'a.sqlite', tmp_path / 'b.sqlite'
    source_db, target_db = open_db(str(source_path)), open_db(str(target_path))
    source_store, target_store = SqliteInteractionContextStore(source_db), SqliteInteractionContextStore(target_db)
    inp = InteractionContextInput('owner', 's', 'e', [VisibleTurn(role='assistant', content='You live in York.')])
    b = source_store.record(inp)
    target_text = 'You live in York.' if same_copy else 'You live in Lima.'
    a = target_store.record(InteractionContextInput('target-owner', 's', 'e', [VisibleTurn(
        role='assistant', content=target_text, message_id='unrelated-message' if different_message else None)]))
    assert a.id != b.id
    child = target_store.record(InteractionContextInput('target-owner', 's-child', 'e-child', [VisibleTurn(
        role='assistant', content='Your home is York.' if same_copy else 'Your home is Lima.', model_context_dependencies={
            'schema_version': 1, 'capture_status': 'complete', 'world_items': [], 'interaction_ids': [a.id]})]))
    _redact_observed_dependencies(source_db, 'owner', {b.id})
    source_db.commit()
    target_db.commit()
    bundle = PortableService(source_path, subject_id='owner', host_id='a').export_bundle()
    target = PortableService(target_path, subject_id='target-owner', host_id='b')
    plan = target.plan_import(bundle)
    assert plan['valid'], plan
    target.apply_import(bundle, plan_hash=str(plan['plan_hash']))
    for item_id in (a.id, child.id):
        row = target_store.get(item_id)
        assert row is not None and erased_context(row.context) == (same_copy and not different_message)
    if same_copy and not different_message:
        assert 'York' not in json.dumps(target.export_bundle())
    else:
        assert target_store.get(a.id) == a and target_store.get(child.id) == child
    source_db.close()
    target_db.close()


@pytest.mark.parametrize('source,answer', [
    ('I live in York.', 'You live in Yorkshire.'),
    ('I live in New York.', 'You live in NewYork.'),
    ('I earn 1.5 dollars.', 'You earn 1.6 dollars.'),
    ('I like tea.', 'You like tea, but not anymore.'),
    ('我喜欢红茶。', '你喜欢红茶拿铁。'),
])
def test_legacy_match_preserves_distinct_values_and_qualifications(tmp_path: Path, source: str, answer: str) -> None:
    db = open_db(str(tmp_path / 'world.sqlite'))
    try:
        store = SqliteInteractionContextStore(db)
        row = store.record(InteractionContextInput('owner', 's', 'e', [VisibleTurn(role='assistant', content=answer)]))
        assert _redact_observed_dependencies(db, 'owner', {'old'}, source_texts=[source]) == set()
        assert store.get(row.id) == row
    finally:
        db.close()


@pytest.mark.parametrize('change', ['missing', 'content', 'foreign', 'unrelated', 'target'])
def test_forget_scope_checks_subject_target_content_and_missing_confirmation(tmp_path: Path, change: str) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    prior = form(path, 'I live in York.', 1, kind='attribute')
    db = open_db(str(path))
    store = SqliteInteractionContextStore(db)
    row = store.record(InteractionContextInput('owner', 's', 'e', [VisibleTurn(role='assistant', content='You live in York.')]))
    db.commit()
    preview = preview_forget(str(path), 'owner', target_kind='cognition', target_id=prior)
    request = command(preview, prior)
    if change == 'missing':
        request['payload'] = {}
    elif change == 'content':
        # Same ID and even the old context_hash: the digest binds actual content.
        db.execute('UPDATE interaction_context SET context_json=? WHERE id=?',
                   (json.dumps([{'role': 'assistant', 'content': 'You live in York. Another sentence.'}]), row.id))
        db.commit()
    elif change in {'foreign', 'unrelated'}:
        store.record(InteractionContextInput('other' if change == 'foreign' else 'owner', 'new-s', 'new-e',
            [VisibleTurn(role='assistant', content='You live in York.' if change == 'foreign' else 'You like tacos.')]))
        db.commit()
    else:
        wrong = preview_forget(str(path), 'owner', target_kind='evidence', target_id='evidence-1')
        request['payload'] = {'forget_scope': wrong['forget_scope']}
    before = list(db.execute('SELECT id,context_json FROM interaction_context ORDER BY id'))
    service = CommandService(path, subject_id='owner', host_id='h')
    result = service.submit_command(request)
    assert result == service.submit_command(request)
    if change in {'foreign', 'unrelated'}:
        assert result['result_state'] == 'applied'
        assert db.execute("SELECT context_json FROM interaction_context WHERE conversation_id='new-s'").fetchone()[0] != '[]'
    else:
        assert result['result_state'] == 'rejected'
        assert result['affected_ids'] == []
        assert result['before_revision'] == result['after_revision'] == preview['world_revision']
        assert list(db.execute('SELECT id,context_json FROM interaction_context ORDER BY id')) == before
        assert db.execute('SELECT 1 FROM cognition WHERE id=?', (prior,)).fetchone()
        assert db.execute('SELECT COUNT(*) FROM world_delete_marker').fetchone()[0] == 0
    db.close()


def test_scope_guard_rolls_back_source_and_receipt_on_context_write_failure(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    prior = form(path, 'I live in York.', 1, kind='attribute')
    db = open_db(str(path))
    store = SqliteInteractionContextStore(db)
    row = store.record(InteractionContextInput('owner', 's', 'e', [VisibleTurn(role='assistant', content='You live in York.')]))
    db.commit()
    preview = preview_forget(str(path), 'owner', target_kind='cognition', target_id=prior)
    db.execute("CREATE TRIGGER fail_context BEFORE UPDATE ON interaction_context BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    db.commit()
    with pytest.raises(sqlite3.DatabaseError, match='injected failure'):
        CommandService(path, subject_id='owner', host_id='h').submit_command(command(preview, prior))
    assert store.get(row.id) == row
    assert db.execute('SELECT 1 FROM cognition WHERE id=?', (prior,)).fetchone()
    assert db.execute("SELECT 1 FROM trust_command_receipt WHERE command_id='forget'").fetchone() is None
    assert db.execute('SELECT revision FROM memory_state').fetchone()[0] == preview['world_revision']
    db.close()
