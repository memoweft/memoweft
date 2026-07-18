"""consolidate 四分支 parity:Python 建同 setup + stub llm 固定 LLMOut → 与 TS(shared/parity/consolidate.json)一致。

钉 messages 逐字节 + created 结构(new/并存 stated/correct)+ reinforce confidence(恒继承 confirmed)+ correct 失效 +
验证 conflict conflicted + resolution 落库 + markConsolidated + existing 排序 + 私有 resolve_evidence_id 短标号。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from conftest import parity

from memoweft.consolidate import consolidate
from memoweft.llm.client import ChatMessage, UsageStats
from memoweft.store import open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.store.event import SqliteEventStore
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.store.semantic_resolution import SqliteSemanticResolutionStore
from memoweft.types import Cognition, ContentType, CredStatus, Event, EvidenceInput, EvidenceLink, FormedBy, Lang, ModelTier

T = "2026-01-01T00:00:00.000Z"

_LLM_OUT: dict[str, Any] = {
    "new": [{"content": "在学 Rust", "content_type": "project", "formed_by": "stated", "support_evidence_ids": ["e1"]}],
    "reinforce": [{"cognition_id": "cog-reinf", "support_evidence_ids": ["e2"]}],
    "correct": [{"cognition_id": "cog-corr", "content": "在上海工作", "content_type": "fact", "formed_by": "stated", "support_evidence_ids": ["e3"]}],
    "conflict": [{"cognition_id": "cog-conf", "support_evidence_ids": ["e4"]}],
    "resolutions": [{"evidence_id": "e2", "resolved_content": "用户确认每天喝咖啡", "response_act": "elaborate", "proposition_origin": "user_stated"}],
}


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


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _cog(id: str, content: str, ct: ContentType, fb: FormedBy, conf: int, cs: CredStatus) -> Cognition:
    return Cognition(
        id=id, subject_id="owner", content=content, content_type=ct, formed_by=fb, confidence=conf, cred_status=cs,
        scope=None, valid_at=None, invalid_at=None, asked_at=None, archived_at=None, muted_at=None, created_at=T, updated_at=T,
    )


def _setup() -> tuple[SqliteEvidenceStore, SqliteEventStore, SqliteCognitionStore, SqliteSemanticResolutionStore, str]:
    db = open_db(":memory:")
    ev = SqliteEvidenceStore(db, clock=_clock)
    evt = SqliteEventStore(db, clock=_clock)
    cog = SqliteCognitionStore(db, clock=_clock)
    sem = SqliteSemanticResolutionStore(db, clock=_clock)
    cog.insert(_cog("cog-reinf", "喜欢喝咖啡", "preference", "confirmed", 280, "candidate"), [EvidenceLink(evidence_id="ev-old-reinf", relation="support")])
    cog.insert(_cog("cog-corr", "在北京工作", "fact", "stated", 600, "limited"), [EvidenceLink(evidence_id="ev-old-corr", relation="support")])
    cog.insert(_cog("cog-conf", "喜欢早睡", "preference", "stated", 600, "limited"), [EvidenceLink(evidence_id="ev-old-conf", relation="support")])

    def put(content: str) -> str:
        return ev.put(EvidenceInput(subject_id="owner", source_kind="spoken", host_id="local", occurred_at=T, raw_content=content)).id

    e1, e2, e3, e4 = put("我最近在学 Rust"), put("对，我每天都喝咖啡"), put("其实我在上海工作了"), put("我昨晚熬夜了")
    evt.insert(Event(id="evt1", subject_id="owner", summary="用户聊了近况", occurred_at=T, created_at=T), [e1, e2, e3, e4], consolidated=False)
    return ev, evt, cog, sem, e2


def test_consolidate_matches_ts() -> None:
    want: Any = parity("consolidate.json")
    langs: list[Lang] = ["zh", "en"]
    for lang in langs:
        ev, evt, cog, sem, e2 = _setup()
        llm = _StubLLM(json.dumps(_LLM_OUT, ensure_ascii=False))
        result = consolidate(
            "owner", event_store=evt, evidence_store=ev, cognition_store=cog, llm=llm,
            semantic_resolution_store=sem, now_iso=T, lang=lang,
        )
        w = want[lang]
        got_msgs = [{"role": m.role, "content": m.content} for m in llm.seen[0]]
        assert got_msgs == w["messages"], f"[{lang}] messages 分叉"
        got_created = [
            {
                "content": c.content, "contentType": c.content_type, "formedBy": c.formed_by,
                "confidence": c.confidence, "credStatus": c.cred_status,
                "supportCount": sum(1 for s in cog.sources_of(c.id) if s.relation == "support"),
            }
            for c in result.created
        ]
        assert got_created == w["created"], f"[{lang}] created 分叉"
        assert result.reinforced == w["reinforced"]
        assert result.corrected == w["corrected"]
        assert result.conflicted == w["conflicted"]
        assert result.processed_events == w["processedEvents"]
        cr = cog.get("cog-reinf")
        assert cr is not None and cr.confidence == w["cogReinfConfidence"] and cr.formed_by == w["cogReinfFormedBy"]
        cc = cog.get("cog-corr")
        assert cc is not None and (cc.invalid_at is not None) == w["cogCorrInvalidated"]
        cf = cog.get("cog-conf")
        assert cf is not None and cf.cred_status == w["cogConfCredStatus"]
        res = sem.of_evidence(e2)
        assert res is not None
        assert res.resolved_content == w["resolution"]["resolvedContent"]
        assert res.response_act == w["resolution"]["responseAct"]
        assert res.proposition_origin == w["resolution"]["propositionOrigin"]
        assert res.resolver_version == w["resolution"]["resolverVersion"]
        assert (len(evt.unconsolidated("owner")) == 0) == w["allEventsConsolidated"]
