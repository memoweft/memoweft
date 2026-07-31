"""趋势聚合的派生内容隐私门：不可推理或不可读来源不得进入模型。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from memoweft.llm.client import ChatMessage, UsageStats
from memoweft.store import open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.trends import aggregate_trends
from memoweft.types import CognitionInput, EvidenceInput, EvidenceLink, ModelTier


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


class _StubLLM:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.seen: list[list[ChatMessage]] = []

    def chat(self, messages: list[ChatMessage]) -> str:
        self.seen.append(messages)
        return self._reply

    @property
    def call_count(self) -> int:
        return len(self.seen)

    @property
    def tier(self) -> Optional[ModelTier]:
        return "cloud"

    @property
    def usage(self) -> Optional[UsageStats]:
        return None


def test_trends_excludes_non_inference_and_mixed_authorization_states() -> None:
    db = open_db(":memory:")
    evidence = SqliteEvidenceStore(db, clock=_clock)
    cognition = SqliteCognitionStore(db, clock=_clock)
    try:
        allowed_ids: list[str] = []
        for index, text in enumerate(["很累", "没睡好", "提不起劲"]):
            item = evidence.put(
                EvidenceInput(
                    subject_id="owner",
                    source_kind="spoken",
                    host_id="local",
                    occurred_at=f"2026-01-01T1{index}:00:00.000Z",
                    raw_content=text,
                    allow_cloud_read=True,
                    allow_inference=True,
                )
            )
            cognition.put(
                CognitionInput(
                    subject_id="owner",
                    content=f"状态：{text}",
                    content_type="state",
                    formed_by="stated",
                    confidence=300,
                    cred_status="low",
                    evidence=[EvidenceLink(evidence_id=item.id, relation="support")],
                )
            )
            allowed_ids.append(item.id)

        no_inference = evidence.put(
            EvidenceInput(
                subject_id="owner",
                source_kind="spoken",
                host_id="local",
                occurred_at="2026-01-01T14:00:00.000Z",
                raw_content="允许上云但禁止推理的原话",
                allow_cloud_read=True,
                allow_inference=False,
            )
        )
        cognition.put(
            CognitionInput(
                subject_id="owner",
                content="禁止推理的派生状态",
                content_type="state",
                formed_by="inferred",
                confidence=250,
                cred_status="low",
                evidence=[
                    EvidenceLink(evidence_id=no_inference.id, relation="support")
                ],
            )
        )
        local_only = evidence.put(
            EvidenceInput(
                subject_id="owner",
                source_kind="observed",
                host_id="local",
                occurred_at="2026-01-01T15:00:00.000Z",
                raw_content="仅本地可读的原话",
                allow_cloud_read=False,
                allow_inference=True,
            )
        )
        cognition.put(
            CognitionInput(
                subject_id="owner",
                content="混合授权来源派生的敏感状态",
                content_type="state",
                formed_by="inferred",
                confidence=250,
                cred_status="low",
                evidence=[
                    EvidenceLink(evidence_id=allowed_ids[0], relation="support"),
                    EvidenceLink(evidence_id=local_only.id, relation="support"),
                ],
            )
        )

        llm = _StubLLM(
            json.dumps(
                {
                    "trends": [
                        {
                            "content": "用户最近持续情绪低落",
                            "based_on_evidence_ids": ["e1", "e2", "e3"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        result = aggregate_trends(
            "owner",
            evidence_store=evidence,
            cognition_store=cognition,
            llm=llm,
            now=datetime(2026, 1, 2, tzinfo=timezone.utc),
            lang="zh",
        )

        prompt = llm.seen[0][1].content
        assert "允许上云但禁止推理的原话" not in prompt
        assert "禁止推理的派生状态" not in prompt
        assert "仅本地可读的原话" not in prompt
        assert "混合授权来源派生的敏感状态" not in prompt
        assert len(result.trends) == 1
        linked = {
            link.evidence_id for link in cognition.sources_of(result.trends[0].id)
        }
        assert linked == set(allowed_ids)
    finally:
        db.close()
