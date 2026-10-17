"""Actual old call shapes remain valid at every new language boundary."""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

import pytest

import memoweft.integrations.hermes as hermes
import memoweft.integrations.hermes.recall as recall
import memoweft.integrations.dsh_bridge as dsh
import memoweft.integrations.dsh_bridge.protocol_v2 as rpc
import memoweft.integrations.recall_language as language
from memoweft.integrations.trust import QueryService
from memoweft.store import open_db
from memoweft.types import Lang
from test_recall_dates_language import _OWNER, _rpc, _seed


def _empty() -> recall.RecallSnapshotV1:
    return recall.RecallSnapshotV1(subject_id=_OWNER, world_revision=9, selected_item_ids=(),
        currentness_digest='a' * 64, rendered_recall='', recall_snapshot_token='b' * 64, count=0)


@pytest.mark.parametrize('configured', [None, 'zh', 'en'])
@pytest.mark.parametrize('provider', [False, True])
def test_runtime_and_plugin_accept_unchanged_three_argument_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: str | None, provider: bool,
) -> None:
    calls: list[tuple[str, str]] = []
    expected = _empty()

    def old_snapshot(db: sqlite3.Connection, subject_id: str, query: str) -> recall.RecallSnapshotV1:
        del db
        calls.append((subject_id, query))
        return expected

    monkeypatch.setattr(hermes, 'recall_world_snapshot', old_snapshot)
    monkeypatch.setattr(hermes, '_load_provider_settings', lambda: {'subject_id': _OWNER})
    class Base:
        pass
    runtime: Any = hermes._build_provider_class(Base)() if provider else hermes.HermesMemoWeftRuntime()
    runtime.initialize('compat', hermes_home=str(tmp_path), agent_context='primary', lang=configured)
    try:
        assert runtime.prefetch_snapshot('old query', session_id='compat') is expected
        assert calls == [(_OWNER, 'old query')]
    finally:
        runtime.shutdown()


@pytest.mark.parametrize('configured', ['zh', 'en'])
def test_text_wrapper_and_trust_single_batch_keep_old_snapshot_keywords(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: Lang,
) -> None:
    path = tmp_path / 'compat.sqlite'
    db = open_db(str(path))
    expected = _empty()
    calls: list[str] = []

    def old_snapshot(db: sqlite3.Connection, subject_id: str, query: str, *, model_tier: str = 'local') -> recall.RecallSnapshotV1:
        del db
        assert subject_id == _OWNER and model_tier == 'local'
        calls.append(query)
        return expected

    monkeypatch.setattr(recall, 'recall_world_snapshot', old_snapshot)
    try:
        _seed(db, 'synthetic topic.')
        assert recall.recall_world_text(db, _OWNER, 'text', lang=configured) == ('', 0)
        service = QueryService(path, subject_id=_OWNER, lang=configured)
        one = service.preview_recall('single')
        batch: Any = service.preview_recall_batch(['batch'])
        assert one['preview'] is not None and batch['snapshots'][0]['preview'] is not None
        assert calls == ['text', 'single', 'batch']
    finally:
        db.close()


@pytest.mark.parametrize('configured', ['zh', 'en'])
@pytest.mark.parametrize('host', ['hermes', 'dsh'])
def test_direct_text_routes_keep_legacy_implementations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: Lang, host: str,
) -> None:
    calls: list[str] = []

    def old_hermes_text(db: sqlite3.Connection, subject_id: str, query: str) -> tuple[str, int]:
        del db
        assert subject_id == _OWNER
        calls.append(query)
        return '记忆：旧格式', 1

    def old_dsh_text(db: sqlite3.Connection, subject_id: str, query: str, *, model_tier: str = 'local') -> tuple[str, int]:
        assert model_tier == 'local'
        return old_hermes_text(db, subject_id, query)

    runtime: Any
    if host == 'hermes':
        monkeypatch.setattr(hermes, '_load_provider_settings', lambda: {'subject_id': _OWNER})
        monkeypatch.setattr(hermes, 'recall_world_text', old_hermes_text)
        runtime = hermes.HermesMemoWeftRuntime()
        runtime.initialize('compat', hermes_home=str(tmp_path), agent_context='primary', lang=configured)
    else:
        monkeypatch.setattr(dsh, 'recall_world_text', old_dsh_text)
        runtime = dsh.DshMemoWeftRuntime()
        runtime.initialize('compat', dsh_home=str(tmp_path), subject_id=_OWNER, model_tier='local', auto_route=False, lang=configured)
    try:
        assert runtime.prefetch('query') == ('记忆：旧格式' if host == 'hermes' else {'text': '记忆：旧格式', 'count': 1})
        assert calls == ['query']
    finally:
        runtime.shutdown()


@pytest.mark.parametrize('host', ['hermes', 'dsh'])
def test_old_query_service_constructor_falls_back_to_real_chinese_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str,
) -> None:
    class OldQueryService(QueryService):
        def __init__(self, path: Path | str, *, subject_id: str, surface: Any = 'trust_local') -> None:
            super().__init__(path, subject_id=subject_id, surface=surface)

    runtime: Any
    if host == 'hermes':
        monkeypatch.setattr(hermes, '_load_provider_settings', lambda: {'subject_id': _OWNER})
        monkeypatch.setattr(hermes, 'QueryService', OldQueryService)
        runtime = hermes.HermesMemoWeftRuntime()
        runtime.initialize('compat', hermes_home=str(tmp_path), agent_context='primary', lang='en')
    else:
        monkeypatch.setattr(rpc, 'QueryService', OldQueryService)
        server = rpc.DshRpcV2Server()
        _rpc(server, 'init', 'initialize', {'session_id': 'compat', 'dsh_home': str(tmp_path), 'subject_id': _OWNER,
             'auto_route': False, 'model_tier': 'local', 'lang': 'en'})
        runtime = server.runtime
    try:
        db = open_db(str(runtime.db_path))
        try:
            _seed(db, 'synthetic topic.')
        finally:
            db.close()
        result: Any = (runtime.handle_trust_tool('memoweft_preview_recall', {'query': 'synthetic topic'}) if host == 'hermes'
                       else _rpc(server, 'preview', 'preview_recall', {'query': 'synthetic topic', 'model_tier': 'local'}))
        assert result['preview']['rendered_recall'] == '记忆：synthetic topic.'
    finally:
        runtime.shutdown()


def test_opaque_forwarder_does_not_receive_an_unconfirmed_language_keyword() -> None:
    calls: list[dict[str, Any]] = []
    def opaque(value: str, **kwargs: Any) -> str:
        calls.append(kwargs)
        return value
    assert language.call_with_recall_language(opaque, 'old Chinese', lang='en') == 'old Chinese'
    assert calls == [{}]


def test_uninspectable_reader_uses_legacy_call_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    def reader(value: str) -> str:
        calls.append(value)
        return value
    def unavailable(_operation: Any) -> Any:
        raise ValueError('unknown signature')
    monkeypatch.setattr(language, 'signature', unavailable)
    assert language.call_with_recall_language(reader, 'old Chinese', lang='en') == 'old Chinese'
    assert calls == ['old Chinese']


def test_default_language_never_inspects_or_passes_a_new_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(_operation: Any) -> Any:
        raise AssertionError('default call must remain direct')
    monkeypatch.setattr(language, 'signature', forbidden)
    assert language.call_with_recall_language(lambda value: value, 'legacy', lang='zh') == 'legacy'


def test_declared_english_implementation_errors_are_not_retried_or_hidden() -> None:
    calls: list[str] = []
    def broken(*, lang: Lang = 'zh') -> str:
        calls.append(lang)
        raise TypeError('inside implementation')
    with pytest.raises(TypeError, match='inside implementation'):
        language.call_with_recall_language(broken, lang='en')
    assert calls == ['en']


@pytest.mark.parametrize('configured', [None, 'zh', 'en'])
def test_history_runtime_keeps_legacy_query_implementation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: str | None,
) -> None:
    calls: list[dict[str, Any]] = []
    def old_query(path: Path, **kwargs: Any) -> dict[str, object]:
        del path
        assert 'lang' not in kwargs
        calls.append(kwargs)
        return {'rendered_context': '旧历史原文'}
    monkeypatch.setattr(dsh, 'query_interactions', old_query)
    runtime = dsh.DshMemoWeftRuntime()
    runtime.initialize('compat', dsh_home=str(tmp_path), subject_id=_OWNER, model_tier='local', auto_route=False, lang=configured)
    try:
        assert runtime.query_interactions('history') == {'rendered_context': '旧历史原文'}
        assert len(calls) == 1
    finally:
        runtime.shutdown()
