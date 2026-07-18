"""transaction 可重入 + 原子回滚();consolidate 包进事务的原子性。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import pytest

from memoweft.consolidate import consolidate
from memoweft.llm.client import ChatMessage, UsageStats
from memoweft.store import make_transaction, noop_transaction, open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.store.event import SqliteEventStore
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.types import CognitionInput, Event, EvidenceInput, ModelTier

T = "2026-01-01T00:00:00.000Z"


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _mk(content: str) -> CognitionInput:
    return CognitionInput(subject_id="owner", content=content, content_type="fact", formed_by="stated", confidence=600, cred_status="limited")


def test_transaction_commit_and_rollback() -> None:
    db = open_db(":memory:")
    cog = SqliteCognitionStore(db, clock=_clock)
    tx = make_transaction(db)
    tx(lambda: cog.put(_mk("提交的")))
    assert len(cog.all("owner")) == 1

    def boom() -> None:
        cog.put(_mk("会被回滚"))
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        tx(boom)
    assert len(cog.all("owner")) == 1  # 回滚:仍 1 条


def test_transaction_reentrant() -> None:
    db = open_db(":memory:")
    cog = SqliteCognitionStore(db, clock=_clock)
    tx = make_transaction(db)

    def outer() -> None:
        cog.put(_mk("外"))
        tx(lambda: cog.put(_mk("里")))  # 里层直接跑,不嵌套 BEGIN

    tx(outer)
    assert len(cog.all("owner")) == 2

    def outer_boom() -> None:
        cog.put(_mk("外2"))
        tx(lambda: cog.put(_mk("里2")))
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        tx(outer_boom)
    assert len(cog.all("owner")) == 2  # 外2/里2 随外层一起回滚


def test_noop_transaction() -> None:
    assert noop_transaction(lambda: 42) == 42


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


def test_consolidate_atomic_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    db = open_db(":memory:")
    ev = SqliteEvidenceStore(db, clock=_clock)
    evt = SqliteEventStore(db, clock=_clock)
    cog = SqliteCognitionStore(db, clock=_clock)
    e1 = ev.put(EvidenceInput(subject_id="owner", source_kind="spoken", host_id="local", occurred_at=T, raw_content="我喜欢猫")).id
    evt.insert(Event(id="evt1", subject_id="owner", summary="聊天", occurred_at=T, created_at=T), [e1], consolidated=False)
    llm = _StubLLM(json.dumps({"new": [{"content": "喜欢猫", "content_type": "preference", "formed_by": "stated", "support_evidence_ids": ["e1"]}]}))
    tx = make_transaction(db)

    def boom(ids: list[str]) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(evt, "mark_consolidated", boom)  # 最后一步抛错 → 整段回滚
    with pytest.raises(RuntimeError):
        consolidate("owner", event_store=evt, evidence_store=ev, cognition_store=cog, llm=llm, transaction=tx, now_iso=T, lang="en")
    # 回滚:new 认知未落库、事件仍未消化(不会"认知写了但事件没标 → 下轮重复")
    assert len(cog.active("owner")) == 0
    assert len(evt.unconsolidated("owner")) == 1
