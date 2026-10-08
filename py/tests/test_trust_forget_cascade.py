"""FG-1: source erasure reaches identities/aliases and on-disk/portable copies."""
import json
from pathlib import Path

from memoweft.portable.builder import build_bundle
from test_trust_command_service import _open, _seed_evidence, _seed_revision, _service, _command, _SUBJECT, _HOST, _T0


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
        assert "保留这句话" in context


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
        assert "FGSourceCopy" not in context and "unrelated" in context
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
