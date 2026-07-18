"""跨会话趋势 —— 移植自 src/background/trends.ts。反复出现的瞬时状态 → 聚成持续模式认知(formed_by=ruled)。

先规则保证"窗口内同类状态真出现够多次"(trend_min_count),再让 LLM 归纳命名——频率客观、LLM 只负责归纳。
纪律:用 all()【历史口径】(含已失效/归档,看"曾反复出现");**排除 confirmed**(D-0033 结构性对抗护栏,防诱导附和被数成趋势);
  同一批证据聚过不重复(dedup);隐私门只筛 tier、**不筛 allow_inference**(与 attribute 不同)。同步(见 D-0043)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from ._jsstr import js_trim
from .clock import epoch_ms, ms_to_iso
from .config import CONFIG, Config, resolve_lang
from .confidence import compute_confidence, derive_cred_status
from .echoed_id import resolve_echoed_id
from .llm.client import ChatMessage, LLMClient
from .llm.json_repair import parse_json_object_with_repair
from .llm.prompts import prompt_text
from .privacy import filter_readable_by_tier
from .store.cognition import SqliteCognitionStore
from .store.evidence import SqliteEvidenceStore
from .types import Cognition, CognitionInput, ConfidenceInputs, EvidenceLink, Lang


@dataclass(slots=True)
class TrendResult:
    trends: list[Cognition]
    considered_count: int
    llm_calls: int


def _build_messages(
    items: list[tuple[str, str, str, str]], lang: Lang
) -> tuple[list[ChatMessage], dict[str, str]]:
    """items = (evidence_id, state_content, text, occurred_at)。短标号 [e1](D-0036)。对齐 trends.ts:46-71。"""
    zh = lang == "zh"
    tag_to_id: dict[str, str] = {}
    parts: list[str] = []
    for k, (eid, state, text, at) in enumerate(items):
        tag = f"e{k + 1}"
        tag_to_id[tag] = eid
        if zh:
            parts.append(f"- [{tag}] ({at[:10]}) 状态「{state}」← 原话：{text}")
        else:
            parts.append(f'- [{tag}] ({at[:10]}) state "{state}" ← utterance: {text}')
    listing = "\n".join(parts)
    body = f"【近期反复出现的状态】：\n{listing}" if zh else f"[Recent recurring states]:\n{listing}"
    messages = [
        ChatMessage(role="system", content=prompt_text("trends", lang)),
        ChatMessage(role="user", content=body),
    ]
    return messages, tag_to_id


def aggregate_trends(
    subject_id: str,
    *,
    evidence_store: SqliteEvidenceStore,
    cognition_store: SqliteCognitionStore,
    llm: LLMClient,
    now: datetime,
    cfg: Config = CONFIG,
    lang: Optional[Lang] = None,
) -> TrendResult:
    """对齐 trends.ts:77-157。"""
    lg = lang if lang is not None else resolve_lang()
    window_start = ms_to_iso(epoch_ms(now) - cfg.trend_window_days * 86_400_000)

    # all() 历史口径 + 排除 confirmed(护栏,勿删)。
    states = [c for c in cognition_store.all(subject_id) if c.content_type == "state" and c.formed_by != "confirmed"]
    items: list[tuple[str, str, str, str]] = []
    window_evidence: set[str] = set()
    tier = llm.tier if llm.tier is not None else "cloud"
    for s in states:
        for link in cognition_store.sources_of(s.id):
            if link.relation != "support":
                continue
            e = evidence_store.get(link.evidence_id)
            if (
                e is not None
                and len(filter_readable_by_tier([e], tier)) > 0
                and e.occurred_at >= window_start
                and e.id not in window_evidence
            ):
                window_evidence.add(e.id)
                items.append((e.id, s.content, e.summary or e.raw_content, e.occurred_at))

    if len(items) < cfg.trend_min_count:
        return TrendResult(trends=[], considered_count=len(items), llm_calls=0)  # 规则筛:不够频

    # dedup:已有 active 趋势覆盖过的证据——这批全被盖过就别重复聚。
    covered: set[str] = set()
    for t in [c for c in cognition_store.active(subject_id) if c.content_type == "trend"]:
        for lk in cognition_store.sources_of(t.id):
            covered.add(lk.evidence_id)
    if all(i in covered for i in window_evidence):
        return TrendResult(trends=[], considered_count=len(items), llm_calls=0)

    before = llm.call_count
    messages, tag_to_id = _build_messages(items, lg)
    out: dict[str, Any] = parse_json_object_with_repair(llm, messages, lang=lg) or {}
    llm_calls = llm.call_count - before

    trends: list[Cognition] = []
    for raw in out.get("trends") or []:
        rc = raw.get("content")
        if rc is None:
            rc = raw.get("trend")
        content = js_trim(rc) if isinstance(rc, str) else ""
        if not content:
            continue
        based_raw = raw.get("based_on_evidence_ids") or []
        cited = list(
            dict.fromkeys(
                r for r in (resolve_echoed_id(i, window_evidence, tag_to_id) for i in based_raw) if r is not None
            )
        )
        if len(cited) == 0:
            continue  # 没引到真实状态证据 → 不硬编
        confidence = compute_confidence(
            ConfidenceInputs(content_type="trend", formed_by="ruled", support_count=len(cited), contradict_count=0), cfg
        )
        trends.append(
            cognition_store.put(
                CognitionInput(
                    subject_id=subject_id, content=content, content_type="trend", formed_by="ruled",
                    confidence=confidence, cred_status=derive_cred_status(confidence, 0, "trend", cfg),
                    evidence=[EvidenceLink(evidence_id=i, relation="support") for i in cited],
                )
            )
        )
    return TrendResult(trends=trends, considered_count=len(items), llm_calls=llm_calls)
