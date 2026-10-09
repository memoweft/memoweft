"""Synthetic pre-upgrade stores must start without losing old memory."""
from pathlib import Path
import sqlite3

import pytest

from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
from memoweft.integrations.hermes import IncompatibleDatabaseError
from memoweft.store import open_db
from memoweft.store.schema import SCHEMA_VERSION


def legacy_store(home: Path, version: int) -> Path:
    path = home / "memoweft" / "memoweft.sqlite3"
    path.parent.mkdir()
    db = open_db(str(path))
    # v21 introduces only observed-source tables; reconstruct the real old shape.
    for name in ("observed_source", "observed_evidence_revision"):
        db.execute(f'DROP TABLE IF EXISTS "{name}"')
    if version < 19:
        db.execute("DROP TABLE portable_import_receipt")
    db.execute(
        "INSERT INTO evidence (id, subject_id, source_kind, host_id, occurred_at, "
        "recorded_at, raw_content, summary, allow_local_read, allow_cloud_read, allow_inference) VALUES "
        "('old-evidence', 'synthetic-owner', 'spoken', 'synthetic-host', "
        "'2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', '合成旧记忆', '合成旧记忆', 1, 0, 1)"
    )
    db.execute(
        "INSERT INTO cognition (id, subject_id, content, content_type, formed_by, "
        "confidence, cred_status, created_at, updated_at) VALUES "
        "('old-memory', 'synthetic-owner', '合成旧偏好：回答简短', 'preference', "
        "'stated', 800, 'trusted', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z')"
    )
    db.execute("INSERT INTO cognition_evidence VALUES ('old-memory', 'old-evidence', 'support')")
    db.execute(f"PRAGMA user_version = {version}")
    db.commit()
    db.close()
    return path


@pytest.mark.parametrize("version", [18, 19, 20])
def test_bridge_migrates_supported_legacy_store_and_preserves_evidence(
    tmp_path: Path, version: int,
) -> None:
    path = legacy_store(tmp_path, version)
    runtime = DshMemoWeftRuntime()
    try:
        runtime.initialize("synthetic-session", dsh_home=str(tmp_path),
                           subject_id="synthetic-owner", auto_route=False)
        assert runtime.enabled
        with sqlite3.connect(path) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert db.execute("SELECT raw_content FROM evidence WHERE id = 'old-evidence'").fetchone() == ("合成旧记忆",)
            assert db.execute("SELECT content FROM cognition WHERE id = 'old-memory'").fetchone() == ("合成旧偏好：回答简短",)
            assert db.execute("SELECT evidence_id FROM cognition_evidence WHERE cognition_id = 'old-memory'").fetchone() == ("old-evidence",)
        # A second startup exercises the post-migration physical validation too.
        runtime.shutdown()
        runtime.initialize("synthetic-session", dsh_home=str(tmp_path),
                           subject_id="synthetic-owner", auto_route=False)
        assert runtime.enabled
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("damage", ["identity", "columns", "future"])
def test_legacy_probe_still_rejects_damage_without_writes(tmp_path: Path, damage: str) -> None:
    path = legacy_store(tmp_path, 20)
    with sqlite3.connect(path) as db:
        if damage == "identity":
            db.execute("PRAGMA application_id = 0")
        elif damage == "future":
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        else:
            db.execute("ALTER TABLE relationship DROP COLUMN relation_type")
    before = path.read_bytes()
    runtime = DshMemoWeftRuntime()
    with pytest.raises(IncompatibleDatabaseError):
        runtime.initialize("synthetic-session", dsh_home=str(tmp_path),
                           subject_id="synthetic-owner", auto_route=False)
    assert path.read_bytes() == before
