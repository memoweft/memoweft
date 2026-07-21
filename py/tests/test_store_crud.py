"""store CRUD 内部行为单测(注入固定 clock):幂等、默认、排序、update 三态、结构墙、clock 格式、event 链。

跨语言 golden 见 test_parity_evidence_auth / test_parity_cognition_order;本文件验 Python 侧行为忠实 TS。
"""
from __future__ import annotations

from datetime import datetime, timezone

from memoweft.clock import to_iso_z
from memoweft.store import open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.store.event import SqliteEventStore
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.types import (
    Cognition,
    CognitionInput,
    CognitionPatch,
    EventInput,
    Evidence,
    EvidenceInput,
    EvidenceLink,
)

FIXED_DT = datetime(2026, 3, 4, 5, 6, 7, 123_000, tzinfo=timezone.utc)


def _fixed() -> datetime:
    return FIXED_DT


# ── clock ──


def test_to_iso_z_matches_js_format() -> None:
    # JS Date.toISOString() = YYYY-MM-DDTHH:MM:SS.sssZ(毫秒 3 位 + Z);py isoformat 默认微秒6位/+00:00 会分叉。
    assert to_iso_z(FIXED_DT) == "2026-03-04T05:06:07.123Z"
    assert to_iso_z(datetime(2026, 1, 1, tzinfo=timezone.utc)) == "2026-01-01T00:00:00.000Z"
    # naive 视为 UTC
    assert to_iso_z(datetime(2026, 1, 1)) == "2026-01-01T00:00:00.000Z"


# ── evidence ──


def test_evidence_put_defaults_and_clock() -> None:
    db = open_db(":memory:")
    try:
        store = SqliteEvidenceStore(db, clock=_fixed)
        ev = store.put(EvidenceInput(subject_id="owner", source_kind="spoken", host_id="local", raw_content="我喜欢爬山"))
        assert ev.recorded_at == "2026-03-04T05:06:07.123Z"  # 注入 clock
        assert ev.occurred_at == ev.recorded_at  # occurred_at 缺省 = recorded_at
        assert ev.summary == "我喜欢爬山"  # summary 缺省 = raw_content
        assert len(ev.id) == 36  # uuid4
        # 回读一致
        got = store.get(ev.id)
        assert got is not None and got.summary == "我喜欢爬山"
    finally:
        db.close()


def test_evidence_origin_idempotent() -> None:
    db = open_db(":memory:")
    try:
        store = SqliteEvidenceStore(db, clock=_fixed)
        a = store.put(EvidenceInput(subject_id="owner", source_kind="spoken", host_id="local", raw_content="a", origin_id="o1"))
        b = store.put(EvidenceInput(subject_id="owner", source_kind="spoken", host_id="local", raw_content="b", origin_id="o1"))
        assert a.id == b.id  # 幂等:返回原条
        assert b.raw_content == "a"  # 第二次的内容被丢弃
        assert len(store.all()) == 1  # 库里只一条
        assert store.find_by_origin("o1") is not None
        assert store.find_by_origin("nope") is None
    finally:
        db.close()


def test_evidence_preceding_ai_context_structural_wall() -> None:
    # AI 上文是非证据上下文：只写入、经专用方法读取，永不进入 Evidence 读结构。
    db = open_db(":memory:")
    try:
        store = SqliteEvidenceStore(db, clock=_fixed)
        ev = store.put(
            EvidenceInput(
                subject_id="owner", source_kind="spoken", host_id="local",
                raw_content="是的", preceding_ai_context="AI:你喜欢爬山吧?",
            )
        )
        assert store.preceding_ai_context_of(ev.id) == "AI:你喜欢爬山吧?"
        # Evidence dataclass 结构上不含该字段(结构墙:读回路径拿不到)
        assert not hasattr(store.get(ev.id), "preceding_ai_context")
        # 无 AI 上文的证据 → None
        ev2 = store.put(EvidenceInput(subject_id="owner", source_kind="tool", host_id="local", raw_content="x"))
        assert store.preceding_ai_context_of(ev2.id) is None
        assert store.preceding_ai_context_of("ghost") is None
    finally:
        db.close()


def test_evidence_all_ordered_and_by_time_range() -> None:
    db = open_db(":memory:")
    try:
        store = SqliteEvidenceStore(db)
        # insert 固定 recorded_at 验 all 排序(recorded_at ASC, rowid ASC)
        def mk(id: str, recorded: str, occurred: str) -> Evidence:
            return Evidence(
                id=id, subject_id="owner", source_kind="spoken", host_id="local", origin_id=None,
                occurred_at=occurred, recorded_at=recorded, raw_content=id, summary=id,
                allow_local_read=True, allow_cloud_read=True, allow_inference=True, corrects_evidence_id=None,
            )
        store.insert(mk("b", "2026-01-02T00:00:00.000Z", "2026-01-02T00:00:00.000Z"))
        store.insert(mk("a", "2026-01-01T00:00:00.000Z", "2026-01-03T00:00:00.000Z"))
        store.insert(mk("c", "2026-01-03T00:00:00.000Z", "2026-01-01T00:00:00.000Z"))
        assert [e.id for e in store.all()] == ["a", "b", "c"]  # recorded_at ASC
        # byTimeRange 按 occurred_at 升序
        got = store.by_time_range("2026-01-01T00:00:00.000Z", "2026-01-02T23:59:59.000Z")
        assert [e.id for e in got] == ["c", "b"]  # occurred_at c(01) < b(02);a(03) 出界
    finally:
        db.close()


def test_evidence_update_and_remove() -> None:
    db = open_db(":memory:")
    try:
        store = SqliteEvidenceStore(db, clock=_fixed)
        ev = store.put(EvidenceInput(subject_id="owner", source_kind="spoken", host_id="local", raw_content="orig"))
        assert ev.allow_cloud_read is True
        up = store.update(ev.id, summary="new-sum", allow_cloud_read=False)
        assert up is not None
        assert up.raw_content == "orig"  # 未传保持原值
        assert up.summary == "new-sum"
        assert up.allow_cloud_read is False
        assert up.allow_inference is True  # 未传保持
        assert store.remove(ev.id) is True
        assert store.get(ev.id) is None
        assert store.remove("ghost") is False
    finally:
        db.close()


def test_evidence_soft_delete_and_purge() -> None:
    # A7:remove 是软删(墓碑,读取排除、原文保留、清 origin_id);purge 才是真抹除。与 TS 同契约。
    db = open_db(":memory:")
    try:
        store = SqliteEvidenceStore(db, clock=_fixed)
        e = store.put(
            EvidenceInput(
                subject_id="owner", source_kind="spoken", host_id="local",
                raw_content="素食者", origin_id="msg-1",
            )
        )
        assert store.remove(e.id) is True
        assert store.get(e.id) is None  # 读取排除墓碑
        assert store.all() == []
        row = db.execute(
            "SELECT raw_content, deleted_at, origin_id FROM evidence WHERE id = ?", (e.id,)
        ).fetchone()
        assert row is not None  # 物理行仍在(墓碑)
        assert row[0] == "素食者"  # 原文保留供审计
        assert row[1] is not None  # deleted_at 已打墓碑
        assert row[2] is None  # 软删清空 origin_id(位置索引:兼容 tuple / sqlite3.Row)
        assert store.remove(e.id) is False  # 重复删返回 False
        # 同 origin_id 可再摄入(不撞幂等唯一约束)
        e2 = store.put(
            EvidenceInput(
                subject_id="owner", source_kind="spoken", host_id="local",
                raw_content="重说", origin_id="msg-1",
            )
        )
        assert e2.id != e.id
        assert len(store.all()) == 1
        # purge = 真抹除(含墓碑)
        assert store.purge(e.id) is True
        assert db.execute("SELECT id FROM evidence WHERE id = ?", (e.id,)).fetchone() is None
    finally:
        db.close()


# ── event ──


def test_event_put_and_evidence_links() -> None:
    db = open_db(":memory:")
    try:
        store = SqliteEventStore(db, clock=_fixed)
        ev = store.put(EventInput(subject_id="owner", summary="一段对话", occurred_at="2026-01-01T00:00:00.000Z", evidence_ids=["e1", "e2"]))
        assert ev.created_at == "2026-03-04T05:06:07.123Z"  # 注入 clock
        assert store.evidence_of(ev.id) == ["e1", "e2"]
        assert sorted(store.covered_evidence_ids("owner")) == ["e1", "e2"]
        # 新建事件未消化
        assert [e.id for e in store.unconsolidated("owner")] == [ev.id]
        store.mark_consolidated([ev.id])
        assert store.unconsolidated("owner") == []
        # coveredEvidenceIds 仍返回(标已消化不改覆盖关系)
        assert sorted(store.covered_evidence_ids("owner")) == ["e1", "e2"]
    finally:
        db.close()


# ── cognition ──


def test_cognition_put_active_sources() -> None:
    db = open_db(":memory:")
    try:
        store = SqliteCognitionStore(db, clock=_fixed)
        cog = store.put(
            CognitionInput(
                subject_id="owner", content="用户喜欢 X", content_type="preference", formed_by="stated",
                confidence=600, cred_status="limited",
                evidence=[EvidenceLink(evidence_id="e1", relation="support")],
            )
        )
        assert cog.created_at == "2026-03-04T05:06:07.123Z"
        assert cog.asked_at is None and cog.archived_at is None and cog.muted_at is None  # 新建恒 None
        assert store.sources_of(cog.id) == [EvidenceLink(evidence_id="e1", relation="support")]
        assert [c.id for c in store.active("owner")] == [cog.id]
        # addEvidence 补挂
        store.add_evidence(cog.id, [EvidenceLink(evidence_id="e2", relation="contradict")])
        assert len(store.sources_of(cog.id)) == 2
    finally:
        db.close()


def test_cognition_update_tristate() -> None:
    db = open_db(":memory:")
    try:
        store = SqliteCognitionStore(db, clock=_fixed)
        cog = store.put(
            CognitionInput(
                subject_id="owner", content="c", content_type="hypothesis", formed_by="inferred",
                confidence=200, cred_status="low", scope="工作",
            )
        )
        # content/confidence/cred_status/formed_by:None(缺省)= 保留;显式改则改
        up1 = store.update(cog.id, CognitionPatch(confidence=300, cred_status="candidate"))
        assert up1 is not None
        assert up1.confidence == 300 and up1.cred_status == "candidate"
        assert up1.content == "c" and up1.formed_by == "inferred"  # 未传保留
        assert up1.scope == "工作"  # scope 未传(UNSET)→ 保留

        # scope 三态:UNSET 保留,None 复位
        up2 = store.update(cog.id, CognitionPatch())  # 全默认:scope=UNSET → 保留
        assert up2 is not None and up2.scope == "工作"
        up3 = store.update(cog.id, CognitionPatch(scope=None))  # 显式 None → 复位
        assert up3 is not None and up3.scope is None

        # archived_at / asked_at / invalid_at 三态(设值 → 复位)
        up4 = store.update(cog.id, CognitionPatch(archived_at="2026-05-05T00:00:00.000Z"))
        assert up4 is not None and up4.archived_at == "2026-05-05T00:00:00.000Z"
        assert store.active("owner") == []  # 归档后 active 排除
        up5 = store.update(cog.id, CognitionPatch(archived_at=None))  # 取消归档
        assert up5 is not None and up5.archived_at is None
        assert [c.id for c in store.active("owner")] == [cog.id]  # 回到 active

        # formed_by 升级( confirmed→stated 路径)
        up6 = store.update(cog.id, CognitionPatch(formed_by="stated"))
        assert up6 is not None and up6.formed_by == "stated"

        assert store.update("ghost", CognitionPatch(confidence=1)) is None
    finally:
        db.close()
