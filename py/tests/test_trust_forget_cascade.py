"""FG-1: source erasure reaches identities/aliases and on-disk/portable copies."""
import json
import pytest
from typing import Any
from pathlib import Path

from memoweft.portable.builder import build_bundle
from test_trust_command_service import _open, _seed_evidence, _seed_revision, _service, _command, _SUBJECT, _HOST, _T0


@pytest.mark.parametrize('target', ['old', 'middle', 'current'])
def test_forget_whole_revision_chain_preview_history_export_and_replay(tmp_path: Path, target: str) -> None:
    from memoweft.integrations.trust.forget_preview import preview_forget
    from test_trust_command_service import _seed_cognition
    path = tmp_path / 'revision-chain.sqlite3'
    with _open(path) as db:
        for item_id in ['old', 'middle', 'current']:
            _seed_cognition(db, item_id, f'e-{item_id}', f'FGChainSecret-{item_id}')
        _seed_cognition(db, 'independent', 'e-independent', 'IndependentBirthday')
        db.execute("UPDATE cognition SET invalid_at=? WHERE id IN ('old','middle')", (_T0,))
        db.executemany('INSERT INTO cognition_transitions VALUES (?,?,?,?,?)', [
            ('first', 'old', 'middle', 'corrects', 1), ('second', 'middle', 'current', 'supersedes', 1)])
        _seed_revision(db)
        before = list(db.iterdump())
    preview = preview_forget(str(path), _SUBJECT, target_kind='cognition', target_id=target)
    items, evidence_ids = preview['items'], preview['evidence_ids']
    assert isinstance(items, list) and isinstance(evidence_ids, list)
    assert {row['item_id'] for row in items} == {'old', 'middle', 'current'}
    assert set(evidence_ids) == {'e-old', 'e-middle', 'e-current'}
    with _open(path) as db:
        assert list(db.iterdump()) == before
    command = _command('forget-chain', 1, 'delete_world_item', 'cognition', target)
    service = _service(path)
    receipt = service.submit_command(command)
    assert receipt['result_state'] == 'applied'
    assert receipt == service.submit_command(command), 'same command replays its original receipt'
    assert {'old', 'middle', 'current', 'e-old', 'e-middle', 'e-current'} <= set(receipt['affected_ids'])
    with _open(path) as db:
        assert [row[0] for row in db.execute('SELECT id FROM cognition')] == ['independent']
        assert not db.execute('SELECT 1 FROM cognition_transitions').fetchone()
        assert db.execute('SELECT raw_content FROM evidence WHERE id=?', ('e-independent',)).fetchone()[0] == 'IndependentBirthday'
        assert 'FGChainSecret' not in json.dumps(build_bundle(db, _SUBJECT, host_id=_HOST, exported_at=_T0))
        assert db.execute('SELECT revision FROM memory_state').fetchone()[0] == 2


def test_forget_revision_chain_rolls_back_when_a_later_historical_source_rejects(tmp_path: Path) -> None:
    from test_trust_command_service import _seed_cognition
    path = tmp_path / 'revision-rollback.sqlite3'
    with _open(path) as db:
        _seed_cognition(db, 'current', 'a-current', 'CurrentChainSecret')
        _seed_cognition(db, 'old', 'z-old', 'HistoricalChainSecret')
        db.execute('INSERT INTO cognition_transitions VALUES (?,?,?,?,?)', ('link', 'old', 'current', 'corrects', 1))
        db.execute('UPDATE evidence SET deleted_at=? WHERE id=?', (_T0, 'z-old'))
        _seed_revision(db)
    receipt = _service(path).submit_command(_command('forget-rollback', 1, 'delete_world_item', 'cognition', 'current'))
    assert receipt['result_state'] == 'rejected'
    assert receipt['affected_ids'] == []
    with _open(path) as db:
        assert db.execute('SELECT COUNT(*) FROM cognition').fetchone()[0] == 2
        assert db.execute('SELECT raw_content FROM evidence WHERE id=?', ('a-current',)).fetchone()[0] == 'CurrentChainSecret'
        assert db.execute('SELECT COUNT(*) FROM cognition_transitions').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM world_delete_marker').fetchone()[0] == 0
        assert db.execute('SELECT revision FROM memory_state').fetchone()[0] == 1


@pytest.mark.parametrize('kind,table,current,name_column,link,column', [
    ('entity', 'entity', 'entity-wang', 'canonical_name', None, None),
    ('relationship', 'relationship', 'rel-wang', 'content', 'relationship_evidence', 'relationship_id'),
    ('event', 'world_event', 'event-hike', 'content', 'world_event_evidence', 'world_event_id'),
])
def test_forget_follows_existing_ledger_revision_links_across_world_kinds(
    tmp_path: Path, kind: str, table: str, current: str, name_column: str, link: str | None, column: str | None,
) -> None:
    from memoweft.integrations.trust.forget_preview import preview_forget
    from test_trust_command_service import _seed_four_kinds
    path = tmp_path / f'{kind}-chain.sqlite3'
    with _open(path) as db:
        _seed_four_kinds(db)
        _seed_evidence(db, 'e-old-chain', 'LedgerRevisionSecret')
        columns = [str(row[1]) for row in db.execute(f'PRAGMA table_info({table})')]
        selection = ["'old-chain'" if name == 'id' else "'LedgerRevisionSecret'" if name == name_column else name for name in columns]
        db.execute(f"INSERT INTO {table} ({','.join(columns)}) SELECT {','.join(selection)} FROM {table} WHERE id=?", (current,))
        if link is not None:
            db.execute(f'INSERT INTO {link} ({column},evidence_id,relation) VALUES (?,?,?)', ('old-chain', 'e-old-chain', 'support'))
        else:
            db.execute('INSERT INTO evidence_ledger VALUES (?,?,?)', ('old-source', json.dumps({'entity_id': 'old-chain', 'evidence_id': 'e-old-chain'}), '{}'))
        db.execute('INSERT INTO evidence_ledger VALUES (?,?,?)', ('correction', json.dumps({
            'relation': 'corrects', f'prior_{kind}_id': 'old-chain', f'replacement_{kind}_id': current}), '{}'))
        _seed_revision(db)
    preview = preview_forget(str(path), _SUBJECT, target_kind=kind, target_id=current)
    items = preview['items']
    assert isinstance(items, list)
    assert {'old-chain', current} <= {row['item_id'] for row in items}
    receipt = _service(path).submit_command(_command('ledger-chain', 1, 'delete_world_item', kind, current))
    assert receipt['result_state'] == 'applied'
    with _open(path) as db:
        assert db.execute(f'SELECT 1 FROM {table} WHERE id=?', ('old-chain',)).fetchone() is None
        assert db.execute('SELECT content FROM cognition WHERE id=?', ('cog-coffee',)).fetchone()[0] == '用户喜欢喝咖啡'
        assert 'LedgerRevisionSecret' not in json.dumps(build_bundle(db, _SUBJECT, host_id=_HOST, exported_at=_T0))


@pytest.mark.parametrize('case', ['relationship', 'name-cognition'])
@pytest.mark.parametrize('side', ['prior', 'replacement'])
def test_forget_traverses_the_reasons_written_by_the_actual_formation_adapter(
    tmp_path: Path, case: str, side: str,
) -> None:
    from test_person_understanding_loop import seed, correct
    from test_formation_accuracy import _item
    from memoweft.integrations.trust.forget_preview import preview_forget
    path = tmp_path / f'production-{case}-{side}.sqlite3'
    ids = seed(path)
    if case == 'relationship':
        kind, table = 'relationship', 'relationship'
        item: dict[str, Any] = dict(_item('ignored'), action='form', statement_kind='relationship', relation_type='teacher',
            target_entity={'canonical_name': '林遥', 'kind': 'person'}, supersedes_relationship_id=ids['relationship'])
        raw = '更正：林遥是我的老师，不是朋友。'
    else:
        kind, table = 'cognition', 'cognition'
        item = dict(_item('ignored'), action='correct', statement_kind='alias',
            entity={'canonical_name': '林澜', 'kind': 'person'}, alias_of={'canonical_name': '林遥', 'kind': 'person'})
        raw = '更正：林遥的名字我说错了，其实叫林澜。'
    assert correct(path, raw, item)['state'] == 'applied'
    with _open(path) as db:
        replacement = str(db.execute(f'SELECT id FROM {table} WHERE id<>? AND invalid_at IS NULL', (ids[kind],)).fetchone()[0])
        reason = db.execute(f'SELECT reason FROM {kind}_transitions WHERE prior_{kind}_id=?', (ids[kind],)).fetchone()[0]
        assert reason == ('superseded' if case == 'relationship' else 'name_corrected')
        revision = int(db.execute('SELECT revision FROM memory_state').fetchone()[0])
    target = ids[kind] if side == 'prior' else replacement
    preview = preview_forget(str(path), _SUBJECT, target_kind=kind, target_id=target)
    evidence_ids = preview['evidence_ids']
    assert isinstance(evidence_ids, list)
    assert {'evidence-1', 'evidence-2'} <= set(evidence_ids)
    receipt = _service(path).submit_command(_command('production-chain', revision, 'delete_world_item', kind, target))
    assert receipt['result_state'] == 'applied'
    with _open(path) as db:
        for evidence in ('evidence-1', 'evidence-2'):
            assert db.execute('SELECT raw_content FROM evidence WHERE id=?', (evidence,)).fetchone()[0] == ''
        exported = json.dumps(build_bundle(db, _SUBJECT, host_id=_HOST, exported_at=_T0), ensure_ascii=False)
        assert raw not in exported
        assert '林遥修乐器很熟练' not in exported


def test_forget_preserves_unrelated_preceding_context_and_clears_source_or_id(tmp_path: Path) -> None:
    path = tmp_path / "contexts.sqlite3"
    kept = json.dumps([{"role": "assistant", "content": "B 的无关主题消解"}], ensure_ascii=False)
    with _open(path) as db:
        for item_id, text, context in (
            ("e-a", "A 的被忘原话", None),
            ("e-b", "B", kept),
            ("e-c", "C", json.dumps([{"content": "引用 A 的被忘原话"}], ensure_ascii=False)),
            ("e-d", "D", json.dumps({"evidence_ids": ["e-a"]})),
            ("e-other", "other", "unrelated plain text"),
        ):
            _seed_evidence(db, item_id, text)
            db.execute("UPDATE evidence SET preceding_ai_context = ? WHERE id = ?", (context, item_id))
        _seed_revision(db)
    result = _service(path).submit_command(_command("erase-a", 1, "delete_evidence", "evidence", "e-a"))
    assert result["result_state"] == "applied"
    with _open(path) as db:
        contexts = dict(db.execute("SELECT id, preceding_ai_context FROM evidence"))
        assert contexts["e-b"] == kept
        assert contexts["e-other"] == "unrelated plain text"
        assert contexts["e-c"] is None and contexts["e-d"] is None


def test_forget_preview_is_read_only_and_matches_shared_source_cascade(tmp_path: Path) -> None:
    from memoweft.integrations.trust.forget_preview import preview_forget
    from test_trust_command_service import _seed_cognition
    path = tmp_path / "preview.sqlite3"
    with _open(path) as db:
        _seed_cognition(db, "c-a", "e-shared", "王小明是我的好兄弟")
        _seed_cognition(db, "c-b", "e-other", "无关决定")
        db.execute("INSERT INTO entity (id,world_id,kind,canonical_name,created_at,updated_at) VALUES ('person',?,'person','王小明',?,?)", (_SUBJECT, _T0, _T0))
        db.execute("INSERT INTO relationship (id,world_id,source_entity_id,target_entity_id,relation_type,content,formed_by,confidence,cred_status,created_at,updated_at) VALUES ('rel',?,'owner','person','friend','好兄弟','stated',800,'trusted',?,?)", (_SUBJECT, _T0, _T0))
        db.execute("INSERT INTO relationship_evidence VALUES ('rel','e-shared','support')")
        _seed_revision(db)
        before = list(db.iterdump())
    preview = preview_forget(str(path), _SUBJECT, target_kind="cognition", target_id="c-a")
    assert preview["world_revision"] == 1
    assert preview["item_count"] == 3 and preview["evidence_count"] == 1
    items = preview["items"]
    assert isinstance(items, list)
    assert {item["name"] for item in items} == {"王小明是我的好兄弟", "王小明", "好兄弟"}
    with _open(path) as db:
        assert list(db.iterdump()) == before
    assert preview_forget(str(path), "other-owner", conversation_id="session")["items"] == []
    receipt = _service(path).submit_command(_command("apply-preview", 1, "delete_world_item", "cognition", "c-a"))
    assert {item["item_id"] for item in items} | {"e-shared"} <= set(receipt["affected_ids"])
    with _open(path) as db:
        assert db.execute("SELECT content FROM cognition WHERE id='c-b'").fetchone()[0] == "无关决定"


def test_conversation_preview_recovers_pruned_job_and_keeps_all_tables(tmp_path: Path) -> None:
    from memoweft.integrations.dsh_bridge import _origin_id
    from memoweft.integrations.trust.forget_preview import preview_forget
    from test_trust_command_service import _seed_cognition
    path = tmp_path / "conversation-preview.sqlite3"
    turn = {"role": "user", "content": "原话", "message_id": "msg", "source_ref": "source:0"}
    with _open(path) as db:
        _seed_cognition(db, "c", "e", "原话")
        origin = _origin_id(message=turn, content="原话", session_id="session", message_index=0,
                            subject_id=_SUBJECT, host_id=_HOST, boundary_id="episode")
        db.execute("UPDATE evidence SET origin_id=? WHERE id='e'", (origin,))
        db.execute("INSERT INTO interaction_context (id,subject_id,conversation_id,episode_id,context_json,context_hash,created_at) VALUES ('i',?,'session','episode',?,'hash',?)", (_SUBJECT,json.dumps([turn]),_T0))
        _seed_revision(db)
        before = list(db.iterdump())
    preview = preview_forget(str(path), _SUBJECT, conversation_id="session")
    assert preview["evidence_ids"] == ["e"] and preview["item_count"] == 2
    preview_items = preview["items"]
    assert isinstance(preview_items, list)
    assert {item["item_id"] for item in preview_items} == {"c", "i"}
    with _open(path) as db:
        assert list(db.iterdump()) == before


def test_last_relationship_source_erases_untracked_entity_alias_and_disk(tmp_path: Path) -> None:
    path = tmp_path / "forget.sqlite3"
    secret = "FGSecretPersonAlias-2026"
    with _open(path) as db:
        _seed_evidence(db, "e-secret", secret)
        db.execute("INSERT INTO entity (id, world_id, kind, canonical_name, created_at, updated_at, aliases_json) VALUES (?, ?, 'person', ?, ?, ?, ?)",
                   ("person-secret", _SUBJECT, secret, _T0, _T0, json.dumps([secret + "-alias"])))
        db.execute("INSERT INTO relationship (id, world_id, source_entity_id, target_entity_id, relation_type, content, formed_by, confidence, cred_status, created_at, updated_at) VALUES ('rel-secret', ?, 'owner', 'person-secret', 'friend', ?, 'stated', 800, 'trusted', ?, ?)",
                   (_SUBJECT, secret, _T0, _T0))
        db.execute("INSERT INTO relationship_evidence VALUES ('rel-secret', 'e-secret', 'support')")
        _seed_revision(db)
    receipt = _service(path).submit_command(_command("forget-source", 1, "delete_world_item", "relationship", "rel-secret"))
    assert receipt["result_state"] == "applied"
    assert receipt["storage_cleanup"]["state"] == "complete"
    assert {"person-secret", "rel-secret", "e-secret"} <= set(receipt["affected_ids"])
    with _open(path) as db:
        assert not db.execute("SELECT 1 FROM entity WHERE id = 'person-secret'").fetchone()
        assert secret not in json.dumps(build_bundle(db, _SUBJECT, host_id=_HOST, exported_at=_T0))
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            assert secret not in str(db.execute(f'SELECT * FROM "{table}"').fetchall())
    for file in tmp_path.glob("forget.sqlite3*"):
        assert secret.encode() not in file.read_bytes()


def test_person_forget_follows_relationship_provenance_without_entity_ledger(tmp_path: Path) -> None:
    path = tmp_path / "person.sqlite3"
    with _open(path) as db:
        _seed_evidence(db, "e-person", "FGSecretPerson")
        db.execute("INSERT INTO entity (id, world_id, kind, canonical_name, created_at, updated_at, aliases_json) VALUES ('person', ?, 'person', 'FGSecretPerson', ?, ?, '[]')", (_SUBJECT, _T0, _T0))
        db.execute("INSERT INTO relationship (id, world_id, source_entity_id, target_entity_id, relation_type, content, formed_by, confidence, cred_status, created_at, updated_at) VALUES ('rel', ?, 'owner', 'person', 'friend', 'FGSecretPerson', 'stated', 800, 'trusted', ?, ?)", (_SUBJECT, _T0, _T0))
        db.execute("INSERT INTO relationship_evidence VALUES ('rel', 'e-person', 'support')")
        _seed_revision(db)
    receipt = _service(path).submit_command(_command("forget-person", 1, "delete_world_item", "entity", "person"))
    assert receipt["result_state"] == "applied"
    with _open(path) as db:
        assert not db.execute("SELECT 1 FROM entity WHERE id='person'").fetchone()
        assert db.execute("SELECT raw_content FROM evidence WHERE id='e-person'").fetchone()[0] == ""


def test_opt_in_removes_original_context_and_keeps_unrelated_ledger(tmp_path: Path) -> None:
    path = tmp_path / "snippets.sqlite3"
    with _open(path) as db:
        _seed_evidence(db, "e-snippet", "FGPrivateSource")
        db.execute("INSERT INTO evidence_ledger (id,content,payload_json) VALUES ('unrelated','{}','{}')")
        db.execute("INSERT INTO interaction_context (id,subject_id,conversation_id,episode_id,context_json,context_hash,created_at) VALUES ('interaction', ?, 'conversation', 'episode', ?, 'old-hash', ?)",
                   (_SUBJECT, json.dumps([{"role":"user","content":"FGPrivateSource"},{"role":"user","content":"保留这句话"}]), _T0))
        _seed_revision(db)
    command = _command("forget-snippets", 1, "delete_evidence", "evidence", "e-snippet")
    command["payload"] = {"delete_conversation_snippets": True}
    receipt = _service(path).submit_command(command)
    assert receipt["result_state"] == "applied"
    with _open(path) as db:
        assert db.execute("SELECT 1 FROM evidence_ledger WHERE id='unrelated'").fetchone()
        context = db.execute("SELECT context_json FROM interaction_context WHERE id='interaction'").fetchone()[0]
        assert "FGPrivateSource" not in context
        # D33 deletes the whole derived copy; it does not retain a partial transcript.
        assert json.loads(context) == []


def test_deleted_conversation_erases_retained_original_context_from_sqlite_and_portable(tmp_path: Path) -> None:
    from memoweft.integrations.trust.true_delete import erase_conversation_context
    path = tmp_path / "conversation.sqlite3"
    with _open(path) as db:
        db.execute("INSERT INTO interaction_context (id,subject_id,conversation_id,episode_id,context_json,context_hash,created_at) VALUES ('deleted', ?, 'session-delete', 'episode', ?, 'old-hash', ?)",
                   (_SUBJECT, json.dumps([{"role":"user","content":"FGSecretTranscript"}]), _T0))
        db.execute("INSERT INTO interaction_context (id,subject_id,conversation_id,episode_id,context_json,context_hash,created_at) VALUES ('kept', ?, 'session-other', 'episode', ?, 'other-hash', ?)",
                   (_SUBJECT, json.dumps([{"role":"user","content":"保留其他对话"}]), _T0))
        _seed_revision(db)
    result = erase_conversation_context(str(path), _SUBJECT, "session-delete")
    assert result["result_state"] == "applied"
    assert result["storage_cleanup"] == {"state":"complete"}
    with _open(path) as db:
        assert db.execute("SELECT context_json FROM interaction_context WHERE id='deleted'").fetchone()[0] == "[]"
        assert json.loads(db.execute("SELECT context_json FROM interaction_context WHERE id='kept'").fetchone()[0])[0]["content"] == "保留其他对话"
        assert "FGSecretTranscript" not in json.dumps(build_bundle(db, _SUBJECT, host_id=_HOST, exported_at=_T0))
    assert b"FGSecretTranscript" not in path.read_bytes()
    assert erase_conversation_context(str(path), _SUBJECT, "session-delete")["result_state"] == "no_change"


def test_forgetting_erases_core_original_copy_without_deleting_host_chat(tmp_path: Path) -> None:
    path = tmp_path / "copy.sqlite3"
    with _open(path) as db:
        _seed_evidence(db, "e-copy", "FGSourceCopy")
        db.execute("INSERT INTO interaction_context (id,subject_id,conversation_id,episode_id,context_json,context_hash,created_at) VALUES ('copy', ?, 'session', 'episode', ?, 'old', ?)",
                   (_SUBJECT, json.dumps([{"role":"user","content":"FGSourceCopy"},{"role":"user","content":"unrelated"}]), _T0))
        _seed_revision(db)
    receipt = _service(path).submit_command(_command("erase-copy", 1, "delete_evidence", "evidence", "e-copy"))
    assert receipt["result_state"] == "applied"
    with _open(path) as db:
        context = db.execute("SELECT context_json FROM interaction_context WHERE id='copy'").fetchone()[0]
        assert json.loads(context) == []
    assert b"FGSourceCopy" not in path.read_bytes()


def test_conversation_erasure_recovers_sources_after_batch_job_has_disappeared(tmp_path: Path) -> None:
    from memoweft.integrations.dsh_bridge import _origin_id
    from memoweft.integrations.trust.true_delete import erase_conversation_context
    from test_trust_command_service import _seed_cognition
    path = tmp_path / "batch.sqlite3"
    turns = [{"role":"user","content":"FGSourceOne","message_id":"msg-one","source_ref":"source:0"},
             {"role":"user","content":"FGSourceTwo","message_id":"msg-two","source_ref":"source:1"}]
    with _open(path) as db:
        for index, turn in enumerate(turns):
            _seed_cognition(db, f"c-{index}", f"e-{index}", turn["content"])
            origin = _origin_id(message=turn, content=turn["content"], session_id="session-delete", message_index=index,
                                subject_id=_SUBJECT, host_id=_HOST, boundary_id="episode")
            db.execute("UPDATE evidence SET origin_id=? WHERE id=?", (origin, f"e-{index}"))
        _seed_cognition(db, "c-other", "e-other", "FGSourceTwo")
        db.execute("INSERT INTO interaction_context (id,subject_id,conversation_id,episode_id,context_json,context_hash,created_at) VALUES ('batch', ?, 'session-delete', 'episode', ?, 'old', ?)",
                   (_SUBJECT, json.dumps(turns), _T0))
        _seed_revision(db)
    _service(path).submit_command(_command("erase-first", 1, "delete_evidence", "evidence", "e-0"))
    result = erase_conversation_context(str(path), _SUBJECT, "session-delete")
    assert result["erased_evidence_count"] == 1
    affected = result["affected_ids"]
    assert isinstance(affected, list) and "c-1" in affected
    with _open(path) as db:
        assert db.execute("SELECT raw_content FROM evidence WHERE id='e-1'").fetchone()[0] == ""
        assert db.execute("SELECT raw_content FROM evidence WHERE id='e-other'").fetchone()[0] == "FGSourceTwo"
        assert db.execute("SELECT content FROM cognition WHERE id='c-other'").fetchone()[0] == "FGSourceTwo"
