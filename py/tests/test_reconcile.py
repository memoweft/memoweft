"""A5 第二道护栏 · 全画像矛盾扫描（reconcile）Python 行为测试，与 TS tests/reconcile.test.ts 逐条对称。

第一道护栏（consolidate guard_hit）只查【本轮 new 候选】——跨轮积累 / topK 截断会漏，库里就并存两条
极性相反的 active 认知、对"冲突可见"隐形。第二道 reconcile 对【已入库全画像】同题聚簇 + 极性判 + 命中
挂反证，兜底这些残留。与护栏共用 contradiction.py 单一真源（同极性判 + 同 attach_contradiction 落库口径）。全离线。

用与 TS 相同的玩具嵌入器向量 + 相同断言，钉 Python 与 TS 行为一致（对治"TS 改了 Python 原样保留"）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from memoweft.llm.client import ChatMessage, UsageStats
from memoweft.reconcile import reconcile_contradictions
from memoweft.store import open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.types import Cognition, EvidenceInput, EvidenceLink, ModelTier

T_EARLY = "2026-07-01T10:00:00.000Z"
T_LATE = "2026-08-01T10:00:00.000Z"


def _clock() -> datetime:
    return datetime(2026, 7, 1, tzinfo=timezone.utc)


class _PolarityLLM:
    """只回极性判 {"contradicts":bool} 的脚本 LLM（reconcile 只调 judge_contradiction）。与 TS polarityLlm 对称。"""

    def __init__(self, contradicts: bool) -> None:
        self._contradicts = contradicts
        self._n = 0

    def chat(self, messages: list[ChatMessage]) -> str:
        self._n += 1
        return '{"contradicts": %s}' % ("true" if self._contradicts else "false")

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
    """玩具嵌入器：含"咖啡/coffee"→[1,0]，含"茶/tea"→[0,1]，其余→[0.5,0.5]（与 TS topicEmbedder 同）。
    同主题余弦≈1、跨主题≈0。"""

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


def _cog(id: str, content: str, at: str) -> Cognition:
    return Cognition(
        id=id, subject_id="owner", content=content, content_type="preference", formed_by="stated",
        confidence=600, cred_status="limited", scope=None, valid_at=None, invalid_at=None, asked_at=None,
        archived_at=None, muted_at=None, created_at=at, updated_at=at,
    )


def _stores() -> tuple[SqliteEvidenceStore, SqliteCognitionStore]:
    db = open_db(":memory:")
    return SqliteEvidenceStore(db, clock=_clock), SqliteCognitionStore(db, clock=_clock)


def _seed_ev(ev: SqliteEvidenceStore, word: str) -> str:
    return ev.put(
        EvidenceInput(subject_id="owner", source_kind="spoken", host_id="local", occurred_at=T_EARLY, raw_content=word)
    ).id


def test_reconcile_hits_reversal_anchor_earlier() -> None:
    """命中：并存两条同主题极性相反 → 锚点(较早入库)挂反证标 conflicted、不删另一条（冲突只暴露、不消解）。"""
    ev, cog = _stores()
    e1 = _seed_ev(ev, "我超爱喝咖啡")
    e2 = _seed_ev(ev, "我把咖啡戒了")
    cog.insert(_cog("cog-a", "用户爱喝咖啡", T_EARLY), [EvidenceLink(evidence_id=e1, relation="support")])  # 较早 = 锚点
    cog.insert(_cog("cog-b", "用户不再喝咖啡了", T_LATE), [EvidenceLink(evidence_id=e2, relation="support")])

    r = reconcile_contradictions("owner", cognition_store=cog, llm=_PolarityLLM(True), embedder=_TopicEmbedder())

    assert r.scanned == 2, "扫描两条"
    assert r.pairs_judged == 1, "同簇一对做一次极性判"
    assert r.conflicts_attached == 1, "命中挂一次反证"
    active = cog.active("owner")
    assert len(active) == 2, "不删：两条都还在"
    anchor = next(c for c in active if c.id == "cog-a")
    assert anchor.cred_status == "conflicted", "锚点'爱喝咖啡'被挂反证 → conflicted"
    assert any("不再喝咖啡" in c.content for c in active), "反转那条仍在库"


def test_reconcile_similarity_gate_different_topic() -> None:
    """相似度门：不同主题(茶 vs 咖啡) → 进不了同簇、不判不动(不误伤)。"""
    ev, cog = _stores()
    e1 = _seed_ev(ev, "我爱喝咖啡")
    e2 = _seed_ev(ev, "我爱喝茶")
    cog.insert(_cog("cog-a", "用户爱喝咖啡", T_EARLY), [EvidenceLink(evidence_id=e1, relation="support")])
    cog.insert(_cog("cog-b", "用户爱喝茶", T_LATE), [EvidenceLink(evidence_id=e2, relation="support")])

    llm = _PolarityLLM(True)  # 即使极性判会说"矛盾"，茶与咖啡余弦≈0、进不了同簇，极性判压根不被调用。
    r = reconcile_contradictions("owner", cognition_store=cog, llm=llm, embedder=_TopicEmbedder())

    assert r.pairs_judged == 0, "跨主题不进簇 → 零极性判"
    assert r.conflicts_attached == 0, "不动"
    assert llm.call_count == 0, "极性判未被调用"
    assert all(c.cred_status != "conflicted" for c in cog.active("owner")), "没有误挂冲突"


def test_reconcile_polarity_gate_same_topic_not_contradictory() -> None:
    """极性门：同主题但不矛盾(爱咖啡 + 手冲咖啡) → 判 false 不挂(不误伤)。"""
    ev, cog = _stores()
    e1 = _seed_ev(ev, "我爱喝咖啡")
    e2 = _seed_ev(ev, "我喜欢手冲咖啡")
    cog.insert(_cog("cog-a", "用户爱喝咖啡", T_EARLY), [EvidenceLink(evidence_id=e1, relation="support")])
    cog.insert(_cog("cog-b", "用户喜欢手冲咖啡", T_LATE), [EvidenceLink(evidence_id=e2, relation="support")])

    r = reconcile_contradictions("owner", cognition_store=cog, llm=_PolarityLLM(False), embedder=_TopicEmbedder())

    assert r.pairs_judged == 1, "同主题进簇、判一次"
    assert r.conflicts_attached == 0, "极性判 false → 不挂"
    assert all(c.cred_status != "conflicted" for c in cog.active("owner")), "没有误挂冲突"


def test_reconcile_empty_and_single_profile() -> None:
    """空画像 / 单条画像 → scanned<2、空返回、不崩、不调 llm（同 TS facade 空画像用例）。"""
    ev, cog = _stores()
    llm = _PolarityLLM(True)

    empty = reconcile_contradictions("owner", cognition_store=cog, llm=llm, embedder=_TopicEmbedder())
    assert empty.scanned == 0 and empty.conflicts_attached == 0, "空画像 → scanned 0"

    e1 = _seed_ev(ev, "我爱喝咖啡")
    cog.insert(_cog("cog-a", "用户爱喝咖啡", T_EARLY), [EvidenceLink(evidence_id=e1, relation="support")])
    single = reconcile_contradictions("owner", cognition_store=cog, llm=llm, embedder=_TopicEmbedder())
    assert single.scanned == 1 and single.pairs_judged == 0, "单条 → 无对可判"
    assert llm.call_count == 0, "scanned<2 不进极性判"
