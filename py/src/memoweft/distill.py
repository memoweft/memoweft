"""事件化(distill)—— 移植自 src/distillation/distill.ts。把"还没整理成事件"的近期证据总结成一个带情境的事件。

隐私门:tier 读取权(cloud 筛 allow_cloud_read / local 筛 allow_local_read)+ inference 门(allow_inference);
被挡证据【不算已覆盖】、留 pending 下轮再扫(D8 覆盖修复)。红线:只总结用户话 + 情境,不含助手回话。
async 取舍:同步(llm.chat 同步),见 D-0043。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ._jsstr import js_trim
from .config import CONFIG, Config, resolve_lang
from .llm.client import ChatMessage, LLMClient
from .llm.prompts import prompt_text
from .privacy import filter_readable_by_tier
from .source_label import ai_context_suffix, source_label
from .store.event import SqliteEventStore
from .store.evidence import SqliteEvidenceStore
from .types import Event, EventInput, Lang


@dataclass(slots=True)
class DistillResult:
    event: Optional[Event]
    pending_count: int
    tier_blocked_count: int
    llm_calls: int


def distill(
    subject_id: str,
    evidence_store: SqliteEvidenceStore,
    event_store: SqliteEventStore,
    llm: LLMClient,
    *,
    lang: Optional[Lang] = None,
    cfg: Config = CONFIG,
) -> DistillResult:
    """对齐 distill.ts:35-88。两道早退 + tier+inference 隐私门 + 时间锚 + D8 覆盖修复。"""
    evidence = [e for e in evidence_store.all() if e.subject_id == subject_id]
    covered = set(event_store.covered_evidence_ids(subject_id))
    pending = sorted((e for e in evidence if e.id not in covered), key=lambda e: e.occurred_at)

    if len(pending) == 0:
        return DistillResult(event=None, pending_count=0, tier_blocked_count=0, llm_calls=0)

    # 隐私门:tier 读取权 + inference 门(被挡不进事件,防经 summary 间接渗进画像)。tier 绑 llm,缺省 cloud。
    tier = llm.tier if llm.tier is not None else "cloud"
    readable = filter_readable_by_tier(pending, tier)
    digestible = [e for e in readable if e.allow_inference]
    tier_blocked_count = len(pending) - len(readable)
    if len(digestible) == 0:
        return DistillResult(event=None, pending_count=len(pending), tier_blocked_count=tier_blocked_count, llm_calls=0)

    lg = lang if lang is not None else resolve_lang()
    lines = "\n".join(
        f"({e.occurred_at[:16]}) {source_label(e.source_kind, lg)}{e.raw_content}"
        f"{ai_context_suffix(evidence_store.preceding_ai_context_of(e.id), lg)}"
        for e in digestible
    )
    if lg == "zh":
        user_head = "按时间顺序的材料（每行带来源标注；[行为观察] / [工具返回] 不是用户原话）："
    else:
        user_head = (
            "Material in chronological order (each line is tagged with its source; "
            "[observed behavior] / [tool result] are NOT the user's own words):"
        )
    messages = [
        ChatMessage(role="system", content=prompt_text("distill", lg)),
        ChatMessage(role="user", content=f"{user_head}\n{lines}"),
    ]

    before = llm.call_count
    summary = js_trim(llm.chat(messages))
    llm_calls = llm.call_count - before

    # D8 覆盖修复:event 只覆盖【真消化进 summary 的】digestible;被挡的不覆盖、留 pending 可再扫。
    event = event_store.put(
        EventInput(
            subject_id=subject_id,
            summary=summary,
            occurred_at=digestible[0].occurred_at,
            evidence_ids=[e.id for e in digestible],
        )
    )
    return DistillResult(
        event=event, pending_count=len(pending), tier_blocked_count=tier_blocked_count, llm_calls=llm_calls
    )
