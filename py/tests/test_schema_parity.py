"""schema parity:Python 建的库结构与 TS(shared/parity/schema.json)逐表逐列一致。"""
from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from conftest import parity

import memoweft.store.driver as store_driver
from memoweft.store import SqliteCognitionStore, SqliteEvidenceStore, open_db, user_version
from memoweft.types import CognitionInput, EvidenceInput, EvidenceLink


def _table_info(db: Any, table: str) -> list[dict[str, Any]]:
    rows = db.execute(f"SELECT name, type, \"notnull\" AS nn, dflt_value AS dflt, pk FROM pragma_table_info('{table}')").fetchall()
    return [{"name": r[0], "type": r[1], "notnull": int(r[2]), "dflt": r[3], "pk": int(r[4])} for r in rows]


def test_schema_matches_ts() -> None:
    want = parity("schema.json")
    db = open_db(":memory:")
    try:
        assert user_version(db) == want["userVersion"], "user_version 应与 TS 一致(LATEST_SCHEMA_VERSION)"
        got_tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        assert got_tables == sorted(want["tables"].keys()), f"表清单分叉:{got_tables} vs {sorted(want['tables'].keys())}"
        for table, want_cols in want["tables"].items():
            got_cols = _table_info(db, table)
            assert got_cols == want_cols, f"表 {table} 列结构分叉:\n got:  {got_cols}\n want: {want_cols}"
    finally:
        db.close()


def test_existing_v1_db_migrates_retracted_cognition_before_stamping_latest(tmp_path: Any) -> None:
    path = str(tmp_path / "rc1.db")
    db = open_db(path)
    evidence_store = SqliteEvidenceStore(db)
    cognition_store = SqliteCognitionStore(db)
    evidence = evidence_store.put(
        EvidenceInput(subject_id="owner", source_kind="spoken", host_id="test", raw_content="已撤回原话")
    )
    cognition = cognition_store.put(
        CognitionInput(
            subject_id="owner",
            content="旧派生认知",
            content_type="preference",
            formed_by="stated",
            confidence=600,
            cred_status="limited",
            evidence=[EvidenceLink(evidence_id=evidence.id, relation="support")],
        )
    )
    evidence_store.remove(evidence.id)
    db.execute(
        "INSERT INTO evidence_retraction (cognition_id, evidence_id, retracted_at) VALUES (?,?,?)",
        (cognition.id, evidence.id, "2026-07-30T00:00:00.000Z"),
    )
    db.execute("PRAGMA user_version = 1")
    db.close()

    upgraded = open_db(path)
    try:
        assert user_version(upgraded) == 4
        assert upgraded.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
        assert upgraded.execute("SELECT COUNT(*) FROM cognition_evidence").fetchone()[0] == 0
        assert upgraded.execute("SELECT COUNT(*) FROM evidence_retraction").fetchone()[0] == 0
    finally:
        upgraded.close()


def test_future_python_schema_is_rejected_before_any_upgrade(tmp_path: Any) -> None:
    path = str(tmp_path / "future.db")
    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA user_version = 5")
        db.commit()
    finally:
        db.close()
    with pytest.raises(RuntimeError, match="higher"):
        open_db(path)


def test_existing_v2_db_adds_world_and_identity_tables_without_losing_v1_data(tmp_path: Any) -> None:
    path = str(tmp_path / "v2.db")
    db = open_db(path)
    evidence_store = SqliteEvidenceStore(db)
    evidence = evidence_store.put(
        EvidenceInput(subject_id="owner", source_kind="spoken", host_id="test", raw_content="保留的 1.0 数据")
    )
    for table in ("identity_state", "cognition_transitions", "proposals", "evidence_ledger", "memory_state"):
        db.execute(f'DROP TABLE "{table}"')
    db.execute("PRAGMA user_version = 2")
    db.close()

    upgraded = open_db(path)
    try:
        assert user_version(upgraded) == 4
        assert upgraded.execute("SELECT raw_content FROM evidence WHERE id = ?", (evidence.id,)).fetchone()[0] == "保留的 1.0 数据"
        tables = {
            row[0]
            for row in upgraded.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        assert {"memory_state", "evidence_ledger", "proposals", "cognition_transitions", "identity_state"} <= tables
    finally:
        upgraded.close()


def test_existing_v3_world_rows_are_unchanged_by_v4_contract_stamp(tmp_path: Any) -> None:
    path = str(tmp_path / "v3.db")
    db = open_db(path)
    db.execute(
        "INSERT INTO evidence_ledger(id, content, payload_json) VALUES (?, ?, ?)",
        ("e:kept", "kept evidence", '{"id":"e:kept"}'),
    )
    db.execute(
        "INSERT INTO proposals(id, kind, base_revision, result_hash, payload_json, status) "
        "VALUES (?, 'addition', 0, ?, ?, 'reject')",
        ("review:kept", "sha256:kept", '{"delta":{},"evidence":[]}'),
    )
    before = tuple(
        db.execute(
            "SELECT id, kind, base_revision, result_hash, payload_json, review_payload_json, status "
            "FROM proposals"
        ).fetchone()
    )
    db.execute("PRAGMA user_version = 3")
    db.close()

    upgraded = open_db(path)
    try:
        assert user_version(upgraded) == 4
        after = tuple(
            upgraded.execute(
                "SELECT id, kind, base_revision, result_hash, payload_json, review_payload_json, status "
                "FROM proposals"
            ).fetchone()
        )
        assert after == before
        assert upgraded.execute(
            "SELECT id, content, payload_json FROM evidence_ledger"
        ).fetchone() == ("e:kept", "kept evidence", '{"id":"e:kept"}')
    finally:
        upgraded.close()


def test_v3_schema_migration_failure_rolls_back_version_and_partial_tables(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = str(tmp_path / "v2-failure.db")
    db = open_db(path)
    for table in ("identity_state", "cognition_transitions", "proposals", "evidence_ledger", "memory_state"):
        db.execute(f'DROP TABLE "{table}"')
    db.execute("PRAGMA user_version = 2")
    db.close()

    monkeypatch.setattr(
        store_driver,
        "WORLD_SCHEMA_SQL",
        (
            "CREATE TABLE stage3_partial (id TEXT PRIMARY KEY)",
            "THIS IS NOT VALID SQLITE",
        ),
    )
    with pytest.raises(RuntimeError, match="Migration v3 failed and was rolled back"):
        open_db(path)

    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 2
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'stage3_partial'"
        ).fetchone() is None
    finally:
        raw.close()
