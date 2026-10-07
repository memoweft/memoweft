"""MW-2 lifecycle parity and real RPC/source privacy boundaries (zero model calls)."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, cast

import pytest

from conftest import parity
from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
from memoweft.integrations.dsh_bridge.observed import ObservedError, ObservedService
from memoweft.integrations.dsh_bridge.protocol_v2 import DshRpcV2Server
from memoweft.integrations.trust import QueryService, PortableService
from memoweft.store import open_db, SqliteEvidenceStore, SqliteCognitionStore
from memoweft.types import EvidenceInput, CognitionInput, EvidenceLink, InteractionContextInput, VisibleTurn
from memoweft.store.interaction_context import SqliteInteractionContextStore
from memoweft.integrations.dsh_bridge.interactions import query_interactions


def test_observed_shared_lifecycle_parity_and_restart(tmp_path: Path) -> None:
    fixture = parity("observed.json")
    path = tmp_path / "observed.db"
    open_db(str(path)).close()
    for step in fixture["steps"]:
        service = ObservedService(path, subject_id=fixture["subject"], host_id=fixture["host"])
        evidence = {"source_key": fixture["source_key"], **fixture["evidence"],
            **({"version": step["version"]} if step.get("version") else {}),
            **({"content": step["content"]} if step.get("content") else {})}
        params = ({"evidence": evidence} if step["operation"] == "upsert_observed" else
            {"source_key": fixture["source_key"], "withdrawn_through": step["version"]}
            if step["operation"] == "retract_observed" else
            {"source_key": fixture["source_key"], "permission_version": step["version"], "permissions": {
                "allow_local_read": step.get("local_read", True), "allow_cloud_read": step["cloud_read"],
                "allow_inference": step.get("inference", True)}})
        if step.get("error"):
            with pytest.raises(ObservedError, match=step["error"]):
                service.execute(step["operation"], params)
        else:
            assert service.execute(step["operation"], params)["result_state"] == step["state"]
        for tier in ("local", "cloud"):
            result = QueryService(path, subject_id=fixture["subject"]).preview_recall("睡眠", model_tier=tier)
            assert bool(cast(dict[str, Any], result["preview"])["count"]) == step[tier]
    export = PortableService(path, subject_id=fixture["subject"], host_id=fixture["host"]).export_bundle()
    assert "睡眠" not in json.dumps(export, ensure_ascii=False)
    assert "HRV" not in path.read_bytes().decode(errors="replace")


def test_rpc_typed_observed_not_chat_and_source_isolation(tmp_path: Path) -> None:
    server = DshRpcV2Server(DshMemoWeftRuntime())
    ordinal = 0
    def call(method: str, params: dict[str, object], request_id: str | None = None) -> dict[str, Any]:
        nonlocal ordinal
        ordinal += 1
        return server.handle({"protocol": "memoweft.dsh_rpc", "protocol_version": 2, "schema_version": 1,
            "request_id": request_id or str(ordinal), "method": method, "params": params})
    assert call("initialize", {"dsh_home": str(tmp_path), "subject_id": "rpc-observed-owner", "auto_route": False})["ok"]
    try:
        fixture = parity("observed.json")
        e = {"source_key": fixture["source_key"], **fixture["evidence"]}
        result = call("upsert_observed", {"evidence": e}, "original-write")
        assert result["ok"] is True, result
        assert result["result"]["model_call_count"] == 0
        assert call("upsert_observed", {"evidence": {**e, "role": "user"}})["error"]["code"] == "invalid_observed_parameter"
        assert call("upsert_observed", {"evidence": {**e, "source_kind": "spoken"}})["ok"] is False
        assert call("preview_recall", {"query": "睡眠", "model_tier": "local"})["result"]["preview"]["count"] == 1
        assert call("preview_recall", {"query": "睡眠", "model_tier": "cloud"})["result"]["preview"]["count"] == 0
        assert call("preview_recall", {"query": "睡眠", "model_tier": "remote"})["error"]["code"] == "invalid_model_tier"
        assert call("retract_observed", {"source_key": e["source_key"], "withdrawn_through": "2026-10-02T00:00:00Z"})["result"]["storage_cleanup"]["state"] == "complete"
        assert call("upsert_observed", {"evidence": e}, "original-write")["error"]["code"] == "observed_source_withdrawn"
        path = server.runtime.db_path
        assert path is not None
        other = ObservedService(path, subject_id="other-owner", host_id="other-host")
        assert other.execute("upsert_observed", {"evidence": e})["result_state"] == "applied"
        assert call("preview_recall", {"query": "睡眠", "model_tier": "local"})["result"]["preview"]["count"] == 0
    finally:
        server.runtime.shutdown()


def test_mixed_derivations_and_assistant_dependency_cannot_bypass_opt_out(tmp_path: Path) -> None:
    fixture = parity("observed.json")
    subject, host = fixture["subject"], fixture["host"]
    path = tmp_path / "memory.db"
    db = open_db(str(path))
    service = ObservedService(path, subject_id=subject, host_id=host)
    result = service.execute("upsert_observed", {"evidence": {"source_key": fixture["source_key"], **fixture["evidence"]}})
    evidence_id = str(result["evidence_id"])
    public = SqliteEvidenceStore(db).put(EvidenceInput(subject_id=subject, source_kind="spoken", host_id=host,
        raw_content="睡眠前喜欢阅读", allow_cloud_read=True))
    cognition = SqliteCognitionStore(db).put(CognitionInput(subject_id=subject, content="睡眠前喜欢阅读",
        content_type="preference", formed_by="stated", confidence=800, cred_status="stable",
        evidence=[EvidenceLink(public.id, "support")]))
    mixed = SqliteCognitionStore(db).put(CognitionInput(subject_id=subject, content="睡眠与阅读有关",
        content_type="hypothesis", formed_by="inferred", confidence=500, cred_status="limited",
        evidence=[EvidenceLink(public.id, "support"), EvidenceLink(evidence_id, "support")]))
    dependency = {"schema_version": 1, "capture_status": "complete", "world_items": [
        {"object_kind": "cognition", "item_id": "state:" + evidence_id}], "interaction_ids": []}
    # Source-independent user history with an assistant reply which consumed health.
    store = SqliteInteractionContextStore(db)
    context = store.record(InteractionContextInput(subject_id=subject, conversation_id="old-chat", episode_id="public-episode",
        context=[VisibleTurn(role="user",content="以前聊过睡眠吗",message_id="q"),
                 VisibleTurn(role="assistant",content="睡眠 5 小时，HRV 偏低",message_id="a",model_context_dependencies=dependency)]))
    # Use a real conversation job so interaction source eligibility is independent
    # of the observed evidence. The production boundary schema is populated by its store.
    from memoweft.integrations.hermes.boundary_store import HermesBoundaryStore, HermesBoundaryFormalTarget, HermesBoundaryEvidenceCandidate, ValidatedHermesBoundary
    boundary = ValidatedHermesBoundary(event_id="public-episode", payload_hash="a" * 64,
        formal_target=HermesBoundaryFormalTarget(boundary_schema_version=1,provider_name="memoweft",
            parent_session_id="old-chat",result_session_id="old-chat",mode="in_place",subject_id=subject,host_id=host),
        evidence=(HermesBoundaryEvidenceCandidate(raw_content=public.raw_content,origin_id="public-source",occurred_at=public.occurred_at),))
    HermesBoundaryStore(db).accept(boundary)
    db.execute("UPDATE memory_world_job SET evidence_ids_json=? WHERE boundary_event_id='public-episode'", (json.dumps([public.id]),))
    backup = PortableService(path, subject_id=subject, host_id=host).export_bundle()
    db.execute("INSERT INTO evidence_ledger VALUES ('derived-ledger',?,?)", (json.dumps({"cognition_id": mixed.id,"evidence_id":evidence_id}), '{}'))
    for tier in ("local", "cloud"):
        items = cast(dict[str, Any], QueryService(path, subject_id=subject).preview_recall("睡眠", model_tier=tier)["preview"])
        ids = {item[1] for item in items["selected_item_ids"]}
        assert cognition.id in ids
        assert (mixed.id in ids) == (tier == "local")
        interaction = query_interactions(path, subject_id=subject, query="之前睡眠 5 小时 HRV", projection="model", model_tier=tier)
        assert ("HRV" in str(interaction["rendered_context"])) == (tier == "local"), context.id
    receipt = service.execute("retract_observed", {"source_key": fixture["source_key"], "withdrawn_through":"2026-10-02T00:00:00Z"})
    assert receipt["storage_cleanup"] == {"state":"complete","detail_code":"current_storage_committed"}
    assert db.execute("SELECT 1 FROM cognition WHERE id=?", (mixed.id,)).fetchone() is None
    assert db.execute("SELECT 1 FROM evidence_ledger WHERE id='derived-ledger'").fetchone() is None
    portable = PortableService(path, subject_id=subject, host_id=host)
    plan = portable.plan_import(backup)
    if plan["valid"]:
        portable.apply_import(backup, plan_hash=cast(str, plan["plan_hash"]))
    assert "HRV" not in json.dumps(portable.export_bundle(), ensure_ascii=False)
    db.close()


def test_valid_time_permissions_and_replacement_remove_old_content(tmp_path: Path) -> None:
    fixture = parity("observed.json")
    path = tmp_path / "memory.db"
    open_db(str(path)).close()
    service = ObservedService(path, subject_id=fixture["subject"], host_id=fixture["host"],
        clock=lambda: datetime(2026, 10, 7, tzinfo=timezone.utc))
    e = {"source_key": fixture["source_key"], **fixture["evidence"], "valid_until":"2026-10-02T00:00:00Z"}
    service.execute("upsert_observed", {"evidence":e})
    assert cast(dict[str, Any], QueryService(path,subject_id=fixture["subject"]).preview_recall("睡眠")["preview"])["count"] == 0
    service.execute("upsert_observed", {"evidence":{**e,"version":"2026-10-03T00:00:00Z","content":"睡眠 7 小时。","valid_until":None}})
    bundle = PortableService(path,subject_id=fixture["subject"],host_id=fixture["host"]).export_bundle()
    raw = json.dumps(bundle,ensure_ascii=False)
    assert "HRV" not in raw and "5 小时" not in raw and "7 小时" in raw


def test_wal_cleanup_pending_is_retryable_even_after_idempotent_replacement(tmp_path: Path) -> None:
    fixture = parity("observed.json")
    path = tmp_path / "wal.db"
    db = open_db(str(path))
    db.execute("PRAGMA journal_mode=WAL")
    service = ObservedService(path, subject_id=fixture["subject"], host_id=fixture["host"])
    e = {"source_key": fixture["source_key"], **fixture["evidence"]}
    service.execute("upsert_observed", {"evidence":e})
    db.execute("BEGIN")
    db.execute("SELECT raw_content FROM evidence").fetchall()
    replacement = {**e,"version":"2026-10-02T00:00:00Z","content":"睡眠 7 小时。"}
    pending = service.execute("upsert_observed", {"evidence":replacement})
    assert cast(dict[str,str], pending["storage_cleanup"])["state"] == "pending"
    db.rollback()
    replay = service.execute("upsert_observed", {"evidence":replacement})
    assert replay["result_state"] == "no_change"
    assert cast(dict[str,str], replay["storage_cleanup"])["state"] == "complete"
    db.close()
