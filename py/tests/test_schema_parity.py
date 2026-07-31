"""schema parity:Python 建的库结构与 TS(shared/parity/schema.json)逐表逐列一致。"""
from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from conftest import parity

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


def test_existing_v1_db_migrates_retracted_cognition_before_stamping_v2(tmp_path: Any) -> None:
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
        assert user_version(upgraded) == 2
        assert upgraded.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
        assert upgraded.execute("SELECT COUNT(*) FROM cognition_evidence").fetchone()[0] == 0
        assert upgraded.execute("SELECT COUNT(*) FROM evidence_retraction").fetchone()[0] == 0
    finally:
        upgraded.close()


def test_future_python_schema_is_rejected_before_any_upgrade(tmp_path: Any) -> None:
    path = str(tmp_path / "future.db")
    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA user_version = 3")
        db.commit()
    finally:
        db.close()
    with pytest.raises(RuntimeError, match="higher"):
        open_db(path)
