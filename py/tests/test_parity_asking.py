"""asking parity:proposeAsk / revisitConflicts × 模板路径 / LLM 措辞路径 → 与 TS(shared/parity/asking.json)一致。

钉候选筛选 + observed 优先(稳定分区)+ 模板文案逐字 + LLM messages 字节/trim/空回落 + 两面证据 + askedAt 去重写()。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from conftest import parity

from memoweft.asking import AskProposal, propose_ask, revisit_conflicts
from memoweft.llm.client import ChatMessage, UsageStats
from memoweft.store import open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.types import Cognition, ContentType, CredStatus, EvidenceInput, EvidenceLink, Lang, ModelTier, SourceKind

T = "2026-01-01T00:00:00.000Z"


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


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


def _cog(id: str, content: str, ct: ContentType, cs: CredStatus, conf: int) -> Cognition:
    return Cognition(
        id=id, subject_id="owner", content=content, content_type=ct, formed_by="inferred", confidence=conf, cred_status=cs,
        scope=None, valid_at=None, invalid_at=None, asked_at=None, archived_at=None, muted_at=None, created_at=T, updated_at=T,
    )


def _dump(p: AskProposal) -> dict[str, Any]:
    return {
        "cognitionId": p.cognition_id, "kind": p.kind, "hypothesis": p.hypothesis, "question": p.question,
        "evidenceSummaries": [e.summary for e in p.evidence],
        "contradictSummaries": [e.summary for e in p.contradict_evidence] if p.contradict_evidence is not None else None,
        "confidence": p.confidence, "credStatus": p.cred_status,
    }


def _setup_ask() -> tuple[SqliteEvidenceStore, SqliteCognitionStore]:
    db = open_db(":memory:")
    ev = SqliteEvidenceStore(db, clock=_clock)
    cog = SqliteCognitionStore(db, clock=_clock)

    def put(sk: SourceKind, content: str, *, cloud: Optional[bool] = None) -> str:
        return ev.put(
            EvidenceInput(subject_id="owner", source_kind=sk, host_id="local", occurred_at=T, raw_content=content, allow_cloud_read=cloud)
        ).id

    e1 = put("spoken", "我最近老熬夜")
    e2 = put("observed", "凌晨3点还在打游戏", cloud=True)  # observed 优先亮出来
    cog.insert(
        _cog("cog-hypo", "可能是熬夜导致没睡好", "hypothesis", "candidate", 240),
        [EvidenceLink(evidence_id=e1, relation="support"), EvidenceLink(evidence_id=e2, relation="support")],
    )
    return ev, cog


def _setup_conflict() -> tuple[SqliteEvidenceStore, SqliteCognitionStore]:
    db = open_db(":memory:")
    ev = SqliteEvidenceStore(db, clock=_clock)
    cog = SqliteCognitionStore(db, clock=_clock)

    def put(content: str) -> str:
        return ev.put(EvidenceInput(subject_id="owner", source_kind="spoken", host_id="local", occurred_at=T, raw_content=content)).id

    e3 = put("我喜欢早睡")
    e4 = put("昨晚熬到3点")
    cog.insert(
        _cog("cog-conflict", "喜欢早睡", "preference", "conflicted", 600),
        [EvidenceLink(evidence_id=e3, relation="support"), EvidenceLink(evidence_id=e4, relation="contradict")],
    )
    return ev, cog


def test_asking_matches_ts() -> None:
    want: Any = parity("asking.json")
    langs: list[Lang] = ["zh", "en"]
    for lang in langs:
        w = want[lang]

        # ① proposeAsk 模板路径(无 llm)
        ev, cog = _setup_ask()
        r = propose_ask("owner", cognition_store=cog, evidence_store=ev, clock=_clock, lang=lang)
        exp = w["proposeAskTemplate"]
        assert [_dump(p) for p in r.proposals] == exp["proposals"], f"[{lang}] proposeAsk 模板分叉"
        assert r.llm_calls == exp["llmCalls"]
        c = cog.get("cog-hypo")
        assert c is not None and c.asked_at == exp["askedAt"]  # markAsked 写入

        # ② proposeAsk LLM 措辞路径
        ev, cog = _setup_ask()
        llm = _StubLLM("  你最近是不是熬夜比较多?  ")  # 带首尾空白验 trim
        r = propose_ask("owner", cognition_store=cog, evidence_store=ev, llm=llm, clock=_clock, lang=lang)
        exp = w["proposeAskLlm"]
        assert [{"role": m.role, "content": m.content} for m in llm.seen[0]] == exp["messages"], f"[{lang}] proposeAsk messages 分叉"
        assert [_dump(p) for p in r.proposals] == exp["proposals"], f"[{lang}] proposeAsk LLM 分叉"
        assert r.llm_calls == exp["llmCalls"]

        # ③ revisitConflicts 模板路径
        ev, cog = _setup_conflict()
        r = revisit_conflicts("owner", cognition_store=cog, evidence_store=ev, clock=_clock, lang=lang)
        exp = w["revisitTemplate"]
        assert [_dump(p) for p in r.proposals] == exp["proposals"], f"[{lang}] revisit 模板分叉"
        assert r.llm_calls == exp["llmCalls"]

        # ④ revisitConflicts LLM 措辞路径
        ev, cog = _setup_conflict()
        llm = _StubLLM("  你现在到底是早睡还是熬夜?  ")
        r = revisit_conflicts("owner", cognition_store=cog, evidence_store=ev, llm=llm, clock=_clock, lang=lang)
        exp = w["revisitLlm"]
        assert [{"role": m.role, "content": m.content} for m in llm.seen[0]] == exp["messages"], f"[{lang}] revisit messages 分叉"
        assert [_dump(p) for p in r.proposals] == exp["proposals"], f"[{lang}] revisit LLM 分叉"
        assert r.llm_calls == exp["llmCalls"]
