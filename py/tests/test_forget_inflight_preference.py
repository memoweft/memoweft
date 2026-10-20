"""B1 M3: a model already reading the old preference must settle before forgetting."""
from __future__ import annotations
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any
import pytest
from test_recent_evidence import _runtime, _ingest, _recent, _db_path, _subject
from test_hermes_batch_adapter_v5 import _batch, _model
from memoweft.integrations.hermes.batch_adapter import HermesBatchAdapterProcessor
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from memoweft.integrations.trust.command_service import CommandService
from memoweft.integrations.trust.command_store import TrustCommandError
from memoweft.integrations.trust.forget_preview import preview_forget

@pytest.mark.parametrize('transition', ['correct', 'supersedes'])
@pytest.mark.parametrize('entrypoint', ['preview', 'execute_old_preview', 'covered_batch'])
def test_forget_waits_for_inflight_preference_revision(tmp_path: Path, transition: str, entrypoint: str) -> None:
    runtime = _runtime(tmp_path)
    reading, finish = Event(), Event()
    def route(messages: Any, *, session_id: str) -> dict[str, object]:
        payload = json.loads(messages[-1]['content'])
        evidence = payload['evidence'][0]
        item: dict[str, Any] = {'action': 'form', 'target': 'owner_self', 'statement_kind': 'preference',
            'formed_by': 'stated', 'proposition': evidence['text'],
            'supports': [{'evidence_id': evidence['id'], 'sentence_id': 't0'}]}
        if '绿茶' in evidence['text']:
            # Frozen B1 response shapes, with fixture-local IDs. Both real runs
            # selected the predecessor before the owner erased that predecessor.
            if transition == 'correct':
                item['action'] = 'correct'
                item['corrects_cognition_id'] = payload['current_cognitions'][0]['id']
            else:
                item['supersedes_cognition_id'] = payload['current_cognitions'][0]['id']
            reading.set()
            assert finish.wait(15), 'test must release the bounded model response'
        return _model(_batch(item))
    try:
        _ingest(runtime, '记住我喜欢喝红茶', 'a', 'red')
        path, subject = _db_path(runtime), _subject(runtime)
        worker = WorldJobWorker(path, processor=HermesBatchAdapterProcessor(str(path), route, model_tier='local'))
        assert worker.run_until_quiescent() == 1
        with sqlite3.connect(path) as db:
            target = str(db.execute('SELECT id FROM cognition').fetchone()[0])
        original = preview_forget(str(path), subject, target_kind='cognition', target_id=target)
        def command(preview: dict[str, Any], request: str) -> dict[str, Any]:
            return dict(schema_version=1,command_id=request,subject_id=subject,actor='owner',
                expected_world_revision=preview['world_revision'],operation='delete_world_item',
                target_kind='cognition',target_id=target,submitted_at='2026-10-20T00:00:00.000Z',
                payload={'forget_scope':preview['forget_scope']})
        service = CommandService(path, subject_id=subject, host_id='test')
        _ingest(runtime, '我其实更喜欢绿茶', 'b', 'green')
        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(worker.run_until_quiescent)
            try:
                assert reading.wait(10)
                if entrypoint == 'preview':
                    with pytest.raises(TrustCommandError, match='formation_in_progress'):
                        preview_forget(str(path), subject, target_kind='cognition', target_id=target)
                elif entrypoint == 'covered_batch':
                    with sqlite3.connect(path) as db:
                        green_id = str(db.execute("SELECT id FROM evidence WHERE raw_content='我其实更喜欢绿茶'").fetchone()[0])
                    extra = [{'target_kind': 'evidence', 'target_id': green_id}]
                    whole = preview_forget(str(path), subject, target_kind='cognition', target_id=target, additional_targets=extra)
                    payload = command(whole, 'covered')
                    payload['payload']['additional_targets'] = extra
                    assert service.submit_command(payload)['result_state'] == 'applied'
                else:
                    receipt = service.submit_command(command(original, 'old-preview'))
                    assert receipt['result_state'] == 'rejected', receipt
                    assert receipt['rejection_code'] == 'formation_in_progress'
                if entrypoint != 'covered_batch':
                    with sqlite3.connect(path) as db:
                        assert db.execute('SELECT 1 FROM cognition WHERE id=?',(target,)).fetchone()
                        assert db.execute("SELECT count(*) FROM evidence WHERE deleted_at IS NOT NULL").fetchone()[0] == 0
            finally:
                finish.set()
                task.result(timeout=15)
        if entrypoint != 'covered_batch':
            fresh = preview_forget(str(path), subject, target_kind='cognition', target_id=target)
            assert fresh['world_revision'] == 2
            assert service.submit_command(command(fresh, 'fresh-preview'))['result_state'] == 'applied'
        assert _recent(runtime, '我喜欢喝什么？') == []
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT count(*) FROM cognition').fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM evidence WHERE raw_content LIKE '%茶%' AND deleted_at IS NULL").fetchone()[0] == 0
    finally:
        finish.set()
        runtime.shutdown()
