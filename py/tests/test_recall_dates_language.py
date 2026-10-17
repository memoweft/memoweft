"""G9 item 3 / PR B, paired with memoweft/weftmate#474 at aa83af9a.

recall-escape-contract.json is copied byte-for-byte from that PR's shared JSON.
Its bodies and exact rendered strings also drive the JavaScript reference and
actual WeftMate parser tests. Keep it portable for both producer and consumer.
recall-dates-exam.json preserves English user turns from the stopped exam;
Chinese counterparts are new equivalent fixtures, not original exam failures.
These tests use SQLite and fixed data only, with no model or exam execution.
"""
from __future__ import annotations

from datetime import datetime
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, cast

import pytest

from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
from memoweft.integrations.dsh_bridge.protocol_v2 import DshRpcV2Server
from memoweft.integrations.hermes import HermesMemoWeftRuntime
from memoweft.integrations.hermes.recall import format_recall, recall_world_snapshot
from memoweft.integrations.trust import QueryService
from memoweft.store import open_db


_FIXTURES = Path(__file__).parent / 'fixtures'
_ESCAPES: list[dict[str, Any]] = json.loads((_FIXTURES / 'recall-escape-contract.json').read_text(encoding='utf-8'))
_EXAM: list[dict[str, Any]] = json.loads((_FIXTURES / 'recall-dates-exam.json').read_text(encoding='utf-8'))['cases']
_CHINESE_MAIN: dict[str, Any] = json.loads((_FIXTURES / 'recall-chinese-main.json').read_text(encoding='utf-8'))
_OWNER = 'synthetic-owner'
_RECORDED = '2030-01-01T00:00:00Z'
_FORMED = '2038-01-01T00:00:00Z'


def _seed(db: sqlite3.Connection, content: str, *, item_id: str = 'fact', kind: str = 'cognition',
          occurred: str = '2026-01-06T17:48:59', recorded: str = _RECORDED,
          event_at: str | None = None, expression: str | None = None) -> None:
    db.execute("INSERT OR IGNORE INTO memory_state(singleton,revision,snapshot_json,snapshot_hash) VALUES(1,9,'{}','synthetic')")
    eid = f'source-{item_id}'
    db.execute("INSERT INTO evidence(id,subject_id,source_kind,host_id,occurred_at,recorded_at,raw_content,summary,"
               "allow_local_read,allow_cloud_read,allow_inference) VALUES(?,?,'spoken','synthetic',?,?,?,?,1,1,1)",
               (eid, _OWNER, occurred, recorded, content, content))
    if kind == 'event':
        db.execute("INSERT INTO world_event(id,world_id,content,occurred_at,time_expression,participants_json,objects_json,"
                   "formed_by,confidence,cred_status,created_at,updated_at) VALUES(?,?,?,?,?,'[]','[]','stated',600,'limited',?,?)",
                   (item_id, _OWNER, content, event_at, expression, _FORMED, _FORMED))
        db.execute("INSERT INTO world_event_evidence(world_event_id,evidence_id,relation) VALUES(?,?,'support')", (item_id, eid))
    else:
        db.execute("INSERT INTO cognition(id,subject_id,content,content_type,formed_by,confidence,cred_status,created_at,updated_at) "
                   "VALUES(?,?,?,'preference','stated',600,'limited',?,?)", (item_id, _OWNER, content, _FORMED, _FORMED))
        db.execute("INSERT INTO cognition_evidence(cognition_id,evidence_id,relation) VALUES(?,?,'support')", (item_id, eid))
    db.commit()


def _snapshot(db: sqlite3.Connection, query: str = 'synthetic topic') -> Any:
    result = recall_world_snapshot(db, _OWNER, query, lang='en')
    assert result is not None
    return result


@pytest.mark.parametrize('fixture', _ESCAPES, ids=lambda x: str(x['name']))
def test_shared_weftmate_english_wire_contract(fixture: dict[str, Any]) -> None:
    items = [{'content': item['body'], 'is_superseded': item.get('past', False)} for item in fixture['items']]
    assert format_recall(items, lang='en') == fixture['rendered']


@pytest.mark.parametrize('fixture', _CHINESE_MAIN['formats'])
def test_default_and_explicit_chinese_render_is_byte_identical_to_main(fixture: dict[str, Any]) -> None:
    assert format_recall(fixture['items']) == fixture['expected']
    assert format_recall(fixture['items'], lang='zh') == fixture['expected']


@pytest.mark.parametrize('fixture', _CHINESE_MAIN['worlds'])
@pytest.mark.parametrize('lang', [None, 'zh'])
def test_complete_chinese_snapshot_is_byte_identical_to_original_main(
    tmp_path: Path, fixture: dict[str, Any], lang: str | None,
) -> None:
    db = open_db(str(tmp_path / 'world.sqlite'))
    try:
        _seed(db, fixture['text'])
        result = (recall_world_snapshot(db, _OWNER, fixture['text'], lang='zh') if lang
                  else recall_world_snapshot(db, _OWNER, fixture['text']))
        assert result is not None
        assert json.loads(json.dumps(asdict(result))) == fixture['expected'], _CHINESE_MAIN['baseline']
    finally:
        db.close()


@pytest.mark.parametrize('fixture', _EXAM, ids=lambda x: str(x['source']))
@pytest.mark.parametrize('lang', [None, 'zh'])
def test_new_chinese_exam_equivalents_keep_legacy_default_and_explicit_configuration(
    tmp_path: Path, fixture: dict[str, Any], lang: str | None,
) -> None:
    runtime = DshMemoWeftRuntime()
    kwargs = {'lang': lang} if lang is not None else {}
    initialized = runtime.initialize('dates', dsh_home=str(tmp_path), subject_id=_OWNER,
                                     auto_route=False, model_tier='local', **kwargs)
    try:
        db = open_db(initialized['db_path'])
        try:
            _seed(db, fixture['zh'])
        finally:
            db.close()
        assert runtime.prefetch(fixture['zh']) == {'text': '记忆：' + fixture['zh'], 'count': 1}
    finally:
        runtime.shutdown()


@pytest.mark.parametrize('fixture', _EXAM, ids=lambda x: str(x['source']))
def test_exam_failure_english_configuration_reaches_dsh_and_trust(tmp_path: Path, fixture: dict[str, Any]) -> None:
    runtime = DshMemoWeftRuntime()
    initialized = runtime.initialize('dates', dsh_home=str(tmp_path), subject_id=_OWNER,
                                     auto_route=False, model_tier='local', lang='en')
    try:
        occurred = datetime.strptime(fixture['source_timestamp'], '%b %d, %Y, %H:%M:%S').isoformat()
        db = open_db(initialized['db_path'])
        try:
            _seed(db, fixture['en'], occurred=occurred)
        finally:
            db.close()
        response = runtime.prefetch(fixture['query'])
        assert response['count'] == 1
        actual = response['text']
        assert actual == f"Memory:[said at: {occurred}] {fixture['en']}", (fixture['question'], actual)
        assert '记忆：' not in actual
    finally:
        runtime.shutdown()


def _rpc(server: DshRpcV2Server, rid: str, method: str, params: dict[str, object]) -> dict[str, Any]:
    response = server.handle({'protocol': 'memoweft.dsh_rpc', 'protocol_version': 2, 'schema_version': 1,
                              'request_id': rid, 'method': method, 'params': params})
    assert response['ok'] is True, response
    return cast(dict[str, Any], response['result'])


def test_initialize_language_reaches_rpc_single_and_batch_and_hermes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = DshRpcV2Server()
    _rpc(server, 'init', 'initialize', {'session_id': 'dates', 'dsh_home': str(tmp_path / 'rpc'),
         'subject_id': _OWNER, 'model_tier': 'local', 'auto_route': False, 'lang': 'en'})
    try:
        assert server.runtime.db_path is not None
        db = open_db(str(server.runtime.db_path))
        try:
            _seed(db, 'synthetic topic is a verbatim statement.')
        finally:
            db.close()
        single = _rpc(server, 'one', 'preview_recall', {'query': 'synthetic topic', 'model_tier': 'local'})
        batch = _rpc(server, 'batch', 'preview_recall_batch', {'queries': ['synthetic topic'], 'model_tier': 'local'})
        assert single == batch['snapshots'][0]
        assert single['preview']['rendered_recall'].startswith('Memory:[said at: ')
    finally:
        server.runtime.shutdown()
    monkeypatch.setattr('memoweft.integrations.hermes._load_provider_settings', lambda: {'subject_id': _OWNER})
    runtime = HermesMemoWeftRuntime()
    runtime.initialize('dates', hermes_home=str(tmp_path / 'hermes'), agent_context='primary', lang='en')
    try:
        assert runtime.db_path is not None
        db = open_db(str(runtime.db_path))
        try:
            _seed(db, 'synthetic topic is a verbatim statement.')
        finally:
            db.close()
        snapshot = runtime.prefetch_snapshot('synthetic topic')
        assert snapshot is not None
        assert snapshot.rendered_recall.startswith('Memory:[said at: ')
        assert runtime.prefetch('synthetic topic') == snapshot.rendered_recall
        preview = cast(dict[str, Any], runtime.handle_trust_tool('memoweft_preview_recall', {'query': 'synthetic topic'})['preview'])
        assert preview['rendered_recall'] == snapshot.rendered_recall
    finally:
        runtime.shutdown()


def test_dates_are_evidence_time_not_formation_time_and_snapshot_is_read_only(tmp_path: Path) -> None:
    db = open_db(str(tmp_path / 'world.sqlite'))
    try:
        _seed(db, 'synthetic topic is a verbatim statement.')
        before = db.total_changes
        result = _snapshot(db)
        assert result.selected_item_ids == (('cognition', 'fact'),)
        assert result.rendered_recall == 'Memory:[said at: 2026-01-06T17:48:59] synthetic topic is a verbatim statement.'
        assert _FORMED not in result.rendered_recall
        assert _RECORDED not in result.rendered_recall
        assert db.total_changes == before
        assert _snapshot(db) == result
        db.execute("UPDATE cognition SET created_at='2040-01-01T00:00:00Z',updated_at='2041-01-01T00:00:00Z'")
        db.commit()
        assert _snapshot(db).recall_snapshot_token == result.recall_snapshot_token
        db.execute("UPDATE evidence SET occurred_at='2026-01-07T17:48:59'")
        db.commit()
        changed = _snapshot(db)
        assert changed.selected_item_ids == result.selected_item_ids
        assert changed.recall_snapshot_token != result.recall_snapshot_token
        service = QueryService(tmp_path / 'world.sqlite', subject_id=_OWNER, lang='en')
        preview = cast(dict[str, Any], service.preview_recall('synthetic topic')['preview'])
        assert preview['rendered_recall'] == changed.rendered_recall
        assert preview['recall_snapshot_token'] == changed.recall_snapshot_token
    finally:
        db.close()


@pytest.mark.parametrize('occurred,recorded,label', [(_RECORDED, _RECORDED, f'[recorded at: {_RECORDED}] '),
    ('', _RECORDED, f'[recorded at: {_RECORDED}] '), ('not a date', '', '')])
def test_recording_fallback_is_labeled_and_missing_real_dates_are_not_invented(
    tmp_path: Path, occurred: str, recorded: str, label: str,
) -> None:
    db = open_db(str(tmp_path / 'world.sqlite'))
    try:
        _seed(db, 'synthetic topic.', occurred=occurred, recorded=recorded)
        assert _snapshot(db).rendered_recall == f'Memory:{label}synthetic topic.'
    finally:
        db.close()


@pytest.mark.parametrize('event_at,expression,label', [
    ('2026-07-14', None, '[event at: 2026-07-14] '),
    (None, 'next Friday', '[event time: next Friday] '),
    ('2026-07-14', 'July 14', '[event at: 2026-07-14; event time: July 14] '), (None, None, ''),
])
def test_event_dates_are_event_fields_without_turning_source_or_formation_time_into_event_time(
    tmp_path: Path, event_at: str | None, expression: str | None, label: str,
) -> None:
    db = open_db(str(tmp_path / 'world.sqlite'))
    try:
        _seed(db, 'synthetic topic.', kind='event', event_at=event_at, expression=expression)
        assert _snapshot(db).rendered_recall == f'Memory:{label}synthetic topic.'
    finally:
        db.close()


def test_new_budget_counts_full_escaped_entries_dates_and_ids_without_truncation(tmp_path: Path) -> None:
    db = open_db(str(tmp_path / 'world.sqlite'))
    try:
        # Same rank and insertion semantics: only rendering may consume budget.
        body = 'synthetic topic.\n' + '\\x\n' * 20
        for index in range(7):
            _seed(db, body, item_id=f'fact-{index}')
        result = _snapshot(db)
        line = 'Memory:[said at: 2026-01-06T17:48:59] ' + body.replace('\\x', '\\\\x')
        count = min(5, 601 // (len(line) + 1))
        assert result.selected_item_ids == tuple(('cognition', f'fact-{index}') for index in range(count))
        assert result.rendered_recall == '\n'.join([line] * count)
        assert len(result.rendered_recall) <= 600
        assert result.count == count
        assert '…' not in result.rendered_recall
    finally:
        db.close()


def test_exact_new_budget_includes_headers_and_separators_and_never_splits_a_source() -> None:
    items: list[dict[str, object]] = [{'content': 'x' * 112} for _ in range(5)]
    assert len(format_recall(items, lang='en')) == 599
    items[0]['content'] = 'x' * 113
    assert len(format_recall(items, lang='en')) == 600
    items[0]['content'] = 'x' * 114
    assert format_recall(items, lang='en') == '\n'.join(['Memory:' + 'x' * 114] + ['Memory:' + 'x' * 112] * 3)
    assert format_recall([{'content': 'topic'}] * 7, lang='en') == '\n'.join(['Memory:topic'] * 5)
    assert format_recall([{'content': 'x' * 601}, {'content': 'lower ranked'}], lang='en') == ''


def test_dates_come_only_from_spoken_support_not_contradiction_and_pending_metadata_is_recording_time(tmp_path: Path) -> None:
    from memoweft.integrations.hermes.batch_adapter import _canonical, _hash_text
    db = open_db(str(tmp_path / 'world.sqlite'))
    try:
        _seed(db, 'synthetic topic.')
        for evidence_id, occurred, relation, kind in [
            ('second', '2025-01-01T00:00:00Z', 'support', 'spoken'),
            ('contradiction', '2042-01-01T00:00:00Z', 'contradict', 'spoken'),
            ('tool', '2043-01-01T00:00:00Z', 'support', 'tool'),
        ]:
            db.execute("INSERT INTO evidence(id,subject_id,source_kind,host_id,occurred_at,recorded_at,raw_content,summary,"
                       "allow_local_read,allow_cloud_read,allow_inference) VALUES(?,?,?,'synthetic',?,?,'synthetic topic.','synthetic topic.',1,1,1)",
                       (evidence_id, _OWNER, kind, occurred, _RECORDED))
            db.execute('INSERT INTO cognition_evidence(cognition_id,evidence_id,relation) VALUES(?,?,?)', ('fact', evidence_id, relation))
        db.commit()
        text = _snapshot(db).rendered_recall
        assert text == 'Memory:[said at: 2025-01-01T00:00:00Z; said at: 2026-01-06T17:48:59] synthetic topic.'
        size = len('synthetic topic.')
        db.execute('INSERT INTO evidence_ledger(id,content,payload_json) VALUES(?,?,?)', (
            'evidence-ledger-' + _hash_text(_canonical(['formation', 'fact', 'source-fact', 0, size])),
            _canonical({'cognition_id': 'fact', 'evidence_id': 'source-fact', 'relation': 'support'}),
            _canonical({'schema_version': 1, 'start': 0, 'end': size, 'source_metadata_pending': ['timestamp']}),
        ))
        db.commit()
        assert _snapshot(db).rendered_recall == f'Memory:[said at: 2025-01-01T00:00:00Z; recorded at: {_RECORDED}] synthetic topic.'
    finally:
        db.close()


def test_snapshot_token_commits_to_the_complete_actual_escaped_rendering(tmp_path: Path) -> None:
    db = open_db(str(tmp_path / 'world.sqlite'))
    try:
        _seed(db, 'synthetic topic.\n记忆：引用\nMemory: quote\n\\literal')
        result = _snapshot(db)
        assert '\n\\记忆：引用\n\\Memory: quote\n\\\\literal' in result.rendered_recall
        payload = {'schema_version': 1, 'surface': 'recall', 'subject_id': _OWNER,
                   'world_revision': result.world_revision, 'selected_item_ids': result.selected_item_ids,
                   'currentness_digest': result.currentness_digest, 'rendered_recall': result.rendered_recall}
        encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        assert result.recall_snapshot_token == sha256(encoded).hexdigest()
    finally:
        db.close()


def test_date_metadata_obeys_the_existing_cloud_gate_and_never_changes_evidence(tmp_path: Path) -> None:
    db = open_db(str(tmp_path / 'world.sqlite'))
    try:
        _seed(db, 'synthetic topic.')
        original = tuple(db.execute('SELECT raw_content,occurred_at,recorded_at FROM evidence').fetchone())
        local = _snapshot(db)
        cloud = recall_world_snapshot(db, _OWNER, 'synthetic topic', lang='en', model_tier='cloud')
        assert cloud is not None and cloud.rendered_recall == local.rendered_recall
        db.execute('UPDATE evidence SET allow_cloud_read=0')
        db.commit()
        blocked = recall_world_snapshot(db, _OWNER, 'synthetic topic', lang='en', model_tier='cloud')
        assert blocked is not None and blocked.selected_item_ids == () and blocked.rendered_recall == ''
        assert _snapshot(db).rendered_recall == local.rendered_recall
        assert tuple(db.execute('SELECT raw_content,occurred_at,recorded_at FROM evidence').fetchone()) == original
    finally:
        db.close()
