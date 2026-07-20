"""content_type 兜底计量仪表的跨语言平价（对应 TS tests/contentTypeFallbackMetering.test.ts）。

靶心与 TS 侧同：_pick_cognition 对非法/缺失 content_type 一律静默改写成 fact，而 fact 恰是
「永不衰减 + 永不自动失效 + 不受 transient_cap 封顶」那一档 —— 兜底方向偏向【最持久】的类型。
三种触发因严重度不同（尤其 out_of_scope 是语义降级而非拼写错误），必须分开计。

本文件只钉计数，不断言行为改变：兜底仍然落 fact，那是现状、不在本次改动范围内。
本仓库有两次「Python 移植滞后于 TS」的前科，故 TS 加仪表的同批必须有 Python 侧用例，
否则平价只能靠人工对齐、下次改动照样漂。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from memoweft.consolidate import ConsolidateResult, consolidate
from memoweft.llm.client import ChatMessage, UsageStats
from memoweft.store import open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.store.event import SqliteEventStore
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.types import Cognition, Event, EvidenceInput, ModelTier

T = "2026-07-20T10:00:00.000Z"


def _clock() -> datetime:
    return datetime(2026, 7, 20, tzinfo=timezone.utc)


class _StubLLM:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self._n = 0

    def chat(self, messages: list[ChatMessage]) -> str:
        self._n += 1
        return self._reply

    @property
    def call_count(self) -> int:
        return self._n

    @property
    def tier(self) -> Optional[ModelTier]:
        return "cloud"

    @property
    def usage(self) -> Optional[UsageStats]:
        return None


def _run(new_items: list[dict[str, object]]) -> tuple[ConsolidateResult, list[Cognition]]:
    """用给定的 new[] 跑一轮 consolidate，返回 (result, active 认知)。"""
    db = open_db(":memory:")
    ev = SqliteEvidenceStore(db, clock=_clock)
    evt = SqliteEventStore(db, clock=_clock)
    cog = SqliteCognitionStore(db, clock=_clock)
    e1 = ev.put(
        EvidenceInput(subject_id="owner", source_kind="spoken", host_id="local", occurred_at=T, raw_content="我住在北京")
    ).id
    evt.insert(Event(id="evt1", subject_id="owner", summary="聊天", occurred_at=T, created_at=T), [e1], consolidated=False)
    for item in new_items:
        item["support_evidence_ids"] = ["e1"]
    llm = _StubLLM(json.dumps({"new": new_items}))
    r = consolidate("owner", event_store=evt, evidence_store=ev, cognition_store=cog, llm=llm, now_iso=T, lang="en")
    return r, cog.active("owner")


def test_missing_content_type_counts_as_missing() -> None:
    r, cogs = _run([{"content": "用户住在北京", "formed_by": "stated"}])
    assert r.content_type_fallback.missing == 1
    assert r.content_type_fallback.invalid == 0
    assert r.content_type_fallback.out_of_scope == 0
    assert len(cogs) == 1
    assert cogs[0].content_type == "fact"  # 行为未变


def test_hallucinated_content_type_counts_as_invalid() -> None:
    r, cogs = _run([{"content": "用户住在北京", "content_type": "locaton", "formed_by": "stated"}])
    assert r.content_type_fallback.invalid == 1
    assert r.content_type_fallback.missing == 0
    assert r.content_type_fallback.out_of_scope == 0
    assert cogs[0].content_type == "fact"


def test_hypothesis_counts_as_out_of_scope() -> None:
    """hypothesis 在 ContentType 里合法、只是 consolidate 不收 —— 语义降级，与拼写错误区分。"""
    r, cogs = _run([{"content": "用户可能不太会做饭", "content_type": "hypothesis", "formed_by": "inferred"}])
    assert r.content_type_fallback.out_of_scope == 1
    assert r.content_type_fallback.missing == 0
    assert r.content_type_fallback.invalid == 0
    # 现状留证：受 hypothesis_cap 与 2 天半衰期约束的推测，被洗成了永久 fact。
    assert cogs[0].content_type == "fact"


def test_trend_counts_as_out_of_scope() -> None:
    r, _ = _run([{"content": "用户最近常熬夜", "content_type": "trend", "formed_by": "ruled"}])
    assert r.content_type_fallback.out_of_scope == 1


def test_valid_content_type_counts_nothing() -> None:
    r, cogs = _run([{"content": "用户住在北京", "content_type": "fact", "formed_by": "stated"}])
    assert (r.content_type_fallback.missing, r.content_type_fallback.invalid, r.content_type_fallback.out_of_scope) == (0, 0, 0)
    assert cogs[0].content_type == "fact"


def test_mixed_causes_accumulate_separately() -> None:
    r, _ = _run(
        [
            {"content": "用户住在北京", "content_type": "fact", "formed_by": "stated"},
            {"content": "用户喜欢面食", "formed_by": "stated"},
            {"content": "用户可能怕冷", "content_type": "hypothesis", "formed_by": "inferred"},
            {"content": "用户爱喝茶", "content_type": "beverage_pref", "formed_by": "stated"},
        ]
    )
    assert (r.content_type_fallback.missing, r.content_type_fallback.invalid, r.content_type_fallback.out_of_scope) == (1, 1, 1)


def test_early_return_yields_zeroes() -> None:
    """无新事件早退时计数为 0（与 profile_size / prompt_chars 的 0 语义一致）。"""
    db = open_db(":memory:")
    ev = SqliteEvidenceStore(db, clock=_clock)
    evt = SqliteEventStore(db, clock=_clock)
    cog = SqliteCognitionStore(db, clock=_clock)
    llm = _StubLLM(json.dumps({"new": []}))
    r = consolidate("owner", event_store=evt, evidence_store=ev, cognition_store=cog, llm=llm, now_iso=T, lang="en")
    assert (r.content_type_fallback.missing, r.content_type_fallback.invalid, r.content_type_fallback.out_of_scope) == (0, 0, 0)
    assert len(cog.active("owner")) == 0
