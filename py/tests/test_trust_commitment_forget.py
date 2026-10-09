"""FG-2: erase source/episode/conversation commitments, including disk bytes."""
import json
from pathlib import Path

import pytest

from memoweft.integrations.trust.forget_preview import preview_forget
from memoweft.integrations.trust.true_delete import erase_conversation_context
from memoweft.store.interaction_commitment import SqliteInteractionCommitmentStore
from test_trust_command_service import _open, _seed_evidence, _seed_revision, _service, _command, _SUBJECT, _T0


def _context(db: object, item_id: str, session: str, turns: list[dict[str, object]]) -> None:
    # Use the production schema while also exercising escaped Unicode JSON.
    import sqlite3
    assert isinstance(db, sqlite3.Connection)
    db.execute("INSERT INTO interaction_context VALUES (?, ?, ?, ?, ?, 'hash', ?)",
               (item_id, _SUBJECT, session, item_id, json.dumps(turns), _T0))


def _assert_no_text(path: Path, text: str) -> None:
    with _open(path) as db:
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            quoted = str(table).replace('"', '""')
            for row in db.execute(f'SELECT * FROM "{quoted}"'):
                for value in row:
                    if isinstance(value, str):
                        assert text not in value, (table, value)
                        try:
                            assert text not in json.dumps(json.loads(value), ensure_ascii=False)
                        except ValueError:
                            pass
    for file in path.parent.glob(path.name + '*'):
        assert text.encode() not in file.read_bytes()


@pytest.mark.parametrize('status', ['active', 'fulfilled', 'superseded', 'retracted'])
@pytest.mark.parametrize('kind', ['commitment', 'recommendation', 'agreement'])
def test_source_forget_erases_commitments_and_transitive_reuse_keeps_unrelated(
    tmp_path: Path, status: str, kind: str,
) -> None:
    path = tmp_path / 'source.sqlite3'
    secret = '王小明FG2原话'
    with _open(path) as db:
        _seed_evidence(db, 'source', secret)
        _context(db, 'episode-a', 'session-a', [
            {'role': 'user', 'content': secret},
            {'role': 'assistant', 'content': '我会提醒你找这个人', 'message_id': 'a'}])
        store = SqliteInteractionCommitmentStore(db)
        original = store.record(subject_id=_SUBJECT, conversation_id='session-a', episode_id='episode-a',
                                kind=kind, content='相关派生文本', raw_quote='我会提醒你找这个人', status=status)  # type: ignore[arg-type]
        _context(db, 'episode-b', 'session-b', [{'role': 'assistant', 'content': secret,
                  'model_context_dependencies': {'commitment_ids': [original.id]}}])
        reused = store.record(subject_id=_SUBJECT, conversation_id='session-b', episode_id='episode-b',
                              kind='agreement', content=secret, raw_quote=secret)
        _context(db, 'episode-keep', 'session-a', [{'role': 'assistant', 'content': '我会提醒你喝水'}])
        kept = store.record(subject_id=_SUBJECT, conversation_id='session-a', episode_id='episode-keep',
                            kind='commitment', content='喝水', raw_quote='我会提醒你喝水')
        foreign = store.record(subject_id='another-owner', conversation_id='session-a', episode_id='episode-a',
                               kind='commitment', content='other owner', raw_quote='keep other owner')
        _seed_revision(db)
        before = list(db.iterdump())
    preview = preview_forget(str(path), _SUBJECT, target_kind='evidence', target_id='source')
    items = preview['items']
    assert isinstance(items, list)
    assert {item['item_id'] for item in items} == {original.id, reused.id}
    with _open(path) as db:
        assert list(db.iterdump()) == before
    receipt = _service(path).submit_command(_command('erase', 1, 'delete_evidence', 'evidence', 'source'))
    assert receipt['storage_cleanup']['state'] == 'complete'
    assert {original.id, reused.id} <= set(receipt['affected_ids'])
    with _open(path) as db:
        assert {row[0] for row in db.execute('SELECT id FROM interaction_commitment')} == {kept.id, foreign.id}
    _assert_no_text(path, secret)


def test_conversation_without_context_erases_legacy_commitment_and_previews_it(tmp_path: Path) -> None:
    path = tmp_path / 'orphan.sqlite3'
    secret = 'FG2OrphanQuote'
    with _open(path) as db:
        item = SqliteInteractionCommitmentStore(db).record(subject_id=_SUBJECT, conversation_id='deleted',
            episode_id='missing', kind='commitment', content=secret, raw_quote=secret)
        _seed_revision(db)
        before = list(db.iterdump())
    preview = preview_forget(str(path), _SUBJECT, conversation_id='deleted')
    assert preview['item_count'] == 1
    with _open(path) as db:
        assert list(db.iterdump()) == before
    result = erase_conversation_context(str(path), _SUBJECT, 'deleted')
    assert result['result_state'] == 'applied' and result['world_revision'] == 2
    assert result['erased_commitment_count'] == 1 and item.id in result['affected_ids']  # type: ignore[operator]
    assert result['storage_cleanup'] == {'state': 'complete'}
    _assert_no_text(path, secret)
    assert erase_conversation_context(str(path), _SUBJECT, 'deleted')['result_state'] == 'no_change'


def test_source_job_recovers_commitment_when_interaction_context_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
    from memoweft.integrations.hermes.world_worker import WorldJobWorker
    from memoweft.integrations.trust.command_service import CommandService
    from memoweft.integrations.trust.revision import current_world_revision
    from test_dsh_local_route import _boundary
    monkeypatch.setattr(WorldJobWorker, 'start', lambda self: None)
    monkeypatch.setattr(WorldJobWorker, 'kick', lambda self: False)
    runtime = DshMemoWeftRuntime()
    try:
        runtime.initialize('s', dsh_home=str(tmp_path), model_tier='local', lang='zh')
        boundary = _boundary()
        runtime.ingest_durable_boundary(boundary)
        path, subject, host = runtime.db_path, runtime.subject_id, runtime.host_id
        assert path is not None and subject is not None and host is not None
        with _open(Path(path)) as db:
            source = str(db.execute('SELECT id FROM evidence').fetchone()[0])
            revision = current_world_revision(db)
            item = SqliteInteractionCommitmentStore(db).record(subject_id=subject, conversation_id='s',
                episode_id=str(boundary['event_id']), kind='commitment', content='FG2JobOnlyQuote', raw_quote='FG2JobOnlyQuote')
            db.execute('DELETE FROM interaction_context')
        preview = preview_forget(str(path), subject, target_kind='evidence', target_id=source)
        assert preview['item_count'] == 1
        receipt = CommandService(path, subject_id=subject, host_id=host).submit_command({
            'schema_version': 1, 'command_id': 'job-only-delete', 'subject_id': subject, 'actor': 'owner',
            'expected_world_revision': revision, 'operation': 'delete_evidence', 'target_kind': 'evidence',
            'target_id': source, 'payload': {}, 'submitted_at': _T0})
        assert receipt['result_state'] == 'applied' and item.id in receipt['affected_ids']
        _assert_no_text(Path(path), 'FG2JobOnlyQuote')
        with _open(Path(path)) as db:
            assert not db.execute('SELECT 1 FROM memory_world_job').fetchone()
    finally:
        runtime.shutdown()
