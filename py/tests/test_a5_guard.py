"""A5 矛盾并存护栏 · Python 行为测试，与 TS tests/a5CoexistGuardrail.test.ts 逐条对称。

护栏在 consolidate 的 new 分支入库前，对内存现有画像算嵌入相似度筛同主题候选、对少量候选做一次
极性判；命中"相似且极性相反" → 不新建相反行，改把反转证据挂 contradict 到旧认知、走 conflict 语义。
可选依赖：不注入 embedder = 行为同旧。

本文件用与 TS 相同的玩具嵌入器向量 + 相同断言，钉 Python 与 TS 行为一致（对治"TS 改了 Python 原样保留"）。
guard-off 的逐字节跨语言一致由 test_parity_consolidate.py 覆盖，这里专测 guard-on 三种分流 + 复现。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from memoweft.consolidate import ContradictionGuard, consolidate
from memoweft.llm.client import ChatMessage, UsageStats
from memoweft.store import open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.store.event import SqliteEventStore
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.types import (
    Cognition,
    ContentType,
    CredStatus,
    Event,
    EvidenceInput,
    EvidenceLink,
    FormedBy,
    ModelTier,
)

T = "2026-07-01T00:00:00.000Z"


def _clock() -> datetime:
    return datetime(2026, 7, 1, tzinfo=timezone.utc)


class _FixedNewLLM:
    """固定返回 {"new":[...]} 的脚本 LLM。"""

    def __init__(self, new_items: str) -> None:
        self._new = new_items
        self._n = 0

    def chat(self, messages: list[ChatMessage]) -> str:
        self._n += 1
        return '{"new":%s}' % self._new

    @property
    def call_count(self) -> int:
        return self._n

    @property
    def tier(self) -> Optional[ModelTier]:
        return "cloud"

    @property
    def usage(self) -> Optional[UsageStats]:
        return None


class _GuardAwareLLM:
    """命中极性判提示词特有标记 → {"contradicts":bool}；其余（consolidate 主调用）→ {"new":[...]}。
    用【只在极性判提示词里、consolidate 提示词里绝对没有】的标记分流（与 TS 同）。"""

    def __init__(self, new_items: str, contradicts: bool) -> None:
        self._new = new_items
        self._contradicts = contradicts
        self._n = 0

    def chat(self, messages: list[ChatMessage]) -> str:
        self._n += 1
        text = "\n".join(m.content for m in messages)
        if re.search(r"同一个人|SAME person|它们矛盾吗|Do they contradict", text):
            return '{"contradicts": %s}' % ("true" if self._contradicts else "false")
        return '{"new":%s}' % self._new

    @property
    def call_count(self) -> int:
        return self._n

    @property
    def tier(self) -> Optional[ModelTier]:
        return "cloud"

    @property
    def usage(self) -> Optional[UsageStats]:
        return None


class _TopicEmbedder:
    """玩具嵌入器：含"咖啡/coffee"→[1,0]，含"茶/tea"→[0,1]，其余→[0.5,0.5]（与 TS topicEmbedder 同）。"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            low = t.lower()
            if "咖啡" in t or "coffee" in low:
                out.append([1.0, 0.0])
            elif "茶" in t or "tea" in low:
                out.append([0.0, 1.0])
            else:
                out.append([0.5, 0.5])
        return out


def _cog(id: str, content: str, ct: ContentType, fb: FormedBy, conf: int, cs: CredStatus) -> Cognition:
    return Cognition(
        id=id, subject_id="owner", content=content, content_type=ct, formed_by=fb, confidence=conf, cred_status=cs,
        scope=None, valid_at=None, invalid_at=None, asked_at=None, archived_at=None, muted_at=None, created_at=T, updated_at=T,
    )


def _setup() -> tuple[SqliteEvidenceStore, SqliteEventStore, SqliteCognitionStore, str]:
    """插入既有认知"用户爱喝咖啡" + 一条未固化的新证据（第二轮的反转/新话由各用例决定），返回新证据 id。"""
    db = open_db(":memory:")
    ev = SqliteEvidenceStore(db, clock=_clock)
    evt = SqliteEventStore(db, clock=_clock)
    cog = SqliteCognitionStore(db, clock=_clock)
    cog.insert(
        _cog("cog-coffee", "用户爱喝咖啡", "preference", "stated", 600, "limited"),
        [EvidenceLink(evidence_id="ev-old", relation="support")],
    )
    return ev, evt, cog, ""


def _feed(ev: SqliteEvidenceStore, evt: SqliteEventStore, raw: str) -> str:
    e = ev.put(EvidenceInput(subject_id="owner", source_kind="spoken", host_id="local", occurred_at=T, raw_content=raw)).id
    evt.insert(Event(id=f"evt-{e[:8]}", subject_id="owner", summary=f"用户说{raw}", occurred_at=T, created_at=T), [e], consolidated=False)
    return e


def test_reproduction_guard_off_coexists() -> None:
    """现状复现（护栏关）：反转被误判 new → 并存两条极性相反认知、都不带冲突标记。"""
    ev, evt, cog, _ = _setup()
    e2 = _feed(ev, evt, "我把咖啡戒了，再也不喝了")
    consolidate(
        "owner", event_store=evt, evidence_store=ev, cognition_store=cog,
        llm=_FixedNewLLM(f'[{{"content":"用户不再喝咖啡了","content_type":"preference","formed_by":"stated","support_evidence_ids":["{e2}"]}}]'),
        now_iso=T,
    )
    active = cog.active("owner")
    assert len(active) == 2, "A5 复现：并存两条"
    assert any("爱喝咖啡" in c.content for c in active) and any("不再喝咖啡" in c.content for c in active)
    assert all(c.cred_status not in ("conflicted", "contested") for c in active), "都不带冲突标记（隐形）"


def test_guard_fires_reversal_to_conflict() -> None:
    """护栏命中：相似且极性相反 → 不新建相反行，旧认知标 conflicted。"""
    ev, evt, cog, _ = _setup()
    e2 = _feed(ev, evt, "我把咖啡戒了，再也不喝了")
    consolidate(
        "owner", event_store=evt, evidence_store=ev, cognition_store=cog,
        llm=_GuardAwareLLM(f'[{{"content":"用户不再喝咖啡了","content_type":"preference","formed_by":"stated","support_evidence_ids":["{e2}"]}}]', True),
        now_iso=T,
        contradiction_guard=ContradictionGuard(embedder=_TopicEmbedder(), min_similarity=0.5),
    )
    active = cog.active("owner")
    assert len(active) == 1, "护栏阻止并存：只剩旧认知一条"
    assert "爱喝咖啡" in active[0].content
    assert active[0].cred_status == "conflicted", "旧认知被挂反证 → conflicted"
    assert not any("不再喝咖啡" in c.content for c in active), "不新建相反行"


def test_guard_similarity_gate_different_topic() -> None:
    """相似度门：不同主题（茶 vs 咖啡）→ 不触发、正常并存（不误伤）。"""
    ev, evt, cog, _ = _setup()
    e2 = _feed(ev, evt, "我最近爱上喝茶了")
    consolidate(
        "owner", event_store=evt, evidence_store=ev, cognition_store=cog,
        # 即便极性判会说"矛盾"，茶与咖啡余弦≈0、进不了 shortlist，极性判压根不会被调用。
        llm=_GuardAwareLLM(f'[{{"content":"用户爱喝茶","content_type":"preference","formed_by":"stated","support_evidence_ids":["{e2}"]}}]', True),
        now_iso=T,
        contradiction_guard=ContradictionGuard(embedder=_TopicEmbedder(), min_similarity=0.5),
    )
    active = cog.active("owner")
    assert len(active) == 2, "不同主题 → 不拦，两条并存"
    assert all(c.cred_status != "conflicted" for c in active)


def test_guard_polarity_gate_same_topic_not_contradictory() -> None:
    """极性门：同主题但不矛盾（爱喝咖啡 + 喜欢手冲咖啡）→ 不触发、正常并存（不误伤）。"""
    ev, evt, cog, _ = _setup()
    e2 = _feed(ev, evt, "我特别喜欢手冲咖啡")
    consolidate(
        "owner", event_store=evt, evidence_store=ev, cognition_store=cog,
        # 同主题（都进 shortlist），但极性判返回 false → 不拦。
        llm=_GuardAwareLLM(f'[{{"content":"用户喜欢手冲咖啡","content_type":"preference","formed_by":"stated","support_evidence_ids":["{e2}"]}}]', False),
        now_iso=T,
        contradiction_guard=ContradictionGuard(embedder=_TopicEmbedder(), min_similarity=0.5),
    )
    active = cog.active("owner")
    assert len(active) == 2, "同主题不矛盾 → 不拦，两条并存"
    assert all(c.cred_status != "conflicted" for c in active)
