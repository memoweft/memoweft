"""One real message is one source across explicit correction and turn formation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from memoweft.integrations.dsh_bridge import _candidates
from memoweft.integrations.hermes.batch_adapter import HermesBatchAdapterProcessor
from memoweft.integrations.hermes.boundary_store import HermesBoundaryStore
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from memoweft.integrations.trust.command_store import TrustCommandError
from memoweft.portable.builder import build_bundle
from test_hermes_boundary_store import _boundary, _target
from test_hermes_batch_adapter_v5 import _batch, _model, _route
from test_hermes_world_worker import _policy
from test_trust_command_service import _open, _seed_cognition, _seed_revision, _service, _command, _clock, _SUBJECT, _HOST, _T0


def _prepare(path: Path) -> Any:
    with _open(path) as db:
        _seed_cognition(db, 'old-tea', 'old-source', '用户喜欢红茶。')
        _seed_revision(db)
    return _service(path)


def _source(raw: str = '我其实更喜欢绿茶。请纠正我喝茶的偏好。') -> dict[str, object]:
    return {'message_id': 'real-user-message', 'session_id': 'real-session', 'content': raw,
            'quote': '我其实更喜欢绿茶。', 'preceding_ai_context': None}


def _proposal(source: dict[str, object] | None = None, *, command_id: str = 'correct-source') -> dict[str, Any]:
    command = dict(_command(command_id, 1, 'correct_world_item', 'cognition', 'old-tea'))
    command['payload'] = {'correction_text': '我更喜欢绿茶。', **({'source': source} if source is not None else {})}
    return command


@pytest.mark.parametrize('mixed', [False, True])
@pytest.mark.parametrize('source_first', [False, True])
def test_one_real_source_is_reused_without_a_duplicate_preference_and_other_facts_survive(tmp_path: Path, mixed: bool, source_first: bool) -> None:
    path = tmp_path / 'source.sqlite3'
    service = _prepare(path)
    raw = '我其实更喜欢绿茶。' + ('我的生日是五月一日。' if mixed else '请纠正我喝茶的偏好。')
    source = _source(raw)
    source.update(timestamp=1787000000, preceding_ai_context='真实助手前文')
    candidates = _candidates([{'role': 'assistant', 'content': '真实助手前文'},
        {'role': 'user', 'content': raw, 'message_id': 'real-user-message', 'timestamp': 1787000000}],
        session_id='real-session', subject_id=_SUBJECT, host_id=_HOST, boundary_id='real-turn')
    boundary = _boundary(*candidates, event_id='real-turn',
        target=_target(parent_session_id='real-session', result_session_id='real-session', subject_id=_SUBJECT, host_id=_HOST))
    if source_first:
        with _open(path) as db:
            HermesBoundaryStore(db, clock=_clock).accept(boundary)
    # On the old implementation this follows the observed legacy route and
    # deterministically reproduces two current green-tea records. New hosts
    # require the advertised source capability rather than taking this fallback.
    command = _proposal(source if service.get_capabilities().get('correction_source') is True else None)
    receipt = service.submit_command(command)
    assert receipt['result_state'] == 'applied'
    assert service.submit_command(command) == receipt
    with _open(path) as db:
        accepted = HermesBoundaryStore(db, clock=_clock).accept(boundary)
        evidence_id = str(db.execute('SELECT id FROM evidence WHERE origin_id=?', (candidates[0].origin_id,)).fetchone()[0])
    item = {'action': 'form', 'target': 'owner_self', 'statement_kind': 'preference', 'formed_by': 'stated',
            'proposition': 'MODEL_REWRITE_IS_NOT_EVIDENCE', 'supports': [{'evidence_id': evidence_id, 'segment_id': 's0'}]}
    items = [item]
    if mixed:
        items.append({**item, 'supports': [{'evidence_id': evidence_id, 'segment_id': 's1'}]})
    processor = HermesBatchAdapterProcessor(str(path), _route([_model(_batch(*items))]), clock=_clock)
    assert WorldJobWorker(path, processor=processor, policy=_policy(), clock=_clock).run_until_quiescent() == 1
    with _open(path) as db:
        green = db.execute("SELECT id,content FROM cognition WHERE invalid_at IS NULL AND content LIKE '%绿茶%'").fetchall()
        assert len(green) == 1, 'the explicit tool and the actual turn describe the same preference'
        assert green[0][1] == '用户其实更喜欢绿茶。'
        assert db.execute('SELECT raw_content FROM evidence WHERE id=?', (evidence_id,)).fetchone()[0] == raw
        assert db.execute("SELECT COUNT(*) FROM evidence WHERE raw_content='我更喜欢绿茶。'").fetchone()[0] == 0
        assert (accepted.skipped, accepted.stored) == ((0, 1) if source_first else (1, 0))
        if mixed:
            assert db.execute("SELECT COUNT(*) FROM cognition WHERE invalid_at IS NULL AND content LIKE '%生日%'").fetchone()[0] == 1
        else:
            revision = int(db.execute('SELECT revision FROM memory_state').fetchone()[0])
    if not mixed:
        deleted = service.submit_command(_command('forget-after-source-reuse', revision, 'delete_world_item', 'cognition', str(green[0][0])))
        assert deleted['result_state'] == 'applied'
        with _open(path) as db:
            assert '绿茶' not in json.dumps(build_bundle(db, _SUBJECT, host_id=_HOST, exported_at=_T0), ensure_ascii=False)


@pytest.mark.parametrize('change', ['wrong_quote', 'ambiguous_quote', 'forged_origin', 'wrong_owner', 'changed_content'])
def test_correction_source_validation_never_mutates_a_different_source_or_world(tmp_path: Path, change: str) -> None:
    path = tmp_path / 'source-validation.sqlite3'
    service = _prepare(path)
    source = _source()
    if change == 'wrong_quote': source['quote'] = '我喜欢蓝茶。'
    elif change == 'ambiguous_quote': source['content'] = '我其实更喜欢绿茶。我其实更喜欢绿茶。'
    elif change == 'forged_origin': source['origin_id'] = 'caller-selected-origin'
    elif change == 'wrong_owner': source['subject_id'] = 'another-owner'
    else:
        first = service.submit_command(_proposal(source))
        assert first['result_state'] == 'applied'
        source = {**source, 'content': '我现在更喜欢白茶。', 'quote': '我现在更喜欢白茶。'}
    with _open(path) as db:
        before = list(db.iterdump())
    with pytest.raises(TrustCommandError):
        command = _proposal(source, command_id='invalid-source')
        if change == 'changed_content':
            command['expected_world_revision'] = 2
            with _open(path) as db:
                command['target_id'] = str(db.execute('SELECT id FROM cognition WHERE invalid_at IS NULL').fetchone()[0])
        service.submit_command(command)
    with _open(path) as db:
        assert list(db.iterdump()) == before


def test_correction_source_keeps_the_existing_version_fence_and_legacy_call_contract(tmp_path: Path) -> None:
    path = tmp_path / 'source-version.sqlite3'
    service = _prepare(path)
    command = _proposal(_source(), command_id='stale-source')
    command['expected_world_revision'] = 0
    assert service.submit_command(command)['result_state'] == 'revision_conflict'
    legacy = service.submit_command(_proposal())
    assert legacy['result_state'] == 'applied'
    assert service.get_capabilities().get('correction_source') is True


def test_correction_source_does_not_write_into_another_world(tmp_path: Path) -> None:
    path = tmp_path / 'another-world.sqlite3'
    service = _prepare(path)
    with _open(path) as db:
        _seed_cognition(db, 'foreign', 'foreign-source', '另一个账户的偏好')
        db.execute("UPDATE cognition SET subject_id='another-owner' WHERE id='foreign'")
        before = list(db.execute('SELECT * FROM cognition'))
    command = _proposal(_source()); command['target_id'] = 'foreign'
    assert service.submit_command(command)['result_state'] == 'rejected'
    with _open(path) as db:
        assert list(db.execute('SELECT * FROM cognition')) == before
        assert db.execute("SELECT COUNT(*) FROM evidence WHERE raw_content LIKE '%绿茶%'").fetchone()[0] == 0


@pytest.mark.parametrize('outcome', ['revision_conflict', 'rejected', 'invalid_source'])
@pytest.mark.parametrize('late', [False, True])
def test_failed_or_late_corrections_cannot_keep_or_reintroduce_forgotten_source_bodies(
    tmp_path: Path, outcome: str, late: bool,
) -> None:
    path = tmp_path / 'source-erasure.sqlite3'
    service = _prepare(path)
    with _open(path) as db:
        _seed_cognition(db, 'independent', 'independent-source', '独立保留的生日')
    raw, previous = '我其实更喜欢绿茶。SOURCE_PRIVATE_RAW。', 'SOURCE_PRIVATE_CONTEXT'
    source = {**_source(raw), 'preceding_ai_context': previous}
    assert service.submit_command(_proposal(source))['result_state'] == 'applied'
    with _open(path) as db:
        current = str(db.execute("SELECT id FROM cognition WHERE content='用户其实更喜欢绿茶。'").fetchone()[0])
    def erase() -> None:
        revision = int(service.get_capabilities()['world_revision'])
        assert service.submit_command(_command('erase-real-source', revision, 'delete_world_item', 'cognition', current))['result_state'] == 'applied'
    if late:
        erase()
    command = _proposal(source, command_id='unbound-source-attempt')
    command['target_id'] = 'missing' if outcome == 'rejected' else 'independent'
    command['expected_world_revision'] = 0 if outcome == 'revision_conflict' else service.get_capabilities()['world_revision']
    if outcome == 'invalid_source':
        command['payload']['source'] = {**source, 'quote': '不存在的错误片段'}
        with pytest.raises(TrustCommandError):
            service.submit_command(command)
    else:
        receipt = service.submit_command(command)
        assert receipt['result_state'] == outcome
        assert service.submit_command(command) == receipt, 'redaction must preserve full-request hash replay'
    if not late:
        erase()
    with _open(path) as db:
        stored = '\n'.join(db.iterdump())
        assert 'SOURCE_PRIVATE_RAW' not in stored and previous not in stored, 'failed requests have no source binding for erasure to discover'
        assert db.execute("SELECT content FROM cognition WHERE id='independent'").fetchone()[0] == '独立保留的生日'


@pytest.mark.parametrize('source_first', [False, True])
@pytest.mark.parametrize('permission', ['allow_local_read', 'allow_cloud_read', 'allow_inference'])
def test_source_permissions_are_identical_in_both_arrival_orders(tmp_path: Path, source_first: bool, permission: str) -> None:
    from memoweft.integrations.hermes.recall import recall_world_snapshot
    path = tmp_path / 'source-permissions.sqlite3'
    service = _prepare(path)
    source = _source()
    candidates = _candidates([{'role': 'user', 'content': source['content'], 'message_id': source['message_id']}],
        session_id='real-session', subject_id=_SUBJECT, host_id=_HOST, boundary_id='permission-turn')
    boundary = _boundary(*candidates, event_id='permission-turn',
        target=_target(parent_session_id='real-session', result_session_id='real-session', subject_id=_SUBJECT, host_id=_HOST))
    if source_first:
        with _open(path) as db:
            HermesBoundaryStore(db, clock=_clock).accept(boundary)
    command = _proposal(source); command['payload'][permission] = False
    state = service.submit_command(command)['result_state']
    assert state == ('applied' if permission == 'allow_cloud_read' else 'rejected'), 'unreadable/non-inferable source cannot be formally applied'
    with _open(path) as db:
        HermesBoundaryStore(db, clock=_clock).accept(boundary)
        if state == 'applied':
            assert db.execute(f'SELECT {permission} FROM evidence WHERE origin_id=?', (candidates[0].origin_id,)).fetchone()[0] == 0
        else:
            assert db.execute("SELECT COUNT(*) FROM cognition WHERE invalid_at IS NULL AND content LIKE '%绿茶%'").fetchone()[0] == 0
        recall = recall_world_snapshot(db, _SUBJECT, '绿茶', model_tier='cloud')
        assert recall is None or '绿茶' not in recall.rendered_recall


def test_correction_defaults_cannot_widen_an_existing_private_source(tmp_path: Path) -> None:
    path = tmp_path / 'private-source.sqlite3'
    service = _prepare(path)
    source = _source()
    candidates = _candidates([{'role': 'user', 'content': source['content'], 'message_id': source['message_id']}],
        session_id='real-session', subject_id=_SUBJECT, host_id=_HOST, boundary_id='private-turn')
    with _open(path) as db:
        HermesBoundaryStore(db, clock=_clock).accept(_boundary(*candidates, event_id='private-turn',
            target=_target(parent_session_id='real-session', result_session_id='real-session', subject_id=_SUBJECT, host_id=_HOST)))
        db.execute('UPDATE evidence SET allow_local_read=0,allow_cloud_read=0,allow_inference=0 WHERE origin_id=?', (candidates[0].origin_id,))
    assert service.submit_command(_proposal(source))['result_state'] == 'rejected'
    with _open(path) as db:
        assert tuple(db.execute('SELECT allow_local_read,allow_cloud_read,allow_inference FROM evidence WHERE origin_id=?',
            (candidates[0].origin_id,)).fetchone()) == (0, 0, 0)


def test_successful_source_then_direct_evidence_erasure_scrubs_all_command_copies_and_replay(tmp_path: Path) -> None:
    path = tmp_path / 'direct-source-erasure.sqlite3'
    service = _prepare(path)
    source = {**_source('我其实更喜欢绿茶。DIRECT_SOURCE_PRIVATE_RAW。'), 'preceding_ai_context': 'DIRECT_SOURCE_PRIVATE_CONTEXT'}
    command = _proposal(source)
    receipt = service.submit_command(command)
    assert receipt['result_state'] == 'applied'
    with _open(path) as db:
        evidence = str(db.execute("SELECT id FROM evidence WHERE raw_content LIKE '%DIRECT_SOURCE_PRIVATE_RAW%'").fetchone()[0])
    assert service.submit_command(_command('direct-erasure', 2, 'delete_evidence', 'evidence', evidence))['result_state'] == 'applied'
    assert service.submit_command(command) == receipt
    with _open(path) as db:
        stored = '\n'.join(db.iterdump())
        assert 'DIRECT_SOURCE_PRIVATE_RAW' not in stored and 'DIRECT_SOURCE_PRIVATE_CONTEXT' not in stored


@pytest.mark.parametrize('scope,expected_intent', [
    ('complete', None), ('second_correction', 'correction'), ('remember', 'remember'),
    ('both_complete', None), ('arbitrary_current', 'correction'), ('unknown_remainder', 'remember'),
    ('missing_transition', 'correction'), ('missing_span', 'correction'), ('wrong_hash', 'correction'),
    ('wrong_projection', 'correction'), ('not_current', None), ('not_applied', 'correction'),
    ('control_with_fact', 'correction'),
])
def test_no_change_status_requires_the_exact_completed_correction_scope(
    tmp_path: Path, scope: str, expected_intent: str | None,
) -> None:
    from memoweft.integrations.dsh_bridge.formation_status import formation_requests
    path = tmp_path / 'source-no-change.sqlite3'
    service = _prepare(path)
    quote = '我其实更喜欢绿茶。'
    controls = '请调用记忆纠正工具更新已有条目后再回答，不能只承诺稍后整理。'
    second = '咖啡偏好改为拿铁。'
    remainder = second if scope in ('second_correction', 'both_complete') else (
        '另外记住我的生日是五月一日。' if scope == 'remember' else '我住在合成路八号。' if scope == 'unknown_remainder'
        else '请调用记忆纠正工具更新已有条目后再回答我住在合成路八号。' if scope == 'control_with_fact' else '')
    raw = quote + controls + remainder
    source = _source(raw)
    with _open(path) as db:
        _seed_cognition(db, 'old-coffee', 'coffee-source', '用户喜欢美式咖啡。')
    if scope != 'arbitrary_current':
        assert service.submit_command(_proposal(source))['result_state'] == 'applied'
    if scope == 'both_complete':
        command = _proposal({**source, 'quote': second}, command_id='second-correction')
        command.update(target_id='old-coffee', expected_world_revision=2)
        assert service.submit_command(command)['result_state'] == 'applied'
    candidates = _candidates([{'role': 'user', 'content': raw, 'message_id': source['message_id']}],
        session_id='real-session', subject_id=_SUBJECT, host_id=_HOST, boundary_id='no-change-turn')
    with _open(path) as db:
        HermesBoundaryStore(db, clock=_clock).accept(_boundary(*candidates, event_id='no-change-turn',
            target=_target(parent_session_id='real-session', result_session_id='real-session', subject_id=_SUBJECT, host_id=_HOST)))
        evidence_id = str(db.execute('SELECT id FROM evidence WHERE origin_id=?', (candidates[0].origin_id,)).fetchone()[0])
        if scope == 'arbitrary_current':
            columns = [str(row[1]) for row in db.execute('PRAGMA table_info(cognition)')]
            select = ["'unrelated-current'" if name == 'id' else "'用户其实更喜欢绿茶。'" if name == 'content' else name for name in columns]
            db.execute(f"INSERT INTO cognition ({','.join(columns)}) SELECT {','.join(select)} FROM cognition WHERE id='old-tea'")
            db.execute('INSERT INTO cognition_evidence VALUES (?,?,?)', ('unrelated-current', evidence_id, 'support'))
        if scope == 'missing_transition':
            db.execute('DELETE FROM cognition_transitions')
        elif scope in ('missing_span', 'wrong_hash'):
            db.execute('UPDATE evidence_ledger SET payload_json=? WHERE json_extract(content,\'$.evidence_id\')=?',
                       (json.dumps({} if scope == 'missing_span' else {'start': 0, 'end': len(quote), 'span_hash': 'wrong'}), evidence_id))
        elif scope == 'wrong_projection':
            db.execute("UPDATE cognition SET content='用户改写出来的绿茶偏好。' WHERE content='用户其实更喜欢绿茶。'")
        elif scope == 'not_current':
            db.execute("UPDATE cognition SET invalid_at=? WHERE content='用户其实更喜欢绿茶。'", (_T0,))
        elif scope == 'not_applied':
            db.execute("UPDATE trust_command_receipt SET result_state='rejected' WHERE command_id='correct-source'")
    processor = HermesBatchAdapterProcessor(str(path), _route([_model({'schema_version': 8, 'result': 'no_change'})] * 2), clock=_clock)
    assert WorldJobWorker(path, processor=processor, policy=_policy(), clock=_clock).run_until_quiescent() == 1
    with _open(path) as db:
        if scope == 'not_current':
            from memoweft.integrations.dsh_bridge.formation_status import _completed_correction_scope
            # The existing eligibility gate hides stale source jobs, without
            # treating their invalid replacement as proof of fulfillment.
            assert _completed_correction_scope(db, _SUBJECT, evidence_id, raw) == (True, set())
        requests = formation_requests(db, _SUBJECT)
        if expected_intent is None:
            assert requests == [], 'only the actually fulfilled correction scope may disappear'
        else:
            assert len(requests) == 1
            assert requests[0]['intent'] == expected_intent
        assert db.execute('SELECT raw_content FROM evidence WHERE id=?', (evidence_id,)).fetchone()[0] == raw
