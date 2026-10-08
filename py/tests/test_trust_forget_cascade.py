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
