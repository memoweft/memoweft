"""Schema v8: per-row raw-content hash binding for boundary Evidence.

The formal batch compiler must be able to prove that the raw content it
interprets is byte-for-byte the content the boundary actually accepted.
Schema v8 therefore binds every boundary-accepted Evidence row to the
SHA-256 of its exact UTF-8 raw content in the Python-owned
``boundary_evidence_content`` table (the frozen 1.x ``evidence`` table is
untouched).  Existing rows are backfilled deterministically during the
v6 -> v8 migration.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Any

from memoweft.integrations.hermes.boundary_store import HermesBoundaryStore
from memoweft.store import open_db

from test_hermes_boundary_store import _boundary, _candidate, _clock
from test_schema_parity import _create_frozen_python_v6


def _digest(raw: str) -> str:
    return sha256(raw.encode("utf-8")).hexdigest()


def test_fresh_schema_has_boundary_evidence_content_table() -> None:
    db = open_db(":memory:")
    try:
        cols = tuple(
            str(row[1])
            for row in db.execute("PRAGMA table_info(boundary_evidence_content)")
        )
        assert cols == ("evidence_id", "raw_content_hash")
    finally:
        db.close()


def test_frozen_v6_migration_backfills_content_hashes(tmp_path: Path) -> None:
    v6_path = tmp_path / "frozen-v6.sqlite3"
    _create_frozen_python_v6(v6_path)
    migrated = open_db(str(v6_path))
    try:
        rows = migrated.execute(
            "SELECT e.id, e.raw_content, b.raw_content_hash "
            "FROM evidence e "
            "JOIN boundary_evidence_content b ON b.evidence_id = e.id"
        ).fetchall()
        total = migrated.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
        # Every pre-existing row is bound; the hash matches its exact bytes.
        assert len(rows) == total > 0
        for _eid, raw, bound in rows:
            assert bound == _digest(str(raw))
    finally:
        migrated.close()


def test_accept_binds_each_evidence_content_hash(tmp_path: Path) -> None:
    db = open_db(str(tmp_path / "memoweft.sqlite3"))
    try:
        boundary = _boundary(
            _candidate("one", raw_content="first private user sentence"),
            _candidate("two", raw_content="second private user sentence"),
        )
        HermesBoundaryStore(db, clock=_clock).accept(boundary)
        rows = db.execute(
            "SELECT e.raw_content, b.raw_content_hash "
            "FROM evidence e "
            "JOIN boundary_evidence_content b ON b.evidence_id = e.id "
            "ORDER BY e.id"
        ).fetchall()
        assert len(rows) == 2
        for raw, bound in rows:
            assert bound == _digest(str(raw))
    finally:
        db.close()
