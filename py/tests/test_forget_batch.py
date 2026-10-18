"""One confirmed topic can include a fact and an independent failed source (#132)."""
from pathlib import Path
from typing import Any
import json
import pytest

from memoweft.integrations.hermes.boundary_store import HermesBoundaryStore
from memoweft.integrations.trust.command_service import CommandService
from memoweft.integrations.trust.forget_preview import preview_forget
from memoweft.store import open_db
from test_hermes_boundary_store import _boundary, _candidate
from test_hermes_world_worker import _initialize_database
from test_state_goals import form
from test_context_erasure_review import command as single_command
from memoweft.integrations.trust.command_store import TrustCommandError
from memoweft.integrations.trust.portable_service import PortableService


def test_one_confirmation_deletes_fact_and_independent_source_atomically(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    target = form(path, 'I like red tea.', 1)
    db = open_db(str(path))
    try:
        HermesBoundaryStore(db).accept(_boundary(_candidate('duplicate', raw_content='I now like green tea.',
            preceding_ai_context='independent assistant context')))
        eid = db.execute("SELECT id FROM evidence WHERE raw_content='I now like green tea.'").fetchone()[0]
        additional = [{'target_kind': 'evidence', 'target_id': eid}]
        preview = preview_forget(str(path), 'owner', target_kind='cognition', target_id=target,
                                 additional_targets=additional)
        assert eid in preview['evidence_ids']  # type: ignore[operator]
        command: dict[str, Any] = dict(schema_version=1, command_id='one-confirmation', subject_id='owner',
            actor='owner', expected_world_revision=preview['world_revision'], operation='delete_world_item',
            target_kind='cognition', target_id=target, submitted_at='2026-10-19T00:00:00.000Z',
            payload={'forget_scope': preview['forget_scope'], 'additional_targets': additional})
        service = CommandService(path, subject_id='owner', host_id='synthetic')
        receipt = service.submit_command(command)
        assert receipt['result_state'] == 'applied'
        assert receipt['after_revision'] == receipt['before_revision'] + 1
        assert {target, eid} <= set(receipt['affected_ids'])
        assert not db.execute('SELECT 1 FROM cognition WHERE id=?', (target,)).fetchone()
        assert db.execute('SELECT raw_content,preceding_ai_context FROM evidence WHERE id=?', (eid,)).fetchone() == ('', None)
        assert service.submit_command(command) == receipt
        assert 'red tea' not in json.dumps(PortableService(path, subject_id='owner', host_id='h').export_bundle())
        assert 'green tea' not in json.dumps(PortableService(path, subject_id='owner', host_id='h').export_bundle())
    finally:
        db.close()


@pytest.mark.parametrize('change', ['late-context', 'changed-context', 'scope-missing', 'scope-single',
                                    'target-swapped', 'target-added', 'revision', 'other-owner', 'later-rejects'])
def test_batch_rejection_rolls_back_every_target(tmp_path: Path, change: str) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    first = form(path, 'I live in York.', 1, kind='attribute')
    second = form(path, 'I like green tea.', 2)
    third = form(path, 'I enjoy walking.', 3)
    additional = [{'target_kind': 'cognition', 'target_id': second}]
    preview = preview_forget(str(path), 'owner', target_kind='cognition', target_id=first, additional_targets=additional)
    request = single_command(preview, first)
    request['payload']['additional_targets'] = additional
    db = open_db(str(path))
    try:
        if change in {'late-context', 'changed-context'}:
            HermesBoundaryStore(db).accept(_boundary(_candidate('independent', raw_content='I like bicycles.',
                preceding_ai_context=json.dumps([{'role': 'assistant', 'content': 'You like green tea.'}]))))
            if change == 'changed-context':
                preview = preview_forget(str(path), 'owner', target_kind='cognition', target_id=first, additional_targets=additional)
                request['payload']['forget_scope'] = preview['forget_scope']
                db.execute("UPDATE evidence SET preceding_ai_context=? WHERE raw_content='I like bicycles.'",
                           (json.dumps([{'role': 'assistant', 'content': 'You like green tea. You like parks.'}]),))
        elif change == 'scope-missing':
            del request['payload']['forget_scope']
        elif change == 'scope-single':
            request['payload']['forget_scope'] = preview_forget(str(path), 'owner', target_kind='cognition', target_id=first)['forget_scope']
        elif change == 'target-swapped':
            request['payload']['additional_targets'] = [{'target_kind': 'cognition', 'target_id': third}]
        elif change == 'target-added':
            request['payload']['additional_targets'] = additional + [{'target_kind': 'cognition', 'target_id': third}]
        elif change == 'revision':
            db.execute('UPDATE memory_state SET revision=revision+1')
        elif change == 'other-owner':
            db.execute("UPDATE cognition SET subject_id='foreign' WHERE id=?", (second,))
        elif change == 'later-rejects':
            db.execute("UPDATE evidence SET deleted_at='2026-10-19T00:00:00.000Z',origin_id=NULL WHERE id='evidence-2'")
            db.execute("DELETE FROM evidence_origin_history WHERE evidence_id='evidence-2'")
        db.commit()
        before = {table: db.execute(f'SELECT * FROM {table}').fetchall()
                  for table in ['cognition', 'evidence', 'world_delete_marker', 'memory_state']}
        receipt = CommandService(path, subject_id='owner', host_id='h').submit_command(request)
        assert receipt['result_state'] in {'rejected', 'revision_conflict'}
        assert receipt['affected_ids'] == []
        for table, rows in before.items():
            assert db.execute(f'SELECT * FROM {table}').fetchall() == rows
    finally:
        db.close()


@pytest.mark.parametrize('additional', [[], None, [{}], [{'target_kind': 'cognition', 'target_id': 'same'}],
    [{'target_kind': 'unknown', 'target_id': 'x'}], [{'target_kind': 'evidence', 'target_id': ''}],
    [{'target_kind': 'evidence', 'target_id': str(n)} for n in range(32)]])
def test_batch_rejects_invalid_targets(tmp_path: Path, additional: Any) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    request = single_command({'world_revision': 0}, 'same')
    request['payload']['additional_targets'] = additional
    with pytest.raises(TrustCommandError, match='invalid_forget_targets'):
        CommandService(path, subject_id='owner', host_id='h').submit_command(request)


def test_batch_overlapping_chain_preserves_unrelated_fact(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    old = form(path, 'I like red tea.', 1)
    new = form(path, 'I now like green tea.', 2, prior=old)
    keep = form(path, 'I enjoy walking.', 3)
    targets = [{'target_kind': 'cognition', 'target_id': old}, {'target_kind': 'evidence', 'target_id': 'evidence-1'}]
    preview = preview_forget(str(path), 'owner', target_kind='cognition', target_id=new, additional_targets=targets)
    request = single_command(preview, new)
    request['payload']['additional_targets'] = targets
    receipt = CommandService(path, subject_id='owner', host_id='h').submit_command(request)
    assert receipt['result_state'] == 'applied'
    db = open_db(str(path))
    try:
        assert db.execute('SELECT id FROM cognition').fetchall() == [(keep,)]
        assert db.execute('SELECT raw_content FROM evidence WHERE id=?', ('evidence-3',)).fetchone() == ('I enjoy walking.',)
    finally:
        db.close()


def test_rpc_batch_preview_and_single_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from memoweft.integrations.dsh_bridge.protocol_v2 import DshRpcV2Server
    from memoweft.integrations.hermes.world_worker import WorldJobWorker
    from test_dsh_trust_rpc import _initialize, _request, _message_boundary
    monkeypatch.setattr(WorldJobWorker, 'start', lambda self: None)
    monkeypatch.setattr(WorldJobWorker, 'kick', lambda self: False)
    server = DshRpcV2Server()
    _initialize(server, tmp_path)
    try:
        for n in range(2):
            assert server.handle(_request(f'accept-{n}', 'ingest_boundary', {
                'boundary': _message_boundary(f'message-{n}', f'synthetic source {n}')}))['ok']
        assert server.runtime.db_path is not None
        db = open_db(str(server.runtime.db_path))
        ids = [r[0] for r in db.execute('SELECT id FROM evidence ORDER BY id')]
        db.close()
        targets = [{'target_kind': 'evidence', 'target_id': ids[1]}]
        result = server.handle(_request('preview', 'preview_forget', {
            'target_kind': 'evidence', 'target_id': ids[0], 'additional_targets': targets}))
        assert result['ok'], result
        preview = result['result']
        assert isinstance(preview, dict)
        assert preview['evidence_ids'] == ids
        request = single_command(preview, ids[0])
        request.update(subject_id=server.runtime.subject_id, target_kind='evidence', operation='delete_evidence')
        request['payload']['additional_targets'] = targets
        deleted = server.handle(_request('delete', 'submit_command', {'command': request}))
        assert deleted['ok'], deleted
        receipt = deleted['result']
        assert isinstance(receipt, dict)
        assert receipt['receipt']['result_state'] == 'applied'
        assert set(ids) <= set(receipt['receipt']['affected_ids'])
    finally:
        server.runtime.shutdown()
