"""Review #133: a removed object is not proof that all its sources were erased."""
from pathlib import Path
import json

import pytest

from memoweft.integrations.trust.command_store import TrustCommandError
from memoweft.integrations.trust.forget_preview import preview_forget
from memoweft.integrations.trust.portable_service import PortableService
from test_trust_command_service import _open, _seed_cognition, _seed_revision, _service, _command, _T0


@pytest.mark.parametrize('order', [('a', 'b'), ('b', 'a')], ids=['a-to-b', 'b-to-a'])
@pytest.mark.parametrize('unrecoverable', [False, True], ids=['normal-source', 'rejecting-source'])
@pytest.mark.parametrize('history', [False, True], ids=['direct', 'revision-chain'])
def test_review_shared_source_keeps_every_explicit_targets_sources(
    tmp_path: Path, order: tuple[str, str], unrecoverable: bool, history: bool,
) -> None:
    path = tmp_path / 'world.sqlite'
    db = _open(path)
    try:
        _seed_cognition(db, 'a', 'ea', 'I like red tea.')
        db.execute("UPDATE evidence SET raw_content=?,summary=? WHERE id='ea'",
                   ('I like red tea. I live in York.', 'I like red tea. I live in York.'))
        _seed_cognition(db, 'b', 'eb', 'I live in York.')
        db.execute("INSERT INTO cognition_evidence VALUES ('b','ea','support')")
        _seed_cognition(db, 'keep', 'ekeep', 'I enjoy walking.')
        if history:
            _seed_cognition(db, 'b-old', 'eb-old', 'I previously lived in Leeds.')
            db.execute("INSERT INTO cognition_transitions VALUES ('b-change','b-old','b','supersedes',1)")
        _seed_revision(db)
        db.commit()
        additional = [{'target_kind': 'cognition', 'target_id': order[1]}]
        def preview() -> dict[str, object]:
            return preview_forget(str(path), 'owner', target_kind='cognition', target_id=order[0],
                                  additional_targets=additional)
        confirmed = preview()
        request = _command('batch-review', 1, 'delete_world_item', 'cognition', order[0],
                           {'forget_scope': confirmed['forget_scope'], 'additional_targets': additional})
        if unrecoverable:
            db.execute("UPDATE evidence SET deleted_at=?,origin_id=NULL WHERE id='eb'", (_T0,))
            db.execute("DELETE FROM evidence_origin_history WHERE evidence_id='eb'")
            db.commit()
            before = {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table}')]
                      for table in ['cognition', 'cognition_evidence', 'cognition_transitions', 'evidence',
                                    'world_delete_marker', 'hard_deleted_origin', 'interaction_context', 'memory_state']}
            with pytest.raises(TrustCommandError, match='source_origin_unrecoverable'):
                preview()
            # Reusing the earlier confirmation must reject and undo the whole
            # tentative cascade too, even when the failure is in its last source.
            receipt = _service(path).submit_command(request)
            assert receipt['result_state'] == 'rejected'
            assert receipt['rejection_code'] == 'source_origin_unrecoverable'
            assert receipt['affected_ids'] == []
            assert receipt['before_revision'] == receipt['after_revision'] == 1
            for table, rows in before.items():
                assert [tuple(row) for row in db.execute(f'SELECT * FROM {table}')] == rows
        else:
            expected_sources = {'ea', 'eb'} | ({'eb-old'} if history else set())
            preview_sources = confirmed['evidence_ids']
            assert isinstance(preview_sources, list)
            assert set(preview_sources) == expected_sources
            receipt = _service(path).submit_command(request)
            assert receipt['result_state'] == 'applied'
            assert receipt['before_revision'] == 1 and receipt['after_revision'] == 2
            assert expected_sources | {'a', 'b'} <= set(receipt['affected_ids'])
            assert [row[0] for row in db.execute('SELECT id FROM cognition')] == ['keep']
            assert {row[0] for row in db.execute('SELECT id FROM evidence WHERE raw_content!=""')} == {'ekeep'}
            marked = {row[0] for row in db.execute('SELECT item_id FROM world_delete_marker')}
            assert marked == {'a', 'b'} | ({'b-old'} if history else set())
            bundle = json.dumps(PortableService(path, subject_id='owner', host_id='h').export_bundle())
            assert all(text not in bundle for text in ['red tea', 'York', 'Leeds'])
            assert 'walking' in bundle
        assert _service(path).submit_command(request) == receipt
    finally:
        db.close()


def test_review_old_incomplete_batch_confirmation_cannot_authorize_expanded_sources(tmp_path: Path) -> None:
    from memoweft.integrations.trust.forget_scope import affected_scope, scope_token
    path = tmp_path / 'world.sqlite'
    db = _open(path)
    try:
        _seed_cognition(db, 'a', 'ea', 'I like red tea. I live in York.')
        _seed_cognition(db, 'b', 'eb', 'I live in York.')
        db.execute("INSERT INTO cognition_evidence VALUES ('b','ea','support')")
        _seed_revision(db)
        db.commit()
        # Frozen e836583 credential: the old preview showed only ea. There are
        # no context effects, so adding the missing eb would not change its hash.
        digest, _ = affected_scope({}, {})
        old_scope = scope_token('owner', 1, 'batch',
            json.dumps([('cognition', 'a'), ('cognition', 'b')], separators=(',', ':')), digest)
        additional = [{'target_kind': 'cognition', 'target_id': 'b'}]
        request = _command('old-confirmation', 1, 'delete_world_item', 'cognition', 'a',
                           {'forget_scope': old_scope, 'additional_targets': additional})
        receipt = _service(path).submit_command(request)
        assert receipt['result_state'] == 'rejected'
        assert receipt['rejection_code'] == 'forget_scope_changed'
        assert receipt['affected_ids'] == []
        assert receipt['after_revision'] == 1
        assert db.execute('SELECT count(*) FROM cognition').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM evidence WHERE raw_content!=""').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM world_delete_marker').fetchone()[0] == 0
        new_preview = preview_forget(str(path), 'owner', target_kind='cognition', target_id='a', additional_targets=additional)
        assert new_preview['evidence_ids'] == ['ea', 'eb']
        request['command_id'] = 'new-confirmation'
        request['payload'] = {'forget_scope': new_preview['forget_scope'], 'additional_targets': additional}
        assert _service(path).submit_command(request)['result_state'] == 'applied'
    finally:
        db.close()
