"""DSH/WeftMate bridge: graph-aware Recall, allow_local_read gate, export gate.

These tests drive the same DshMemoWeftRuntime the WeftMate host plugin uses,
with World rows inserted directly (deterministic, zero model calls).  The
graph-aware matching and permission gate are the shared hermes/recall.py
implementation, so these also pin the production Recall contract.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
from memoweft.store import open_db

_T = "2026-08-14T12:00:00.000Z"


def _runtime(tmp_path: Path) -> tuple[DshMemoWeftRuntime, str, Path]:
    rt = DshMemoWeftRuntime()
    init = rt.initialize(
        "s", dsh_home=str(tmp_path), platform="desktop", user_id="owner"
    )
    return rt, str(init["subject_id"]), Path(str(init["db_path"]))


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
        assert "小王是小李的女朋友" in result["text"]
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
        assert "小王是小李的女朋友" not in result["text"]
        assert "用户喜欢喝咖啡" in result["text"]
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
        assert exported["cognitions"] == []  # 其唯一 Evidence 被拒 → 认知不可导出
        evidence_ids = {item["id"] for item in exported["evidence"]}
        assert "ev-open" in evidence_ids
        assert "ev-cog" not in evidence_ids
    finally:
        rt.shutdown()
