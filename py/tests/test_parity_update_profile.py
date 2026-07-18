"""updateProfile 编排 parity:distill→consolidate→attribute→重索引 与 TS(shared/parity/update-profile.json)一致。

钉串链顺序 + 各步 llmCalls + metrics 透传(promptChars UTF-16)+ 索引只含 active∧未 muted + 索引失败不回滚()。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from conftest import parity

from memoweft.llm.client import ChatMessage, UsageStats
from memoweft.store import make_transaction, open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.store.event import SqliteEventStore
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.store.semantic_resolution import SqliteSemanticResolutionStore
from memoweft.types import EvidenceInput, Lang, ModelTier
from memoweft.update_profile import update_profile

T = "2026-01-01T00:00:00.000Z"

_REPLIES = [
    "用户聊到每天喝咖啡的习惯",
    '{"new":[{"content":"喜欢喝咖啡","content_type":"preference","formed_by":"stated","support_evidence_ids":["e1"]}]}',
]


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


class _SeqLLM:
    """按调用序返回不同回复(① distill 摘要 ② consolidate JSON)。"""

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self._n = 0

    def chat(self, messages: list[ChatMessage]) -> str:
        r = self._replies[self._n] if self._n < len(self._replies) else "{}"
        self._n += 1
        return r

    @property
    def call_count(self) -> int:
        return self._n

    @property
    def tier(self) -> Optional[ModelTier]:
        return "cloud"

    @property
    def usage(self) -> Optional[UsageStats]:
        return None


class _StubRetriever:
    def __init__(self, fail: bool = False) -> None:
        self.items: list[tuple[str, str]] = []
        self._fail = fail

    def index_all(self, items: list[tuple[str, str]]) -> None:
        if self._fail:
            raise RuntimeError("嵌入器未启动")
        self.items = items


def _setup() -> tuple[SqliteEvidenceStore, SqliteEventStore, SqliteCognitionStore, SqliteSemanticResolutionStore, Any]:
    db = open_db(":memory:")
    ev = SqliteEvidenceStore(db, clock=_clock)
    evt = SqliteEventStore(db, clock=_clock)
    cog = SqliteCognitionStore(db, clock=_clock)
    sem = SqliteSemanticResolutionStore(db, clock=_clock)
    for content in ("我每天都喝咖啡", "尤其是早上那杯"):
        ev.put(EvidenceInput(subject_id="owner", source_kind="spoken", host_id="local", occurred_at=T, raw_content=content))
    return ev, evt, cog, sem, make_transaction(db)


def test_update_profile_matches_ts() -> None:
    want: Any = parity("update-profile.json")
    langs: list[Lang] = ["zh", "en"]
    for lang in langs:
        ev, evt, cog, sem, tx = _setup()
        llm = _SeqLLM(list(_REPLIES))
        retr = _StubRetriever()
        r = update_profile(
            "owner", evidence_store=ev, event_store=evt, cognition_store=cog, retriever=retr, llm=llm,
            semantic_resolution_store=sem, transaction=tx, clock=_clock, lang=lang,
        )
        w = want[lang]
        # distill
        assert r.distilled.event is not None and r.distilled.event.summary == w["distilled"]["eventSummary"]
        assert r.distilled.pending_count == w["distilled"]["pendingCount"]
        assert r.distilled.tier_blocked_count == w["distilled"]["tierBlockedCount"]
        assert r.distilled.llm_calls == w["distilled"]["llmCalls"]
        # consolidate
        got_created = [
            {"content": c.content, "contentType": c.content_type, "formedBy": c.formed_by, "confidence": c.confidence, "credStatus": c.cred_status}
            for c in r.consolidated.created
        ]
        assert got_created == w["consolidated"]["created"]
        assert r.consolidated.processed_events == w["consolidated"]["processedEvents"]
        assert r.consolidated.llm_calls == w["consolidated"]["llmCalls"]
        assert r.consolidated.profile_size == w["consolidated"]["profileSize"]
        assert r.consolidated.prompt_chars == w["consolidated"]["promptChars"]  # UTF-16 长度
        # attribute(无 state 现象 → 不调模型)
        assert len(r.attributed.hypotheses) == w["attributed"]["hypothesesCount"]
        assert r.attributed.considered_phenomena == w["attributed"]["consideredPhenomena"]
        assert r.attributed.llm_calls == w["attributed"]["llmCalls"]
        # 索引 + metrics
        assert r.indexed == w["indexed"]
        assert r.index_error == w["indexError"]
        assert [t for (_i, t) in retr.items] == w["indexedTexts"]
        assert r.metrics.profile_size == w["metrics"]["profileSize"]
        assert r.metrics.prompt_chars == w["metrics"]["promptChars"]
        assert llm.call_count == w["totalLlmCalls"]


def test_update_profile_index_failure_does_not_rollback() -> None:
    """索引是读路径优化:检索器抛错 → index_error 记下,但画像已落库不回滚。"""
    ev, evt, cog, sem, tx = _setup()
    llm = _SeqLLM(list(_REPLIES))
    r = update_profile(
        "owner", evidence_store=ev, event_store=evt, cognition_store=cog, retriever=_StubRetriever(fail=True),
        llm=llm, semantic_resolution_store=sem, transaction=tx, clock=_clock, lang="zh",
    )
    assert r.index_error is not None and "嵌入器未启动" in r.index_error
    assert r.indexed == 0
    assert len(r.consolidated.created) == 1  # 画像照落
    assert len(cog.active("owner")) == 1  # 库里在
