"""DSH/WeftMate bridge: graph-aware Recall, allow_local_read gate, export gate.

These tests drive the same DshMemoWeftRuntime the WeftMate host plugin uses,
with World rows inserted directly (deterministic, zero model calls).  The
graph-aware matching and permission gate are the shared hermes/recall.py
implementation, so these also pin the production Recall contract.
"""
from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest

from support.json_assertions import as_objects, as_string

from memoweft.integrations.dsh_bridge import DshBoundaryError, DshMemoWeftRuntime
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from memoweft.store import open_db

_T = "2026-08-14T12:00:00.000Z"


def _runtime(tmp_path: Path) -> tuple[DshMemoWeftRuntime, str, Path]:
    rt = DshMemoWeftRuntime()
    init = rt.initialize(
        "s", dsh_home=str(tmp_path), platform="desktop", user_id="owner"
    )
    return rt, str(init["subject_id"]), Path(str(init["db_path"]))


def test_dsh_runtime_accepts_one_trusted_startup_subject_binding(tmp_path: Path) -> None:
    runtime = DshMemoWeftRuntime()
    try:
        initialized = runtime.initialize(
            "memory-experience",
            dsh_home=str(tmp_path),
            platform="memory-web",
            user_id="presentation-user",
            subject_id="owner",
            auto_route=False,
        )
        assert initialized["subject_id"] == "owner"
        assert runtime.subject_id == "owner"
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("subject_id", ["", " owner", "owner ", "x" * 513])
def test_dsh_runtime_rejects_invalid_explicit_subject_binding(
    tmp_path: Path, subject_id: str
) -> None:
    runtime = DshMemoWeftRuntime()
    with pytest.raises(DshBoundaryError, match="subject_id"):
        runtime.initialize(
            "memory-experience",
            dsh_home=str(tmp_path),
            platform="memory-web",
            user_id="presentation-user",
            subject_id=subject_id,
            auto_route=False,
        )


def _boundary(*, mode: str = "in_place", prefix: str = "weftmate-compression-boundary-v1") -> dict[str, object]:
    source_messages = [{"role": "user", "content": "我喜欢喝咖啡", "source_ref": "source:0"}]
    payload = {
        "schema_version": 1,
        "provider_name": "memoweft",
        "parent_session_id": "route-tier-session",
        "result_session_id": "route-tier-session",
        "mode": mode,
        "source_messages": source_messages,
    }
    canonical = json.dumps(
        payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    )
    payload_hash = sha256(canonical.encode("utf-8")).hexdigest()
    return {
        **payload,
        "payload_hash": payload_hash,
        "event_id": prefix + ":" + "a" * 32 + ":" + payload_hash,
    }


def test_dsh_accepts_idempotent_committed_turn_boundary(tmp_path: Path) -> None:
    runtime = DshMemoWeftRuntime()
    runtime.initialize("s", dsh_home=str(tmp_path), platform="desktop", auto_route=False)
    boundary = _boundary(mode="turn", prefix="weftmate-turn-boundary-v1")
    try:
        first = runtime.ingest_durable_boundary(boundary)
        second = runtime.ingest_durable_boundary(boundary)
        assert first["job_id"] == second["job_id"]
        assert first["evidence_count"] == second["evidence_count"] == 1
        with sqlite3.connect(str(runtime.db_path)) as db:
            assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1
    finally:
        runtime.shutdown()


def test_dsh_turn_mode_requires_turn_prefix_and_same_session(tmp_path: Path) -> None:
    runtime = DshMemoWeftRuntime()
    runtime.initialize("s", dsh_home=str(tmp_path), platform="desktop", auto_route=False)
    try:
        wrong_prefix = _boundary(mode="turn")
        with pytest.raises(DshBoundaryError, match="event_id"):
            runtime.ingest_durable_boundary(wrong_prefix)
    finally:
        runtime.shutdown()


def _insert_evidence(
    db: sqlite3.Connection,
    subject: str,
    evidence_id: str,
    *,
    allow_local_read: int = 1,
) -> None:
    db.execute(
        "INSERT OR IGNORE INTO evidence (id, subject_id, source_kind, host_id, "
        "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
        "allow_cloud_read, allow_inference) VALUES "
        "(?, ?, 'spoken', 'weftmate:test', ?, ?, '原话', '原话', ?, 1, 1)",
        (evidence_id, subject, _T, _T, allow_local_read),
    )


def _insert_relationship(
    db: sqlite3.Connection, subject: str, *, evidence_id: str = "ev-rel"
) -> None:
    for entity_id, name, aliases in (
        ("ent-wang", "小王", ["杨杨"]),
        ("ent-li", "小李", []),
    ):
        db.execute(
            "INSERT OR IGNORE INTO entity (id, world_id, kind, canonical_name, "
            "invalid_at, created_at, updated_at, aliases_json) VALUES "
            "(?, ?, 'person', ?, NULL, ?, ?, ?)",
            (entity_id, subject, name, _T, _T, json.dumps(aliases, ensure_ascii=False)),
        )
    db.execute(
        "INSERT OR IGNORE INTO relationship (id, world_id, source_entity_id, "
        "target_entity_id, relation_type, content, formed_by, confidence, "
        "cred_status, invalid_at, created_at, updated_at) VALUES "
        "('rel-1', ?, 'ent-wang', 'ent-li', 'girlfriend', '小王是小李的女朋友', "
        "'stated', 600, 'limited', NULL, ?, ?)",
        (subject, _T, _T),
    )
    db.execute(
        "INSERT OR IGNORE INTO relationship_evidence "
        "(relationship_id, evidence_id, relation) VALUES ('rel-1', ?, 'support')",
        (evidence_id,),
    )
    for entity_id in ("ent-wang", "ent-li"):
        db.execute(
            "INSERT OR IGNORE INTO evidence_ledger (id, content, payload_json) VALUES "
            "(?, ?, '{\"schema_version\":1}')",
            (
                f"entity-support-{entity_id}-{evidence_id}",
                json.dumps(
                    {
                        "relation": "support",
                        "entity_id": entity_id,
                        "evidence_id": evidence_id,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        )
    db.execute(
        "INSERT OR IGNORE INTO evidence_ledger (id, content, payload_json) VALUES "
        "(?, ?, ?)",
        (
            f"entity-alias-ent-wang-{evidence_id}",
            json.dumps(
                {
                    "relation": "alias",
                    "canonical_entity_id": "ent-wang",
                    "alias_name": "杨杨",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            json.dumps(
                {"schema_version": 1, "evidence_ids": [evidence_id]},
                ensure_ascii=False,
                sort_keys=True,
            ),
        ),
    )


def _insert_event(
    db: sqlite3.Connection, subject: str, *, evidence_id: str = "ev-event"
) -> None:
    db.execute(
        "INSERT OR IGNORE INTO world_event (id, world_id, content, occurred_at, "
        "time_expression, participants_json, objects_json, formed_by, confidence, "
        "cred_status, invalid_at, created_at, updated_at) VALUES "
        "('event-1', ?, '用户喜欢喝咖啡', NULL, NULL, '[]', '[]', 'stated', 600, "
        "'limited', NULL, ?, ?)",
        (subject, _T, _T),
    )
    db.execute(
        "INSERT OR IGNORE INTO world_event_evidence "
        "(world_event_id, evidence_id, relation) VALUES ('event-1', ?, 'support')",
        (evidence_id,),
    )


def _insert_cognition(
    db: sqlite3.Connection, subject: str, *, evidence_id: str = "ev-cog"
) -> None:
    db.execute(
        "INSERT OR IGNORE INTO cognition (id, subject_id, content, content_type, "
        "formed_by, confidence, cred_status, scope, valid_at, invalid_at, "
        "asked_at, archived_at, muted_at, created_at, updated_at) VALUES "
        "('cog-1', ?, '用户喜欢喝咖啡', 'preference', 'stated', 600, 'limited', "
        "NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)",
        (subject, _T, _T),
    )
    db.execute(
        "INSERT OR IGNORE INTO cognition_evidence (cognition_id, evidence_id, relation) "
        "VALUES ('cog-1', ?, 'support')",
        (evidence_id,),
    )


def test_graph_aware_recall_matches_relationship_via_entity_alias(tmp_path: Path) -> None:
    rt, subject, db_path = _runtime(tmp_path)
    try:
        db = open_db(str(db_path))
        try:
            _insert_evidence(db, subject, "ev-rel")
            _insert_relationship(db, subject)
        finally:
            db.close()
        # 查询用别名"杨杨"：内容里只有"小王"，靠实体别名进入匹配面。
        result = rt.prefetch("杨杨和小李是什么关系", session_id="s")
        assert result["count"] == 1
        assert "小王是小李的女朋友" in as_string(result["text"])
    finally:
        rt.shutdown()


def test_permission_gate_hides_disallowed_evidence(tmp_path: Path) -> None:
    rt, subject, db_path = _runtime(tmp_path)
    try:
        db = open_db(str(db_path))
        try:
            _insert_evidence(db, subject, "ev-rel", allow_local_read=0)
            _insert_relationship(db, subject)
            _insert_evidence(db, subject, "ev-cog")
            _insert_cognition(db, subject)
        finally:
            db.close()
        result = rt.prefetch("女朋友 咖啡", session_id="s")
        # 关系被权限门挡住；认知仍可见（fail-closed 只挡无权限的行）。
        assert result["count"] == 1
        assert "小王是小李的女朋友" not in as_string(result["text"])
        assert "用户喜欢喝咖啡" in as_string(result["text"])
        direct = rt.prefetch("小王和小李", session_id="s")
        assert direct["count"] == 0
    finally:
        rt.shutdown()


def test_export_gate_excludes_disallowed_evidence(tmp_path: Path) -> None:
    rt, subject, db_path = _runtime(tmp_path)
    try:
        db = open_db(str(db_path))
        try:
            _insert_evidence(db, subject, "ev-cog", allow_local_read=0)
            _insert_cognition(db, subject)
            _insert_evidence(db, subject, "ev-open")
        finally:
            db.close()
        exported = rt.export_world()
        assert as_objects(exported["cognitions"]) == []  # 其唯一 Evidence 被拒 → 认知不可导出
        evidence_ids = {item["id"] for item in as_objects(exported["evidence"])}
        assert "ev-open" in evidence_ids
        assert "ev-cog" not in evidence_ids
    finally:
        rt.shutdown()


@pytest.mark.parametrize(
    ("invalid_support", "sql", "params"),
    (
        ("soft_deleted", "UPDATE evidence SET deleted_at = ? WHERE id = 'ev-cog'", (_T,)),
        (
            "cross_subject",
            "UPDATE evidence SET subject_id = 'other-subject' WHERE id = 'ev-cog'",
            (),
        ),
        ("local_read_revoked", "UPDATE evidence SET allow_local_read = 0 WHERE id = 'ev-cog'", ()),
    ),
)
def test_export_world_excludes_noncurrent_support_and_never_dangles(
    tmp_path: Path, invalid_support: str, sql: str, params: tuple[object, ...]
) -> None:
    rt, subject, db_path = _runtime(tmp_path)
    try:
        db = open_db(str(db_path))
        try:
            _insert_evidence(db, subject, "ev-cog")
            _insert_cognition(db, subject)
            db.execute(sql, params)
            db.commit()
        finally:
            db.close()
        exported = rt.export_world()
        assert as_objects(exported["cognitions"]) == [], invalid_support
        assert as_objects(exported["evidence"]) == [], invalid_support
    finally:
        rt.shutdown()


@pytest.mark.parametrize("kind", ("cognition", "relationship", "event"))
@pytest.mark.parametrize("invalid_support", ("soft_deleted", "cross_subject", "multi_provenance"))
def test_list_world_excludes_rows_with_noncurrent_support(
    tmp_path: Path, kind: str, invalid_support: str
) -> None:
    rt, subject, db_path = _runtime(tmp_path)
    list_key = {"cognition": "cognitions", "relationship": "relationships", "event": "events"}[kind]
    try:
        db = open_db(str(db_path))
        try:
            _insert_evidence(db, subject, "ev-primary")
            if kind == "cognition":
                _insert_cognition(db, subject, evidence_id="ev-primary")
            elif kind == "relationship":
                _insert_relationship(db, subject, evidence_id="ev-primary")
            else:
                _insert_event(db, subject, evidence_id="ev-primary")

            if invalid_support == "soft_deleted":
                db.execute("UPDATE evidence SET deleted_at = ? WHERE id = 'ev-primary'", (_T,))
            elif invalid_support == "cross_subject":
                db.execute("UPDATE evidence SET subject_id = 'other-subject' WHERE id = 'ev-primary'")
            else:
                _insert_evidence(db, subject, "ev-deleted")
                db.execute("UPDATE evidence SET deleted_at = ? WHERE id = 'ev-deleted'", (_T,))
                table, column, row_id = {
                    "cognition": ("cognition_evidence", "cognition_id", "cog-1"),
                    "relationship": ("relationship_evidence", "relationship_id", "rel-1"),
                    "event": ("world_event_evidence", "world_event_id", "event-1"),
                }[kind]
                db.execute(
                    f"INSERT INTO {table} ({column}, evidence_id, relation) VALUES (?, 'ev-deleted', 'support')",
                    (row_id,),
                )
            db.commit()
        finally:
            db.close()
        assert as_objects(rt.list_world()[list_key]) == [], f"{kind}:{invalid_support}"
    finally:
        rt.shutdown()


@pytest.mark.parametrize(
    ("kind", "list_key"),
    (("cognition", "cognitions"), ("relationship", "relationships"), ("event", "events")),
)
def test_list_world_keeps_row_with_all_current_supports(
    tmp_path: Path, kind: str, list_key: str
) -> None:
    rt, subject, db_path = _runtime(tmp_path)
    try:
        db = open_db(str(db_path))
        try:
            _insert_evidence(db, subject, "ev-primary")
            _insert_evidence(db, subject, "ev-secondary")
            if kind == "cognition":
                _insert_cognition(db, subject, evidence_id="ev-primary")
                table, column, row_id = "cognition_evidence", "cognition_id", "cog-1"
            elif kind == "relationship":
                _insert_relationship(db, subject, evidence_id="ev-primary")
                table, column, row_id = "relationship_evidence", "relationship_id", "rel-1"
            else:
                _insert_event(db, subject, evidence_id="ev-primary")
                table, column, row_id = "world_event_evidence", "world_event_id", "event-1"
            db.execute(
                f"INSERT INTO {table} ({column}, evidence_id, relation) VALUES (?, 'ev-secondary', 'support')",
                (row_id,),
            )
            db.commit()
        finally:
            db.close()
        assert [item["id"] for item in as_objects(rt.list_world()[list_key])] == [row_id]
    finally:
        rt.shutdown()


@pytest.mark.parametrize(
    ("model_tier", "expected_route_calls"),
    ((None, 1), ("cloud", 1), ("local", 0)),
)
def test_dsh_route_tier_controls_dispatch_after_local_read_revocation(
    tmp_path: Path, model_tier: str | None, expected_route_calls: int
) -> None:
    calls: list[object] = []

    def route(messages: object, session_id: str = "") -> dict[str, object]:
        del messages, session_id
        calls.append(object())
        return {"content": '{"schema_version":8,"result":"no_change"}', "model": "test"}

    runtime = DshMemoWeftRuntime()
    init_kwargs: dict[str, object] = {
        "dsh_home": str(tmp_path),
        "platform": "desktop",
        "user_id": "route-tier-owner",
        "one_shot_llm": route,
    }
    if model_tier is not None:
        init_kwargs["model_tier"] = model_tier
    runtime.initialize("route-tier", **init_kwargs)
    worker = runtime._world_worker
    assert worker is not None
    processor = worker.processor
    worker.shutdown()
    try:
        runtime.ingest_durable_boundary(_boundary())
        db = open_db(str(runtime.db_path))
        try:
            db.execute("UPDATE evidence SET allow_local_read = 0")
            db.commit()
        finally:
            db.close()
        assert runtime.db_path is not None
        runner = WorldJobWorker(runtime.db_path, processor=processor)
        try:
            assert runner.run_until_quiescent() == 1
        finally:
            runner.shutdown()
        assert len(calls) == expected_route_calls
    finally:
        runtime.shutdown()


@pytest.mark.parametrize(
    ("model_tier", "denied_column"),
    (
        ("cloud", "allow_cloud_read"),
        ("cloud", "allow_inference"),
        ("local", "allow_inference"),
    ),
)
def test_dsh_route_tier_denied_evidence_capability_prevents_dispatch(
    tmp_path: Path, model_tier: str, denied_column: str
) -> None:
    calls: list[object] = []

    def route(messages: object, session_id: str = "") -> dict[str, object]:
        del messages, session_id
        calls.append(object())
        return {"content": '{"schema_version":8,"result":"cognitions","cognitions":[]}', "model": "test"}

    runtime = DshMemoWeftRuntime()
    runtime.initialize(
        "route-capability",
        dsh_home=str(tmp_path),
        platform="desktop",
        user_id="route-tier-owner",
        one_shot_llm=route,
        model_tier=model_tier,
    )
    worker = runtime._world_worker
    assert worker is not None
    processor = worker.processor
    worker.shutdown()
    try:
        runtime.ingest_durable_boundary(_boundary())
        db = open_db(str(runtime.db_path))
        try:
            db.execute(f"UPDATE evidence SET {denied_column} = 0")
            db.commit()
        finally:
            db.close()
        assert runtime.db_path is not None
        runner = WorldJobWorker(runtime.db_path, processor=processor)
        try:
            assert runner.run_until_quiescent() == 1
        finally:
            runner.shutdown()
        assert calls == []
    finally:
        runtime.shutdown()


def test_dsh_rejects_unknown_model_tier(tmp_path: Path) -> None:
    runtime = DshMemoWeftRuntime()
    with pytest.raises(DshBoundaryError, match="model_tier"):
        runtime.initialize(
            "invalid-tier",
            dsh_home=str(tmp_path),
            platform="desktop",
            user_id="route-tier-owner",
            model_tier="unknown",
        )


def test_export_world_is_byte_stable_for_the_same_current_snapshot(tmp_path: Path) -> None:
    runtime, subject, db_path = _runtime(tmp_path)
    try:
        db = open_db(str(db_path))
        try:
            _insert_evidence(db, subject, "ev-rel")
            _insert_relationship(db, subject, evidence_id="ev-rel")
            _insert_evidence(db, subject, "ev-event")
            _insert_event(db, subject, evidence_id="ev-event")
            _insert_evidence(db, subject, "ev-cog")
            _insert_cognition(db, subject, evidence_id="ev-cog")
            db.commit()
        finally:
            db.close()
        first = json.dumps(
            runtime.export_world(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        second = json.dumps(
            runtime.export_world(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        assert first == second
    finally:
        runtime.shutdown()
