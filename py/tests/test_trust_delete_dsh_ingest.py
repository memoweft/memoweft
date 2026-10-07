"""World deletion must accept an ingested item's own provenance ledger."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from memoweft.integrations.trust.command_service import CommandService

from test_dsh_local_route import _boundary


@pytest.mark.parametrize("foreign_ledger", [None, "other_cognition", "cross_kind"])
def test_dsh_ingested_cognition_delete_respects_ledger_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, foreign_ledger: str | None
) -> None:
    monkeypatch.setenv("MEMOWEFT_TESTING", "1")
    monkeypatch.setenv("MEMOWEFT_TEST_MODEL_RESPONSE", "__smart__")
    monkeypatch.setattr(WorldJobWorker, "start", lambda self: None)
    monkeypatch.setattr(WorldJobWorker, "kick", lambda self: False)
    runtime = DshMemoWeftRuntime()
    try:
        runtime.initialize("s", dsh_home=str(tmp_path), model_tier="local", lang="zh")
        runtime.ingest_durable_boundary(_boundary())
        assert runtime._world_worker is not None
        assert runtime._world_worker.run_until_quiescent() == 1
        db_path, subject_id, host_id = runtime.db_path, runtime.subject_id, runtime.host_id
        assert db_path is not None and subject_id is not None and host_id is not None
        with sqlite3.connect(db_path) as db:
            cognition_id, evidence_id = db.execute(
                "SELECT cognition_id, evidence_id FROM cognition_evidence"
            ).fetchone()
            ledgers = [json.loads(row[0]) for row in db.execute(
                "SELECT content FROM evidence_ledger"
            )]
            assert any(row.get("cognition_id") == cognition_id and
                       row.get("evidence_id") == evidence_id for row in ledgers)
            revision = db.execute(
                "SELECT revision FROM memory_state WHERE singleton = 1"
            ).fetchone()[0]
            if foreign_ledger is not None:
                foreign = (
                    {"cognition_id": "cognition-another", "evidence_id": evidence_id}
                    if foreign_ledger == "other_cognition" else
                    {"cognition_id": cognition_id, "entity_id": "entity-another",
                     "evidence_id": evidence_id}
                )
                db.execute(
                    "INSERT INTO evidence_ledger (id, content, payload_json) VALUES (?, ?, ?)",
                    ("foreign-ledger", json.dumps(foreign), "{}"),
                )
        receipt = CommandService(
            db_path, subject_id=subject_id, host_id=host_id
        ).submit_command({
            "schema_version": 1,
            "command_id": "delete-dsh-cognition",
            "subject_id": subject_id,
            "actor": "owner",
            "expected_world_revision": revision,
            "operation": "delete_world_item",
            "target_kind": "cognition",
            "target_id": cognition_id,
            "payload": {},
            "submitted_at": "2026-09-27T00:00:00.000Z",
        })
        with sqlite3.connect(db_path) as db:
            cognition_count = db.execute(
                "SELECT COUNT(*) FROM cognition WHERE id = ?", (cognition_id,)
            ).fetchone()[0]
            raw, deleted_at = db.execute(
                "SELECT raw_content, deleted_at FROM evidence WHERE id = ?", (evidence_id,)
            ).fetchone()
        if foreign_ledger is None:
            assert receipt["result_state"] == "applied"
            assert cognition_count == 0
            assert raw == "" and deleted_at is not None
        else:
            assert receipt["result_state"] == "rejected"
            assert receipt["rejection_code"] == "source_evidence_shared"
            assert cognition_count == 1
            assert raw and deleted_at is None
    finally:
        runtime.shutdown()
