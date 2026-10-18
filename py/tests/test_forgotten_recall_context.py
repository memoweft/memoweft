"""D33: recalled assistant copies are erased, including Portable and replay."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.integrations.trust.command_service import CommandService
from memoweft.integrations.trust.forget_preview import preview_forget
from memoweft.integrations.trust.portable_service import PortableService
from test_dsh_interactions import _boundary
from test_state_goals import form


@pytest.mark.parametrize('dependency', [False, True])
def test_forget_recalled_reply_erases_whole_copy_and_portable(tmp_path: Path, dependency: bool) -> None:
    runtime = DshMemoWeftRuntime()
    runtime.initialize('s', dsh_home=str(tmp_path), subject_id='owner', auto_route=False, lang='zh')
    try:
        assert runtime.db_path is not None
        path = runtime.db_path
        prior = form(path, '我喜欢喝红茶。', 1)
        with sqlite3.connect(path) as db:
            snapshot = recall_world_snapshot(db, 'owner', '喜欢喝什么茶', lang='zh')
            assert snapshot is not None and snapshot.selected_item_ids == (('cognition', prior),)
        reply: dict[str, Any] = {'role': 'assistant', 'content': '你喜欢喝**红茶**。另外可以散步。', 'message_id': 'answer'}
        if dependency:
            reply['model_context_dependencies'] = {'schema_version': 1, 'capture_status': 'complete',
                'world_items': [{'object_kind': 'cognition', 'item_id': prior}], 'interaction_ids': []}
        boundary = _boundary('question', 'c', [{'role': 'user', 'content': '我喜欢喝什么？', 'message_id': 'question'}, reply])
        runtime.ingest_durable_boundary(boundary)
        with sqlite3.connect(path) as db:
            context_id, raw = db.execute("SELECT id,context_json FROM interaction_context WHERE conversation_id='question'").fetchone()
            if dependency:
                assert json.loads(raw)[1]['model_context_dependencies']['world_items'][0]['item_id'] == prior
            # Same text in another account must never enter this cascade.
            db.execute("INSERT INTO interaction_context SELECT 'foreign', 'other', conversation_id, episode_id, context_json, context_hash, created_at FROM interaction_context WHERE id=?", (context_id,))
        portable = PortableService(path, subject_id='owner', host_id='host')
        followup = _boundary('followup', 'd', [
            {'role': 'user', 'content': '再说说？', 'message_id': 'followup-question'},
            {'role': 'assistant', 'content': '就是刚才提到的那款饮料。', 'message_id': 'followup-answer',
             'model_context_dependencies': {'schema_version': 1, 'capture_status': 'complete',
                'world_items': [], 'interaction_ids': [context_id]}}])
        runtime.ingest_durable_boundary(followup)
        old = portable.export_bundle()
        preview = preview_forget(str(path), 'owner', target_kind='cognition', target_id=prior)
        preview_items = preview['items']
        assert isinstance(preview_items, list)
        receipt = CommandService(path, subject_id='owner', host_id='host').submit_command({
            'schema_version': 1, 'command_id': 'forget', 'subject_id': 'owner', 'actor': 'owner',
            'expected_world_revision': preview['world_revision'], 'submitted_at': '2026-10-19T00:00:00.000Z',
            'operation': 'delete_world_item', 'target_kind': 'cognition', 'target_id': prior, 'payload': {}})
        assert receipt['result_state'] == 'applied'
        exported = portable.export_bundle()
        assert '红茶' not in json.dumps(exported, ensure_ascii=False)
        assert any(item['item_id'] == context_id and item['object_kind'] == 'interaction_context' for item in preview_items)
        with sqlite3.connect(path) as db:
            assert all(not turn['content'] for turn in json.loads(db.execute(
                'SELECT context_json FROM interaction_context WHERE id=?', (context_id,)).fetchone()[0]))
            assert '红茶' in db.execute("SELECT context_json FROM interaction_context WHERE id='foreign'").fetchone()[0]
            assert db.execute("SELECT raw_content FROM evidence WHERE raw_content='我喜欢喝什么？'").fetchone()
            assert all(not turn["content"] for turn in json.loads(db.execute(
                "SELECT context_json FROM interaction_context WHERE conversation_id='followup'").fetchone()[0]))
        exported = portable.export_bundle()
        assert '红茶' not in json.dumps(exported, ensure_ascii=False)
        # Old live backup and repeated boundary cannot revive a deleted copy.
        plan = portable.plan_import(old)
        if plan['valid']:
            portable.apply_import(old, plan_hash=str(plan['plan_hash']))
        runtime.ingest_durable_boundary(boundary)
        if dependency:
            # A response that finishes after forgetting must not persist a new
            # copy merely because it has a different conversation/episode ID.
            runtime.ingest_durable_boundary(_boundary('late', 'e', [
                {'role': 'user', 'content': '继续？', 'message_id': 'late-question'}, reply]))
        assert '红茶' not in json.dumps(portable.export_bundle(), ensure_ascii=False)
        restored = PortableService(tmp_path / 'restored.sqlite', subject_id='restored', host_id='host2')
        plan = restored.plan_import(exported)
        assert plan['valid'], plan
        restored.apply_import(exported, plan_hash=str(plan['plan_hash']))
        assert '红茶' not in json.dumps(restored.export_bundle(), ensure_ascii=False)
        # Content-free provenance must still locate independent user sources
        # after restore, where original formation jobs are not carried over.
        from memoweft.integrations.trust.true_delete import erase_conversation_context
        removed = erase_conversation_context(str(tmp_path / 'restored.sqlite'), 'restored', 'question')
        assert removed['erased_evidence_count'] == 1
    finally:
        runtime.shutdown()


@pytest.mark.parametrize('answer,matched', [
    ('你喜欢喝红茶。', True), ('你喜欢喝**红茶**。另外喜欢散步。', True),
    ('小王喜欢喝红茶。', False), ('你喜欢喝绿茶。', False),
    ('红茶的产地很多。', False), ('我喜欢喝红茶。', True),
])
def test_legacy_match_uses_whole_owner_fact(tmp_path: Path, answer: str, matched: bool) -> None:
    from memoweft.store import open_db
    from memoweft.store.interaction_context import SqliteInteractionContextStore
    from memoweft.types import InteractionContextInput, VisibleTurn
    from memoweft.integrations.trust.true_delete import _redact_observed_dependencies
    db = open_db(str(tmp_path / 'world.sqlite'))
    try:
        store = SqliteInteractionContextStore(db)
        row = store.record(InteractionContextInput('owner', 's', 'e', [VisibleTurn(role='assistant', content=answer)]))
        erased = _redact_observed_dependencies(db, 'owner', {'old'}, source_texts=['我喜欢喝红茶。', '用户喜欢喝红茶。'])
        assert (row.id in erased) == matched
        current = store.get(row.id)
        assert current is not None and bool(current.context) != matched
    finally:
        db.close()


@pytest.mark.parametrize('order', ['live-first', 'erased-first'])
def test_portable_context_erasure_wins_both_import_orders(tmp_path: Path, order: str) -> None:
    from memoweft.store import open_db
    from memoweft.store.interaction_context import SqliteInteractionContextStore
    from memoweft.types import InteractionContextInput, VisibleTurn
    from memoweft.integrations.trust.true_delete import _redact_observed_dependencies
    path = tmp_path / 'source.sqlite'
    db = open_db(str(path))
    store = SqliteInteractionContextStore(db)
    row = store.record(InteractionContextInput('owner', 's', 'e', [VisibleTurn(role='assistant', content='你喜欢喝红茶。')]))
    db.commit()
    source = PortableService(path, subject_id='owner', host_id='a')
    live = source.export_bundle()
    _redact_observed_dependencies(db, 'owner', {row.id})
    db.commit()
    erased = source.export_bundle()
    db.close()
    target_path = tmp_path / 'target.sqlite'
    open_db(str(target_path)).close()
    target = PortableService(target_path, subject_id='new-owner', host_id='b')
    for bundle in ([live, erased, live] if order == 'live-first' else [erased, live]):
        plan = target.plan_import(bundle)
        assert plan['valid'], plan
        target.apply_import(bundle, plan_hash=str(plan['plan_hash']))
    assert '红茶' not in json.dumps(target.export_bundle(), ensure_ascii=False)


def test_late_dependency_link_erases_copy_and_descendant(tmp_path: Path) -> None:
    from memoweft.store import open_db
    from memoweft.store.interaction_context import SqliteInteractionContextStore
    from memoweft.types import InteractionContextInput, VisibleTurn
    db = open_db(str(tmp_path / 'world.sqlite'))
    try:
        store = SqliteInteractionContextStore(db)
        row = store.record(InteractionContextInput('owner', 's', 'e', [
            VisibleTurn(role='user', content='喜欢什么？', message_id='q'),
            VisibleTurn(role='assistant', content='你喜欢喝红茶。', message_id='a')]))
        child = store.record(InteractionContextInput('owner', 's2', 'e2', [VisibleTurn(
            role='assistant', content='就是那款饮料。', model_context_dependencies={
                'schema_version': 1, 'capture_status': 'complete', 'world_items': [], 'interaction_ids': [row.id]})]))
        db.execute("INSERT INTO world_delete_marker VALUES ('owner','cognition','old','2026-10-19T00:00:00.000Z')")
        result = store.link_dependencies(subject_id='owner', conversation_id='s', user_message_id='q',
            assistant_message_id='a', expected_context_hash=row.context_hash, dependencies={
                'schema_version': 1, 'capture_status': 'complete',
                'world_items': [{'object_kind': 'cognition', 'item_id': 'old'}], 'interaction_ids': []})
        assert result[0] == 'applied'
        for item_id in (row.id, child.id):
            current = store.get(item_id)
            assert current is not None and current.context == []
    finally:
        db.close()


def test_complete_empty_capture_is_not_legacy_text_matching(tmp_path: Path) -> None:
    from memoweft.store import open_db
    from memoweft.store.interaction_context import SqliteInteractionContextStore
    from memoweft.types import InteractionContextInput, VisibleTurn
    from memoweft.integrations.trust.true_delete import _redact_observed_dependencies
    db = open_db(str(tmp_path / 'world.sqlite'))
    try:
        store = SqliteInteractionContextStore(db)
        row = store.record(InteractionContextInput('owner', 's', 'e', [VisibleTurn(
            role='assistant', content='我喜欢喝红茶。', model_context_dependencies={
                'schema_version': 1, 'capture_status': 'complete_empty', 'world_items': [], 'interaction_ids': []})]))
        assert not _redact_observed_dependencies(db, 'owner', {'old'}, '我喜欢喝红茶。')
        assert store.get(row.id) == row
    finally:
        db.close()
