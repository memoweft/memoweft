"""attribute + trends parity:Python 建同 setup + stub llm → 与 TS(shared/parity/{attribute,trends}.json)一致。

钉 attribute:现象筛选(min_phenomenon_support/未归因)+ 禁 state→state + 时间窗 + 短标号 + hypothesis_cap 封顶 + 支撑=原因+锚;
钉 trends:all() 历史口径 + 排除 confirmed + trend_min_count 门 + 短标号 + ruled + 不筛 allow_inference(P2-7)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from conftest import parity

from memoweft.attribute import attribute
from memoweft.llm.client import ChatMessage, UsageStats
from memoweft.store import open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.trends import aggregate_trends
from memoweft.types import Cognition, EvidenceInput, EvidenceLink, Lang, ModelTier, SourceKind


def _store_clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime(2026, 1, 2, tzinfo=timezone.utc)  # 2026-01-02T00:00:00.000Z


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


def _state_cog(id: str, content: str, created_at: str) -> Cognition:
    return Cognition(
        id=id, subject_id="owner", content=content, content_type="state", formed_by="stated",
        confidence=300, cred_status="low", scope=None, valid_at=None, invalid_at=None,
        asked_at=None, archived_at=None, muted_at=None, created_at=created_at, updated_at=created_at,
    )


def test_attribute_matches_ts() -> None:
    want: Any = parity("attribute.json")
    langs: list[Lang] = ["zh", "en"]
    for lang in langs:
        db = open_db(":memory:")
        ev = SqliteEvidenceStore(db, clock=_store_clock)
        cog = SqliteCognitionStore(db, clock=_store_clock)

        def put(sk: SourceKind, content: str, occurred: str, *, cloud: Optional[bool] = None) -> str:
            return ev.put(
                EvidenceInput(subject_id="owner", source_kind=sk, host_id="local", occurred_at=occurred,
                              raw_content=content, allow_cloud_read=cloud)
            ).id

        p1 = put("spoken", "昨晚没睡好", "2026-01-01T22:00:00.000Z")
        p2 = put("spoken", "今天也没睡好", "2026-01-01T23:00:00.000Z")  # 最晚 → 现象锚
        put("observed", "凌晨3点还在打游戏", "2026-01-01T03:00:00.000Z", cloud=True)  # 候选原因(显式授权)
        put("spoken", "晚上喝了咖啡", "2026-01-01T20:00:00.000Z")  # 候选原因
        cog.insert(
            _state_cog("cog-phenom", "最近总没睡好", "2026-01-01T00:00:00.000Z"),
            [EvidenceLink(evidence_id=p1, relation="support"), EvidenceLink(evidence_id=p2, relation="support")],
        )
        llm = _StubLLM(json.dumps({"hypotheses": [{"content": "可能是熬夜打游戏导致没睡好", "based_on_evidence_ids": ["e1"]}]}, ensure_ascii=False))
        result = attribute("owner", evidence_store=ev, cognition_store=cog, llm=llm, clock=_now, lang=lang)

        w = want[lang]
        got_msgs = [{"role": m.role, "content": m.content} for m in llm.seen[0]]
        assert got_msgs == w["messages"], f"[{lang}] attribute messages 分叉"
        got = [
            {
                "content": h.cognition.content, "contentType": h.cognition.content_type, "formedBy": h.cognition.formed_by,
                "confidence": h.cognition.confidence, "credStatus": h.cognition.cred_status,
                "basedOnCount": len(h.based_on_evidence_ids), "phenomenon": h.phenomenon,
            }
            for h in result.hypotheses
        ]
        assert got == w["hypotheses"], f"[{lang}] hypotheses 分叉"
        assert result.considered_phenomena == w["consideredPhenomena"]
        assert result.llm_calls == w["llmCalls"]
        db.close()


def test_trends_matches_ts() -> None:
    want: Any = parity("trends.json")
    langs: list[Lang] = ["zh", "en"]
    for lang in langs:
        db = open_db(":memory:")
        ev = SqliteEvidenceStore(db, clock=_store_clock)
        cog = SqliteCognitionStore(db, clock=_store_clock)

        def put(content: str, occurred: str) -> str:
            return ev.put(
                EvidenceInput(subject_id="owner", source_kind="spoken", host_id="local", occurred_at=occurred, raw_content=content)
            ).id

        t1 = put("好累", "2026-01-01T10:00:00.000Z")
        t2 = put("没睡好", "2026-01-01T11:00:00.000Z")
        t3 = put("提不起劲", "2026-01-01T12:00:00.000Z")
        cog.insert(_state_cog("cog-s1", "很累", "2026-01-01T00:00:01.000Z"), [EvidenceLink(evidence_id=t1, relation="support")])
        cog.insert(_state_cog("cog-s2", "没睡好", "2026-01-01T00:00:02.000Z"), [EvidenceLink(evidence_id=t2, relation="support")])
        cog.insert(_state_cog("cog-s3", "提不起劲", "2026-01-01T00:00:03.000Z"), [EvidenceLink(evidence_id=t3, relation="support")])
        llm = _StubLLM(json.dumps({"trends": [{"content": "用户最近持续情绪低落", "based_on_evidence_ids": ["e1", "e2", "e3"]}]}, ensure_ascii=False))
        result = aggregate_trends("owner", evidence_store=ev, cognition_store=cog, llm=llm, now=_now(), lang=lang)

        w = want[lang]
        got_msgs = [{"role": m.role, "content": m.content} for m in llm.seen[0]]
        assert got_msgs == w["messages"], f"[{lang}] trends messages 分叉"
        got = [
            {
                "content": t.content, "contentType": t.content_type, "formedBy": t.formed_by,
                "confidence": t.confidence, "credStatus": t.cred_status,
                "supportCount": sum(1 for s in cog.sources_of(t.id) if s.relation == "support"),
            }
            for t in result.trends
        ]
        assert got == w["trends"], f"[{lang}] trends 分叉"
        assert result.considered_count == w["consideredCount"]
        assert result.llm_calls == w["llmCalls"]
        db.close()
