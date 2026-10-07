"""schema parity:Python 建的库结构与 TS(shared/parity/schema.json)逐表逐列一致。"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from conftest import parity

import memoweft.store.driver as store_driver
from memoweft.store import SqliteCognitionStore, SqliteEvidenceStore, open_db, user_version
from memoweft.store.driver import IncompatibleSchemaError, application_id
from memoweft.store.schema import (
    ENTITY_COLUMNS,
    MEMORY_WORLD_JOB_COLUMNS,
    PORTABLE_IMPORT_RECEIPT_COLUMNS,
    PYTHON_APPLICATION_ID,
    SCHEMA_VERSION,
)
from memoweft.types import CognitionInput, EvidenceInput, EvidenceLink


FROZEN_PYTHON_V6_FIXTURE = (
    Path(__file__).parent / "fixtures" / "hermes_python_v6.sql"
)
FROZEN_PYTHON_V6_WHEEL_SHA256 = (
    "1310510146f863e5cfa154eabc35cc4166adc6c4f0306018ffaac2e2d65ec551"
)
FROZEN_PYTHON_V6_PAYLOAD_SHA256 = (
    "43045acd9bb0c8c51ad319c16a01acc44c3c7b87543fa54da36946ac1bec5702"
)
FROZEN_PYTHON_V6_PAYLOAD_MARKER = b"-- BEGIN FROZEN SQL PAYLOAD\n"


def _table_info(db: Any, table: str) -> list[dict[str, Any]]:
    rows = db.execute(f"SELECT name, type, \"notnull\" AS nn, dflt_value AS dflt, pk FROM pragma_table_info('{table}')").fetchall()
    return [{"name": r[0], "type": r[1], "notnull": int(r[2]), "dflt": r[3], "pk": int(r[4])} for r in rows]


def _schema_signature(db: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    """比较完整 named DDL；比只看列更能兜住 partial indexes/CHECK 漂移。"""
    return tuple(
        tuple(row)
        for row in db.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    )


def _drop_current_python_tables_and_stamp_legacy(db: sqlite3.Connection, version: int) -> None:
    """已有旧测试的 synthetic downgrade helper；移除 Python-owned 物理表后盖旧版本。"""
    db.execute("DROP TABLE portable_import_receipt")
    db.execute("DROP TABLE clarification")
    db.execute("DROP TABLE trust_command_receipt")
    db.execute("DROP TABLE trust_command")
    db.execute("DROP TABLE world_item_lifecycle")
    db.execute("DROP TABLE world_event_evidence")
    db.execute("DROP TABLE world_event")
    db.execute("DROP TABLE retraction")
    db.execute("DROP TABLE cognition_target")
    db.execute("DROP TABLE terminal_outcome")
    db.execute("DROP TABLE memory_world_job")
    db.execute("DROP TABLE boundary_evidence_content")
    db.execute("DROP TABLE relationship_evidence")
    db.execute("DROP TABLE relationship")
    db.execute("DROP TABLE entity")
    db.execute("PRAGMA application_id = 0")
    db.execute(f"PRAGMA user_version = {version}")


def _create_frozen_python_v6(path: Path) -> None:
    """从旧 v6 wheel 产出的冻结 SQL 建库；不读取当前 schema 常量或用户 DB。"""
    fixture = FROZEN_PYTHON_V6_FIXTURE.read_bytes()
    metadata, marker, payload = fixture.partition(FROZEN_PYTHON_V6_PAYLOAD_MARKER)
    assert marker == FROZEN_PYTHON_V6_PAYLOAD_MARKER
    assert fixture.count(FROZEN_PYTHON_V6_PAYLOAD_MARKER) == 1
    assert (
        f"-- source-wheel-sha256: {FROZEN_PYTHON_V6_WHEEL_SHA256}\n".encode()
        in metadata
    )
    assert (
        f"-- fixture-payload-sha256: {FROZEN_PYTHON_V6_PAYLOAD_SHA256}\n".encode()
        in metadata
    )
    assert sha256(payload).hexdigest() == FROZEN_PYTHON_V6_PAYLOAD_SHA256

    db = sqlite3.connect(path)
    try:
        db.executescript(payload.decode("utf-8"))
    finally:
        db.close()


def test_schema_matches_ts() -> None:
    want = parity("schema.json")
    db = open_db(":memory:")
    try:
        assert want["userVersion"] == 6, "shared parity remains the TypeScript v6 contract"
        assert user_version(db) == SCHEMA_VERSION
        assert application_id(db) == PYTHON_APPLICATION_ID
        got_tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        # The shared parity fixture remains the contract for the cross-runtime
        # 1.x store.  The persistent Next memory loop additionally owns its
        # replay receipt, which has no TypeScript equivalent to compare yet.
        assert set(want["tables"]) <= set(got_tables), f"缺少对拍表:{got_tables} vs {sorted(want['tables'])}"
        for table, want_cols in want["tables"].items():
            got_cols = _table_info(db, table)
            assert got_cols == want_cols, f"表 {table} 列结构分叉:\n got:  {got_cols}\n want: {want_cols}"
        assert _table_info(db, "proposal_decision_receipts") == [
            {"name": "proposal_id", "type": "TEXT", "notnull": 0, "dflt": None, "pk": 1},
            {"name": "offered_result_hash", "type": "TEXT", "notnull": 1, "dflt": None, "pk": 0},
            {"name": "effective_decision", "type": "TEXT", "notnull": 1, "dflt": None, "pk": 0},
            {"name": "world_revision", "type": "INTEGER", "notnull": 1, "dflt": None, "pk": 0},
            {"name": "snapshot_hash", "type": "TEXT", "notnull": 1, "dflt": None, "pk": 0},
            {"name": "decided_at", "type": "TEXT", "notnull": 1, "dflt": None, "pk": 0},
            {"name": "receipt_hash", "type": "TEXT", "notnull": 1, "dflt": None, "pk": 0},
        ]
        assert tuple(
            column["name"] for column in _table_info(db, "memory_world_job")
        ) == MEMORY_WORLD_JOB_COLUMNS
        assert {
            "outcome_id",
            "job_id",
            "terminal_state",
            "delivery_state",
            "claim_token",
        } <= {
            column["name"] for column in _table_info(db, "terminal_outcome")
        }
        indexes = {
            str(row[0])
            for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND tbl_name = 'memory_world_job'"
            ).fetchall()
        }
        assert {
            "ux_memory_world_job_boundary_event",
            "ix_memory_world_job_ready",
            "ix_memory_world_job_expired",
        } <= indexes
        terminal_outcome_indexes = {
            str(row[0])
            for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND tbl_name = 'terminal_outcome'"
            ).fetchall()
        }
        assert {
            "ix_terminal_outcome_ready",
            "ix_terminal_outcome_expired",
        } <= terminal_outcome_indexes
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
    _drop_current_python_tables_and_stamp_legacy(db, 1)
    db.close()

    upgraded = open_db(path)
    try:
        assert user_version(upgraded) == SCHEMA_VERSION
        assert upgraded.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
        assert upgraded.execute("SELECT COUNT(*) FROM cognition_evidence").fetchone()[0] == 0
        assert upgraded.execute("SELECT COUNT(*) FROM evidence_retraction").fetchone()[0] == 0
    finally:
        upgraded.close()


def test_future_python_schema_is_rejected_before_any_upgrade(tmp_path: Any) -> None:
    path = str(tmp_path / "future.db")
    db = sqlite3.connect(path)
    try:
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        db.commit()
    finally:
        db.close()
    with pytest.raises(RuntimeError, match="higher"):
        open_db(path)


_LEGACY_V9_ENTITY_DDL = """CREATE TABLE entity (
  id             TEXT    PRIMARY KEY,
  world_id       TEXT    NOT NULL,
  kind           TEXT    NOT NULL,
  canonical_name TEXT    NOT NULL,
  invalid_at     TEXT,
  created_at     TEXT    NOT NULL,
  updated_at     TEXT    NOT NULL
)"""


def test_v10_legacy_entity_text_migrates_and_validates(tmp_path: Any) -> None:
    """Live-database shape: entity was created by the pre-v11 v9 DDL (no
    aliases_json) and is ALTER-migrated to v11.  sqlite_master keeps the
    legacy CREATE text, so the exact-text fingerprint must not reject the
    migrated table; the column-order contract (aliases_json last) does."""
    path = str(tmp_path / "v10-legacy-entity.db")
    raw = sqlite3.connect(path)
    try:
        fresh = open_db(":memory:")
        try:
            statements = [
                row[1]
                for row in fresh.execute(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%'"
                ).fetchall()
                # v10 predates the v12/v13 objects: exclude them so the
                # simulated v10 database lacks those tables.
                if row[1] is not None
                and str(row[0])
                not in (
                    "retraction",
                    "ix_retraction_prior",
                    "world_event",
                    "ix_world_event_world",
                    "ix_world_event_occurred",
                    "world_event_evidence",
                    "ix_wev_evidence",
                    "terminal_outcome",
                    "ix_terminal_outcome_ready",
                    "ix_terminal_outcome_expired",
                    "trust_command",
                    "ix_trust_command_subject",
                    "trust_command_receipt",
                        "world_item_lifecycle",
                        "ix_world_item_lifecycle_current",
                        "clarification",
                        "ix_clarification_session_state",
                        "ix_clarification_follow_up",
                        "portable_import_receipt",
                        "ix_portable_import_receipt_target",
                    )
            ]
        finally:
            fresh.close()
        for stmt in statements:
            raw.execute(stmt)
        raw.execute(f"PRAGMA application_id = {PYTHON_APPLICATION_ID}")
        # Rewind entity to the legacy physical shape and stamp v10.
        raw.execute("DROP TABLE entity")
        raw.execute(_LEGACY_V9_ENTITY_DDL)
        raw.execute(
            "CREATE UNIQUE INDEX ux_entity_world_name\n"
            "ON entity(world_id, canonical_name) WHERE invalid_at IS NULL"
        )
        raw.execute("PRAGMA user_version = 10")
        raw.commit()
    finally:
        raw.close()

    upgraded = open_db(path)
    try:
        assert user_version(upgraded) == SCHEMA_VERSION
        columns = tuple(
            str(row[1]) for row in upgraded.execute("PRAGMA table_info(entity)")
        )
        assert columns == ENTITY_COLUMNS
    finally:
        upgraded.close()
    # Re-opening at the current version takes the line-353 validation path.
    reopened = open_db(path)
    try:
        assert user_version(reopened) == SCHEMA_VERSION
    finally:
        reopened.close()


def test_existing_v2_db_adds_world_and_identity_tables_without_losing_v1_data(tmp_path: Any) -> None:
    path = str(tmp_path / "v2.db")
    db = open_db(path)
    evidence_store = SqliteEvidenceStore(db)
    evidence = evidence_store.put(
        EvidenceInput(subject_id="owner", source_kind="spoken", host_id="test", raw_content="保留的 1.0 数据")
    )
    for table in ("identity_state", "cognition_transitions", "proposal_decision_receipts", "proposals", "evidence_ledger", "memory_state"):
        db.execute(f'DROP TABLE "{table}"')
    _drop_current_python_tables_and_stamp_legacy(db, 2)
    db.close()

    upgraded = open_db(path)
    try:
        assert user_version(upgraded) == SCHEMA_VERSION
        assert upgraded.execute("SELECT raw_content FROM evidence WHERE id = ?", (evidence.id,)).fetchone()[0] == "保留的 1.0 数据"
        tables = {
            row[0]
            for row in upgraded.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        assert {"memory_state", "evidence_ledger", "proposals", "proposal_decision_receipts", "cognition_transitions", "identity_state"} <= tables
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
    _drop_current_python_tables_and_stamp_legacy(db, 3)
    db.close()

    upgraded = open_db(path)
    try:
        assert user_version(upgraded) == SCHEMA_VERSION
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


def test_existing_v4_product_bundle_rows_are_unchanged_by_v5_contract_stamp(tmp_path: Any) -> None:
    path = str(tmp_path / "v4-product-bundle.db")
    db = open_db(path)
    db.execute(
        "INSERT INTO evidence_ledger(id, content, payload_json) VALUES (?, ?, ?)",
        ("e:v5-kept", "kept evidence", '{"id":"e:v5-kept"}'),
    )
    db.execute(
        "INSERT INTO proposals(id, kind, base_revision, result_hash, payload_json, status) "
        "VALUES (?, 'product_bundle', 7, ?, ?, 'accept')",
        (
            "review:v5-kept",
            "sha256:v5-kept",
            '{"productBundle":{"relationshipEvolutionSteps":[{"kind":"successor"}]}}',
        ),
    )
    before = tuple(
        db.execute(
            "SELECT id, kind, base_revision, result_hash, payload_json, review_payload_json, status "
            "FROM proposals"
        ).fetchone()
    )
    _drop_current_python_tables_and_stamp_legacy(db, 4)
    db.close()

    upgraded = open_db(path)
    try:
        assert user_version(upgraded) == SCHEMA_VERSION
        assert tuple(
            upgraded.execute(
                "SELECT id, kind, base_revision, result_hash, payload_json, review_payload_json, status "
                "FROM proposals"
            ).fetchone()
        ) == before
        assert upgraded.execute(
            "SELECT id, content, payload_json FROM evidence_ledger"
        ).fetchone() == ("e:v5-kept", "kept evidence", '{"id":"e:v5-kept"}')
    finally:
        upgraded.close()


def test_existing_v5_cognition_evidence_update_rows_are_byte_unchanged_by_v6_contract_stamp(
    tmp_path: Any,
) -> None:
    path = str(tmp_path / "v5-cognition-evidence-update.db")
    evidence_payload = (
        '{"id":"e:v6-kept","metadata":{"occurred_at":"2026-08-13T12:00:00Z"}}'
    )
    proposal_payload = (
        '{"cognition_updates":[{"id":"cog:v6-kept","sources":['
        '{"evidence_id":"e:v6-kept","relation":"support"}]}],'
        '"delta":{"source_evidence_ids":["e:v6-kept"]},'
        '"evolution_steps":[{"kind":"cognition_change","relation":"reaffirms"}]}'
    )
    review_payload = '{"display":"再次确认","confidence_before":600,"confidence_after":640}'
    db = open_db(path)
    db.execute(
        "INSERT INTO evidence_ledger(id, content, payload_json) VALUES (?, ?, ?)",
        ("e:v6-kept", "李华很可靠。", evidence_payload),
    )
    db.execute(
        "INSERT INTO proposals(id, kind, base_revision, result_hash, payload_json, "
        "review_payload_json, status) VALUES (?, 'product_bundle', 8, ?, ?, ?, 'accept')",
        ("review:v6-kept", "sha256:v6-kept", proposal_payload, review_payload),
    )
    evidence_sql = (
        "SELECT id, content, payload_json, typeof(payload_json), "
        "length(CAST(payload_json AS BLOB)), hex(CAST(payload_json AS BLOB)) "
        "FROM evidence_ledger WHERE id = 'e:v6-kept'"
    )
    proposal_sql = (
        "SELECT id, kind, base_revision, result_hash, payload_json, review_payload_json, status, "
        "typeof(payload_json), length(CAST(payload_json AS BLOB)), "
        "hex(CAST(payload_json AS BLOB)), hex(CAST(review_payload_json AS BLOB)) "
        "FROM proposals WHERE id = 'review:v6-kept'"
    )
    schema_sql = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    )
    before_evidence = tuple(db.execute(evidence_sql).fetchone())
    before_proposal = tuple(db.execute(proposal_sql).fetchone())
    _drop_current_python_tables_and_stamp_legacy(db, 5)
    before_schema = tuple(tuple(row) for row in db.execute(schema_sql).fetchall())
    db.close()

    upgraded = open_db(path)
    try:
        assert user_version(upgraded) == SCHEMA_VERSION
        assert tuple(upgraded.execute(evidence_sql).fetchone()) == before_evidence
        assert tuple(upgraded.execute(proposal_sql).fetchone()) == before_proposal
        after_schema = {
            (str(row[0]), str(row[1])): tuple(row)
            for row in upgraded.execute(schema_sql).fetchall()
        }
        for row in before_schema:
            assert after_schema[(str(row[0]), str(row[1]))] == row
        assert "memory_world_job" in {
            row[0]
            for row in upgraded.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        upgraded.close()


def test_existing_current_db_missing_world_job_fails_closed_without_self_heal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v7-missing-world-job.sqlite3"
    db = open_db(str(path))
    db.execute("DROP TABLE memory_world_job")
    assert user_version(db) == SCHEMA_VERSION
    assert application_id(db) == PYTHON_APPLICATION_ID
    db.close()
    before = path.read_bytes()

    with pytest.raises(IncompatibleSchemaError, match="incompatible physical schema"):
        open_db(str(path))

    assert path.read_bytes() == before
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert raw.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'memory_world_job'"
        ).fetchone() is None
    finally:
        raw.close()


def test_real_v15_to_v16_adds_terminal_outcome_and_converges_with_fresh_schema(
    tmp_path: Path,
) -> None:
    """A genuine v15 file has the Python marker and every pre-v16 object."""

    path = tmp_path / "real-v15.sqlite3"
    v15 = open_db(str(path))
    try:
        world_result_json = '{"reason":"historical_no_change","schema_version":1,"state":"no_change"}'
        v15.execute(
            """INSERT INTO memory_world_job (
                 job_id, job_schema_version, boundary_event_id,
                 boundary_payload_hash, boundary_schema_version, provider_name,
                 parent_session_id, result_session_id, boundary_mode,
                 formal_target_json, formal_target_hash, subject_id, host_id,
                 evidence_ids_json, state, attempts, world_result_json,
                 result_hash, delivery_receipt_json, delivery_receipt_hash,
                 created_at, completed_at, terminal_state
               ) VALUES (
                 'historical-terminal-job', 1, 'historical-boundary',
                 ?, 1, 'memoweft', 'parent-session', 'result-session',
                 'in_place', '{}', ?, 'subject-1', 'hermes:test', '[]',
                 'no_change', 1, ?, ?, '{}', ?, ?, ?, 'no_change'
               )""",
            (
                "b" * 64,
                sha256(b"{}").hexdigest(),
                world_result_json,
                sha256(world_result_json.encode()).hexdigest(),
                sha256(b"{}").hexdigest(),
                "2026-08-24T12:00:00.000Z",
                "2026-08-24T12:00:01.000Z",
            ),
        )
        historical_job = v15.execute(
            "SELECT * FROM memory_world_job WHERE job_id = 'historical-terminal-job'"
        ).fetchone()
        assert historical_job is not None
        v15.execute("DROP TABLE portable_import_receipt")
        v15.execute("DROP TABLE terminal_outcome")
        v15.execute("DROP TABLE clarification")
        v15.execute("DROP TABLE trust_command_receipt")
        v15.execute("DROP TABLE trust_command")
        v15.execute("DROP TABLE world_item_lifecycle")
        v15.execute("PRAGMA user_version = 15")
        assert application_id(v15) == PYTHON_APPLICATION_ID
        assert user_version(v15) == 15
    finally:
        v15.close()

    migrated = open_db(str(path))
    try:
        assert user_version(migrated) == SCHEMA_VERSION
        assert migrated.execute(
            "SELECT * FROM memory_world_job WHERE job_id = 'historical-terminal-job'"
        ).fetchone() == historical_job
        assert migrated.execute("SELECT COUNT(*) FROM terminal_outcome").fetchone() == (0,)
        assert tuple(
            str(row[1]) for row in migrated.execute("PRAGMA table_info(terminal_outcome)")
        ) == (
            "outcome_id", "schema_version", "job_id", "boundary_event_id",
            "provider_name", "subject_id", "parent_session_id", "result_session_id",
            "terminal_state", "terminal_detail", "world_revision", "world_result_json",
            "result_hash", "occurred_at", "delivery_state", "attempts",
            "next_attempt_at", "claim_owner", "claim_token", "lease_expires_at",
            "heartbeat_at", "delivered_at", "last_error",
        )
        migrated_signature = _schema_signature(migrated)
    finally:
        migrated.close()

    fresh = open_db(str(tmp_path / "fresh-v16.sqlite3"))
    try:
        assert _schema_signature(fresh) == migrated_signature
    finally:
        fresh.close()


def test_v16_schema_migration_failure_rolls_back_version_and_partial_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "v16-failure.sqlite3"
    v15 = open_db(str(path))
    try:
        v15.execute("DROP TABLE portable_import_receipt")
        v15.execute("DROP TABLE terminal_outcome")
        v15.execute("DROP TABLE clarification")
        v15.execute("DROP TABLE trust_command_receipt")
        v15.execute("DROP TABLE trust_command")
        v15.execute("DROP TABLE world_item_lifecycle")
        v15.execute("PRAGMA user_version = 15")
        assert user_version(v15) == 15
        assert application_id(v15) == PYTHON_APPLICATION_ID
    finally:
        v15.close()
    before = path.read_bytes()

    monkeypatch.setattr(
        store_driver,
        "TERMINAL_OUTCOME_SCHEMA_SQL",
        (
            "CREATE TABLE stage16_partial (id TEXT PRIMARY KEY)",
            "CREATE INDEX ix_stage16_partial ON stage16_partial(id)",
            "THIS IS NOT VALID SQLITE",
        ),
    )
    with pytest.raises(RuntimeError, match="Migration v16 failed and was rolled back"):
        open_db(str(path))

    assert path.read_bytes() == before
    raw = sqlite3.connect(path)
    try:
        assert user_version(raw) == 15
        assert application_id(raw) == PYTHON_APPLICATION_ID
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'stage16_partial'"
        ).fetchone() is None
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'ix_stage16_partial'"
        ).fetchone() is None
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'terminal_outcome'"
        ).fetchone() is None
    finally:
        raw.close()


@pytest.mark.parametrize(
    "damage", ("DROP TABLE terminal_outcome", "ALTER TABLE terminal_outcome ADD COLUMN damaged TEXT")
)
def test_current_v16_missing_or_damaged_terminal_outcome_fails_closed_without_self_heal(
    tmp_path: Path, damage: str
) -> None:
    path = tmp_path / "damaged-v16-terminal-outcome.sqlite3"
    db = open_db(str(path))
    try:
        db.execute(damage)
        assert user_version(db) == SCHEMA_VERSION
        assert application_id(db) == PYTHON_APPLICATION_ID
    finally:
        db.close()
    before = path.read_bytes()

    with pytest.raises(IncompatibleSchemaError, match="incompatible"):
        open_db(str(path))

    assert path.read_bytes() == before


def test_real_v17_to_v18_adds_clarification_and_converges_with_fresh_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "real-v17.sqlite3"
    v17 = open_db(str(path))
    try:
        v17.execute("DROP TABLE portable_import_receipt")
        v17.execute("DROP TABLE clarification")
        v17.execute("PRAGMA user_version = 17")
        assert application_id(v17) == PYTHON_APPLICATION_ID
        assert user_version(v17) == 17
    finally:
        v17.close()

    migrated = open_db(str(path))
    try:
        assert user_version(migrated) == SCHEMA_VERSION == 21
        assert migrated.execute("SELECT COUNT(*) FROM clarification").fetchone() == (0,)
        migrated_signature = _schema_signature(migrated)
    finally:
        migrated.close()

    fresh = open_db(str(tmp_path / "fresh-v18.sqlite3"))
    try:
        assert _schema_signature(fresh) == migrated_signature
    finally:
        fresh.close()


def test_v18_schema_migration_failure_rolls_back_version_and_partial_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "v18-failure.sqlite3"
    v17 = open_db(str(path))
    try:
        v17.execute("DROP TABLE portable_import_receipt")
        v17.execute("DROP TABLE clarification")
        v17.execute("PRAGMA user_version = 17")
    finally:
        v17.close()
    before = path.read_bytes()

    monkeypatch.setattr(
        store_driver,
        "CLARIFICATION_SCHEMA_SQL",
        (
            "CREATE TABLE stage18_partial (id TEXT PRIMARY KEY)",
            "CREATE INDEX ix_stage18_partial ON stage18_partial(id)",
            "THIS IS NOT VALID SQLITE",
        ),
    )
    with pytest.raises(RuntimeError, match="Migration v18 failed and was rolled back"):
        open_db(str(path))

    assert path.read_bytes() == before
    raw = sqlite3.connect(path)
    try:
        assert user_version(raw) == 17
        assert application_id(raw) == PYTHON_APPLICATION_ID
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'stage18_partial'"
        ).fetchone() is None
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'ix_stage18_partial'"
        ).fetchone() is None
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'clarification'"
        ).fetchone() is None
    finally:
        raw.close()


def test_real_v18_to_v19_adds_portable_receipt_and_converges_with_fresh_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "real-v18.sqlite3"
    v18 = open_db(str(path))
    try:
        v18.execute("DROP TABLE portable_import_receipt")
        v18.execute("PRAGMA user_version = 18")
        assert application_id(v18) == PYTHON_APPLICATION_ID
        assert user_version(v18) == 18
    finally:
        v18.close()

    migrated = open_db(str(path))
    try:
        assert user_version(migrated) == SCHEMA_VERSION == 21
        assert migrated.execute(
            "SELECT COUNT(*) FROM portable_import_receipt"
        ).fetchone() == (0,)
        assert tuple(
            str(row[1])
            for row in migrated.execute(
                "PRAGMA table_info(portable_import_receipt)"
            )
        ) == PORTABLE_IMPORT_RECEIPT_COLUMNS
        migrated_signature = _schema_signature(migrated)
    finally:
        migrated.close()

    fresh = open_db(str(tmp_path / "fresh-v19.sqlite3"))
    try:
        assert _schema_signature(fresh) == migrated_signature
    finally:
        fresh.close()


def test_v19_schema_migration_failure_rolls_back_version_and_partial_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "v19-failure.sqlite3"
    v18 = open_db(str(path))
    try:
        v18.execute("DROP TABLE portable_import_receipt")
        v18.execute("PRAGMA user_version = 18")
    finally:
        v18.close()
    before = path.read_bytes()

    monkeypatch.setattr(
        store_driver,
        "PORTABLE_IMPORT_RECEIPT_SCHEMA_SQL",
        (
            "CREATE TABLE stage19_partial (id TEXT PRIMARY KEY)",
            "CREATE INDEX ix_stage19_partial ON stage19_partial(id)",
            "THIS IS NOT VALID SQLITE",
        ),
    )
    with pytest.raises(RuntimeError, match="Migration v19 failed and was rolled back"):
        open_db(str(path))

    assert path.read_bytes() == before
    raw = sqlite3.connect(path)
    try:
        assert user_version(raw) == 18
        assert application_id(raw) == PYTHON_APPLICATION_ID
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'stage19_partial'"
        ).fetchone() is None
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'ix_stage19_partial'"
        ).fetchone() is None
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'portable_import_receipt'"
        ).fetchone() is None
    finally:
        raw.close()


def test_frozen_python_v6_migrates_to_current_and_converges_with_fresh_schema(
    tmp_path: Path,
) -> None:
    migrated_path = tmp_path / "synthetic-frozen-python-v6.sqlite3"
    _create_frozen_python_v6(migrated_path)
    raw = sqlite3.connect(migrated_path)
    try:
        assert user_version(raw) == 6
        assert application_id(raw) == 0
        assert raw.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'proposal_decision_receipts'"
        ).fetchone() == (1,)
        assert raw.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'memory_world_job'"
        ).fetchone() is None
        before_schema = {
            (str(row[0]), str(row[1])): tuple(row)
            for row in _schema_signature(raw)
        }
        before_evidence = tuple(
            raw.execute(
                "SELECT id, subject_id, raw_content, preceding_ai_context "
                "FROM evidence WHERE id = 'e:synthetic-v6'"
            ).fetchone()
        )
        before_evidence_bytes = tuple(
            raw.execute(
                "SELECT CAST(raw_content AS BLOB), "
                "CAST(preceding_ai_context AS BLOB) "
                "FROM evidence WHERE id = 'e:synthetic-v6'"
            ).fetchone()
        )
        before_receipt = tuple(
            raw.execute(
                "SELECT proposal_id, offered_result_hash, effective_decision, "
                "world_revision, snapshot_hash, decided_at, receipt_hash "
                "FROM proposal_decision_receipts "
                "WHERE proposal_id = 'proposal:synthetic-v6'"
            ).fetchone()
        )
    finally:
        raw.close()

    migrated = open_db(str(migrated_path))
    try:
        assert user_version(migrated) == SCHEMA_VERSION
        assert application_id(migrated) == PYTHON_APPLICATION_ID
        assert migrated.execute(
            "SELECT COUNT(*) FROM memory_world_job"
        ).fetchone() == (0,)
        assert tuple(
            migrated.execute(
                "SELECT id, subject_id, raw_content, preceding_ai_context "
                "FROM evidence WHERE id = 'e:synthetic-v6'"
            ).fetchone()
        ) == before_evidence
        assert tuple(
            migrated.execute(
                "SELECT CAST(raw_content AS BLOB), "
                "CAST(preceding_ai_context AS BLOB) "
                "FROM evidence WHERE id = 'e:synthetic-v6'"
            ).fetchone()
        ) == before_evidence_bytes
        assert tuple(
            migrated.execute(
                "SELECT proposal_id, offered_result_hash, effective_decision, "
                "world_revision, snapshot_hash, decided_at, receipt_hash "
                "FROM proposal_decision_receipts "
                "WHERE proposal_id = 'proposal:synthetic-v6'"
            ).fetchone()
        ) == before_receipt
        migrated_schema = _schema_signature(migrated)
        migrated_by_name = {
            (str(row[0]), str(row[1])): row for row in migrated_schema
        }
        for key, row in before_schema.items():
            assert migrated_by_name[key] == row
    finally:
        migrated.close()

    fresh = open_db(str(tmp_path / "fresh-python-v7.sqlite3"))
    try:
        assert user_version(fresh) == SCHEMA_VERSION
        assert application_id(fresh) == PYTHON_APPLICATION_ID
        assert _schema_signature(fresh) == migrated_schema
    finally:
        fresh.close()


def test_python_v6_missing_owner_marker_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "not-python-owned-v6.sqlite3"
    _create_frozen_python_v6(path)
    db = sqlite3.connect(path)
    db.execute("DROP TABLE proposal_decision_receipts")
    db.commit()
    db.close()
    before = path.read_bytes()

    with pytest.raises(IncompatibleSchemaError, match="Python v6"):
        open_db(str(path))

    assert path.read_bytes() == before


def test_python_v6_wrong_owned_index_definition_is_rejected_before_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wrong-python-v6-index.sqlite3"
    _create_frozen_python_v6(path)
    db = sqlite3.connect(path)
    try:
        db.execute("DROP INDEX ix_evidence_occurred")
        db.execute(
            "CREATE INDEX ix_evidence_occurred ON evidence(recorded_at)"
        )
        db.commit()
    finally:
        db.close()
    before = path.read_bytes()

    with pytest.raises(IncompatibleSchemaError, match="object definitions"):
        open_db(str(path))

    assert path.read_bytes() == before
    raw = sqlite3.connect(path)
    try:
        assert user_version(raw) == 6
        assert application_id(raw) == 0
        assert raw.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'ix_evidence_occurred'"
        ).fetchone() == (
            "CREATE INDEX ix_evidence_occurred ON evidence(recorded_at)",
        )
    finally:
        raw.close()


def test_v7_schema_migration_failure_rolls_back_version_app_id_and_partial_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "v7-failure.sqlite3"
    _create_frozen_python_v6(path)
    monkeypatch.setattr(
        store_driver,
        "WORLD_JOB_SCHEMA_SQL",
        (
            "CREATE TABLE stage7_partial (id TEXT PRIMARY KEY)",
            "PRAGMA application_id = 1234",
            "THIS IS NOT VALID SQLITE",
        ),
    )
    with pytest.raises(RuntimeError, match="Migration v7 failed and was rolled back"):
        open_db(str(path))

    raw = sqlite3.connect(path)
    try:
        assert user_version(raw) == 6
        assert application_id(raw) == 0
        assert raw.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'stage7_partial'"
        ).fetchone() is None
        assert raw.execute(
            "SELECT raw_content FROM evidence WHERE id = 'e:synthetic-v6'"
        ).fetchone() == ("synthetic v6 evidence",)
    finally:
        raw.close()


def test_existing_current_db_with_wrong_application_id_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wrong-application-id.sqlite3"
    db = open_db(str(path))
    db.execute("PRAGMA application_id = 1234")
    db.close()
    before = path.read_bytes()

    with pytest.raises(IncompatibleSchemaError, match="application_id"):
        open_db(str(path))

    assert path.read_bytes() == before


def test_existing_current_db_with_wrong_owned_table_definition_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wrong-current-table.sqlite3"
    db = open_db(str(path))
    db.execute("ALTER TABLE evidence ADD COLUMN unexpected_column TEXT")
    db.close()
    before = path.read_bytes()

    with pytest.raises(IncompatibleSchemaError, match="object definitions"):
        open_db(str(path))

    assert path.read_bytes() == before
    raw = sqlite3.connect(path)
    try:
        assert user_version(raw) == SCHEMA_VERSION
        assert application_id(raw) == PYTHON_APPLICATION_ID
        assert "unexpected_column" in {
            str(row[1]) for row in raw.execute("PRAGMA table_info(evidence)")
        }
    finally:
        raw.close()


def test_v3_schema_migration_failure_rolls_back_version_and_partial_tables(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = str(tmp_path / "v2-failure.db")
    db = open_db(path)
    for table in ("identity_state", "cognition_transitions", "proposal_decision_receipts", "proposals", "evidence_ledger", "memory_state"):
        db.execute(f'DROP TABLE "{table}"')
    _drop_current_python_tables_and_stamp_legacy(db, 2)
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
