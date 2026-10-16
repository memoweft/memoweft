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
