"""distill 证据→事件 parity:Python 建同证据集 + stub llm → messages/event/计数与 TS(distill.json)一致。

钉 messages 逐字节(system=distill prompt/user=材料行 含 sourceLabel/aiContextSuffix/occurredAt.slice)、
时间锚（digestible[0]）、隐私门（observed cloud 挡 / infer=false 不消化）、summary trim 与覆盖计数。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from conftest import parity

from memoweft.distill import DistillResult, distill
from memoweft.llm.client import ChatMessage, UsageStats
from memoweft.store import open_db
from memoweft.store.event import SqliteEventStore
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.types import EvidenceInput, Lang, ModelTier, SourceKind


class _StubLLM:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.seen: list[list[ChatMessage]] = []
        self._n = 0

    def chat(self, messages: list[ChatMessage]) -> str:
        self.seen.append(messages)
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


def _fixed() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _run(lang: Lang) -> tuple[DistillResult, _StubLLM, SqliteEventStore]:
    db = open_db(":memory:")
    ev_store = SqliteEvidenceStore(db, clock=_fixed)
    evt_store = SqliteEventStore(db, clock=_fixed)

    def put(
        sk: SourceKind,
        content: str,
        occurred: str,
        *,
        preceding: Optional[str] = None,
        allow_inference: Optional[bool] = None,
    ) -> None:
        ev_store.put(
            EvidenceInput(
                subject_id="owner", source_kind=sk, host_id="local", occurred_at=occurred, raw_content=content,
                preceding_ai_context=preceding, allow_inference=allow_inference,
            )
        )

    # 插入序打乱,验按 occurred_at 排序 + 隐私门各分支(与生成器 parityDistill 同证据集)。
    put("spoken", "我最近在学 Rust", "2026-01-01T10:00:00.000Z")
    put("observed", "凌晨3点还在打游戏", "2026-01-01T03:00:00.000Z")
    put("spoken", "是的", "2026-01-01T11:00:00.000Z", preceding="AI:你喜欢爬山吧?")
    put("spoken", "不想被推断", "2026-01-01T09:00:00.000Z", allow_inference=False)
    llm = _StubLLM("  用户在学 Rust 并确认了偏好。  ")
    result = distill("owner", ev_store, evt_store, llm, lang=lang)
    return result, llm, evt_store


def test_distill_matches_ts() -> None:
    want: Any = parity("distill.json")
    langs: list[Lang] = ["zh", "en"]
    for lang in langs:
        result, llm, evt_store = _run(lang)
        w = want[lang]
        got_messages = [{"role": m.role, "content": m.content} for m in llm.seen[0]]
        assert got_messages == w["messages"], f"[{lang}] messages 分叉"
        assert result.event is not None
        assert result.event.summary == w["eventSummary"]  # trim 后
        assert result.event.occurred_at == w["eventOccurredAt"]  # 时间锚 digestible[0]
        assert result.pending_count == w["pendingCount"]
        assert result.tier_blocked_count == w["tierBlockedCount"]
        assert result.llm_calls == w["llmCalls"]
        assert len(evt_store.evidence_of(result.event.id)) == w["digestibleCount"]


def test_distill_early_exits() -> None:
    db = open_db(":memory:")
    ev_store = SqliteEvidenceStore(db, clock=_fixed)
    evt_store = SqliteEventStore(db, clock=_fixed)
    llm = _StubLLM("x")
    # 早退①:无 pending → 不调模型
    r0 = distill("owner", ev_store, evt_store, llm, lang="en")
    assert r0.event is None and r0.pending_count == 0 and llm.call_count == 0
    # 早退②:有 pending 但全被隐私门挡(observed cloud 挡)→ 不调模型
    ev_store.put(EvidenceInput(subject_id="owner", source_kind="observed", host_id="local",
                               occurred_at="2026-01-01T00:00:00.000Z", raw_content="观察"))
    r1 = distill("owner", ev_store, evt_store, llm, lang="en")
    assert r1.event is None and r1.pending_count == 1 and r1.tier_blocked_count == 1 and llm.call_count == 0
