"""便携包互通:TS 生成的合法包(shared/parity/bundle.json)→ Python 建同构库导入 → 保真(id/时间戳/溯源链)。

这是 1.3 最强的跨语言证据:TS 侧产出的记忆包,Python 侧原样读回、数据不丢。
( 起走完整 ImportPlan 语义:validate 门 + duplicates + 事务。)
"""
from __future__ import annotations

from copy import deepcopy
import sqlite3
from datetime import datetime, timezone
from typing import Any

from conftest import parity

from memoweft.portable import import_bundle, validate_bundle
from memoweft.store import make_transaction, open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.store.event import SqliteEventStore
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.store.interaction_context import SqliteInteractionContextStore
from memoweft.store.semantic_resolution import SqliteSemanticResolutionStore
from memoweft.types import Evidence


def _load() -> Any:
    return parity("bundle.json")


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _stores(db: sqlite3.Connection) -> dict[str, Any]:
    return {
        "evidence_store": SqliteEvidenceStore(db, clock=_clock),
        "event_store": SqliteEventStore(db, clock=_clock),
        "cognition_store": SqliteCognitionStore(db, clock=_clock),
        "interaction_context_store": SqliteInteractionContextStore(db, clock=_clock),
        "semantic_resolution_store": SqliteSemanticResolutionStore(db, clock=_clock),
    }


def test_ts_bundle_is_valid_to_python() -> None:
    # Python 的 validate 也认这个 TS 包合法(交叉印证 validate parity)。
    assert validate_bundle(_load()).valid


def test_import_roundtrip_preserves_data() -> None:
    bundle = _load()
    db = open_db(":memory:")
    try:
        st = _stores(db)
        plan = import_bundle(bundle, **st, transaction=make_transaction(db))
        assert plan.valid
        assert plan.counts.evidence == bundle["metadata"]["counts"]["evidence"] == 2
        assert plan.counts.events == bundle["metadata"]["counts"]["events"] == 1
        assert plan.counts.cognitions == bundle["metadata"]["counts"]["cognitions"] == 1
        assert plan.counts.event_evidence == 2
        assert plan.counts.cognition_evidence == 1
        assert plan.counts.interaction_contexts == 1

        # 逐条保真:原 id / 时间戳 / 关键字段读回来一致。
        ev1 = db.execute(
            "SELECT id, subject_id, source_kind, raw_content, allow_cloud_read, occurred_at FROM evidence WHERE id='ev-1'"
        ).fetchone()
        assert ev1 == ("ev-1", "owner", "spoken", "原话 ev-1", 0, "2026-01-01T00:00:00.000Z")
        cog = db.execute(
            "SELECT id, content, content_type, formed_by, confidence, cred_status FROM cognition WHERE id='cog-1'"
        ).fetchone()
        assert cog == ("cog-1", "用户喜欢 X", "preference", "stated", 600, "limited")
        # 溯源链保真:event→2 证据、cognition→1 证据。
        evev = {r[0] for r in db.execute("SELECT evidence_id FROM event_evidence WHERE event_id='evt-1'").fetchall()}
        assert evev == {"ev-1", "ev-2"}
        cogev = db.execute("SELECT evidence_id, relation FROM cognition_evidence WHERE cognition_id='cog-1'").fetchone()
        assert cogev == ("ev-1", "support")

        # 幂等:再导一次 → 全计 duplicates、counts 归零、库不重复。
        again = import_bundle(bundle, **st, transaction=make_transaction(db))
        assert again.counts.evidence == 0 and again.counts.cognitions == 0 and again.counts.events == 0
        assert again.duplicates.evidence == 2 and again.duplicates.events == 1 and again.duplicates.cognitions == 1
        assert len(st["evidence_store"].all()) == 2
    finally:
        db.close()


def test_same_evidence_id_with_different_payload_or_permissions_is_fatal() -> None:
    bundle = _load()
    db = open_db(":memory:")
    try:
        st = _stores(db)
        source = next(e for e in bundle["data"]["evidence"] if e["id"] == "ev-1")
        st["evidence_store"].insert(
            Evidence(
                id=source["id"],
                subject_id=source["subjectId"],
                source_kind=source["sourceKind"],
                host_id=source["hostId"],
                origin_id=source.get("originId"),
                occurred_at=source["occurredAt"],
                recorded_at=source["recordedAt"],
                raw_content="target public collision",
                summary="target public collision",
                allow_local_read=True,
                allow_cloud_read=True,
                allow_inference=True,
                corrects_evidence_id=source.get("correctsEvidenceId"),
            )
        )

        plan = import_bundle(bundle, **st, transaction=make_transaction(db))

        assert not plan.valid
        assert any("evidence ev-1" in error for error in plan.errors)
        assert plan.counts.evidence == plan.counts.events == plan.counts.cognitions == 0
        assert st["cognition_store"].all("owner") == []
    finally:
        db.close()


def test_same_event_or_cognition_id_with_different_payload_or_links_is_fatal() -> None:
    bundle = _load()
    db = open_db(":memory:")
    try:
        st = _stores(db)
        assert import_bundle(bundle, **st, transaction=make_transaction(db)).valid

        payload_collision = deepcopy(bundle)
        payload_collision["data"]["events"][0]["summary"] = "colliding event"
        payload_collision["data"]["cognitions"][0]["content"] = "colliding cognition"
        payload_plan = import_bundle(
            payload_collision, **st, transaction=make_transaction(db)
        )
        assert not payload_plan.valid
        assert any("event " in error for error in payload_plan.errors)
        assert any("cognition " in error for error in payload_plan.errors)

        relation_collision = deepcopy(bundle)
        relation_collision["data"]["eventEvidence"] = relation_collision["data"][
            "eventEvidence"
        ][:1]
        relation_collision["data"]["cognitionEvidence"][0]["relation"] = "contradict"
        relation_plan = import_bundle(
            relation_collision, **st, transaction=make_transaction(db)
        )
        assert not relation_plan.valid
        assert any("event " in error for error in relation_plan.errors)
        assert any("cognition " in error for error in relation_plan.errors)
    finally:
        db.close()
