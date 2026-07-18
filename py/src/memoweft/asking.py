"""主动询问与冲突复核，与 TypeScript asking 实现保持行为一致。

产出「该问什么 + 附什么证据」的结构化建议(AskProposal);**是否开口、最终措辞归宿主**。
控制流纯规则(候选筛选/证据挂载/observed 优先/tier 门/askedAt 去重写);仅【措辞】一步可选走 LLM——
  未提供 llm 时使用确定性模板。提问仅作为宿主建议，不进入证据库。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Sequence

from ._jsstr import js_trim
from .clock import Clock, system_clock, to_iso_z
from .config import CONFIG, Config, ConfidenceBand, resolve_lang
from .llm.client import ChatMessage, LLMClient
from .llm.prompts import prompt_text
from .privacy import filter_readable_by_tier
from .store.cognition import SqliteCognitionStore
from .store.evidence import SqliteEvidenceStore
from .types import CognitionPatch, CredStatus, Evidence, Lang


@dataclass(frozen=True, slots=True)
class EvidenceBrief:
    id: str
    summary: str


@dataclass(slots=True)
class AskProposal:
    cognition_id: str
    kind: Literal["hypothesis", "conflict"]
    hypothesis: str
    question: str
    evidence: list[EvidenceBrief]
    confidence: int
    cred_status: CredStatus
    #: 仅用于冲突复核：与支撑证据并列展示的反对证据。
    contradict_evidence: Optional[list[EvidenceBrief]] = None


@dataclass(slots=True)
class AskResult:
    proposals: list[AskProposal] = field(default_factory=list)
    llm_calls: int = 0


def _brief(e: Evidence) -> EvidenceBrief:
    return EvidenceBrief(id=e.id, summary=e.summary or e.raw_content)


# ── proposeAsk ──


def _template_question(hypothesis: str, evidence: Sequence[EvidenceBrief], lang: Lang) -> str:
    """生成带证据且保留不确定性的确定性后备问题。"""
    if lang == "zh":
        shown = "、".join(f"「{e.summary}」" for e in evidence)
        if not shown:
            return f"我有个不太确定的猜测：{hypothesis}。是这样吗？"
        return f"我看到{shown}，所以在想：{hypothesis}。是这样吗？"
    shown = ", ".join(f'"{e.summary}"' for e in evidence)
    if not shown:
        return f"I have a hunch I'm not too sure about: {hypothesis}. Is that right?"
    return f"I noticed {shown}, which got me wondering: {hypothesis}. Is that right?"


def _phrase_question(hypothesis: str, evidence: Sequence[EvidenceBrief], llm: LLMClient, lang: Lang) -> str:
    """使用 LLM 调整问题措辞；空响应时返回确定性模板。"""
    shown = "\n".join(f"- {e.summary}" for e in evidence)
    user = f"【假设】{hypothesis}\n【证据】\n{shown}" if lang == "zh" else f"[Hypothesis] {hypothesis}\n[Evidence]\n{shown}"
    messages = [
        ChatMessage(role="system", content=prompt_text("proposeAsk", lang)),
        ChatMessage(role="user", content=user),
    ]
    text = js_trim(llm.chat(messages))
    return text or _template_question(hypothesis, evidence, lang)


def propose_ask(
    subject_id: str,
    *,
    cognition_store: SqliteCognitionStore,
    evidence_store: SqliteEvidenceStore,
    llm: Optional[LLMClient] = None,
    cfg: Config = CONFIG,
    clock: Clock = system_clock,
    lang: Optional[Lang] = None,
    max_asks: Optional[int] = None,
    confidence_band: Optional[ConfidenceBand] = None,
    askable_statuses: Optional[Sequence[str]] = None,
    mark_asked: bool = True,
) -> AskResult:
    """为低置信假设生成带证据的询问建议。"""
    lg = lang if lang is not None else resolve_lang()
    ma = max_asks if max_asks is not None else cfg.asking.max_asks
    band = confidence_band if confidence_band is not None else cfg.asking.confidence_band
    statuses = askable_statuses if askable_statuses is not None else cfg.asking.askable_statuses

    # 候选 = active 假设里:没问过、状态可问、把握度在"将信将疑"带内;把握度高的优先。
    pool = [
        c
        for c in cognition_store.active(subject_id)
        if c.content_type == "hypothesis"
        and c.asked_at is None
        and c.cred_status in statuses
        and band.min <= c.confidence <= band.max
    ]
    candidates = sorted(pool, key=lambda c: c.confidence, reverse=True)[:ma]

    before = llm.call_count if llm is not None else 0
    proposals: list[AskProposal] = []
    for cog in candidates:
        support_ids = [lk.evidence_id for lk in cognition_store.sources_of(cog.id) if lk.relation == "support"]
        support_evidence = [e for e in (evidence_store.get(i) for i in support_ids) if e is not None]
        # observed 优先亮出来(稳定分区,非全序)。
        support_evidence = sorted(support_evidence, key=lambda e: 0 if e.source_kind == "observed" else 1)
        evidence = [_brief(e) for e in support_evidence]
        # 隐私边界：措辞模型仅接收当前 tier 可读的证据；宿主展示数据保留完整证据集合。
        tier = llm.tier if (llm is not None and llm.tier is not None) else "cloud"
        readable = [_brief(e) for e in filter_readable_by_tier(support_evidence, tier)]
        question = _phrase_question(cog.content, readable, llm, lg) if llm is not None else _template_question(cog.content, evidence, lg)
        proposals.append(
            AskProposal(
                cognition_id=cog.id, kind="hypothesis", hypothesis=cog.content, question=question,
                evidence=evidence, confidence=cog.confidence, cred_status=cog.cred_status,
            )
        )
        if mark_asked:
            cognition_store.update(cog.id, CognitionPatch(asked_at=to_iso_z(clock())))

    after = llm.call_count if llm is not None else 0
    return AskResult(proposals=proposals, llm_calls=after - before)


# ── revisitConflicts ──


def _revisit_template(content: str, support: Sequence[EvidenceBrief], contradict: Sequence[EvidenceBrief], lang: Lang) -> str:
    """生成并列呈现支撑与反对证据的确定性后备问题。"""
    if lang == "zh":
        s = "、".join(f"「{e.summary}」" for e in support)
        c = "、".join(f"「{e.summary}」" for e in contradict)
        if s and c:
            return f'关于"{content}"——一方面{s}，另一方面又{c}。现在到底是哪样呢？'
        return f'关于"{content}"，我这边的信息有点对不上，能帮我确认下现在是怎样吗？'
    s = ", ".join(f'"{e.summary}"' for e in support)
    c = ", ".join(f'"{e.summary}"' for e in contradict)
    if s and c:
        return f'About "{content}" — on one hand {s}, but on the other hand {c}. Which is it actually now?'
    return f'About "{content}", the signals on my end don\'t quite line up. Could you help me confirm how it stands now?'


def _revisit_phrase(
    content: str, support: Sequence[EvidenceBrief], contradict: Sequence[EvidenceBrief], llm: LLMClient, lang: Lang
) -> str:
    """使用 LLM 调整冲突复核措辞；空响应时返回确定性模板。"""
    s = "\n".join(f"- {e.summary}" for e in support)
    c = "\n".join(f"- {e.summary}" for e in contradict)
    user = (
        f"【认知】{content}\n【支撑证据】\n{s}\n【反对证据】\n{c}"
        if lang == "zh"
        else f"[Cognition] {content}\n[Supporting evidence]\n{s}\n[Opposing evidence]\n{c}"
    )
    messages = [
        ChatMessage(role="system", content=prompt_text("revisitConflicts", lang)),
        ChatMessage(role="user", content=user),
    ]
    text = js_trim(llm.chat(messages))
    return text or _revisit_template(content, support, contradict, lang)


def revisit_conflicts(
    subject_id: str,
    *,
    cognition_store: SqliteCognitionStore,
    evidence_store: SqliteEvidenceStore,
    llm: Optional[LLMClient] = None,
    cfg: Config = CONFIG,
    clock: Clock = system_clock,
    lang: Optional[Lang] = None,
    max_asks: Optional[int] = None,
    mark_asked: bool = True,
) -> AskResult:
    """为 conflicted 认知生成同时包含支撑与反对证据的复核问题。"""
    lg = lang if lang is not None else resolve_lang()
    ma = max_asks if max_asks is not None else cfg.asking.max_asks
    # 候选 = active 冲突认知里没复看过的(无置信带过滤、无重排,直接吃 active() 序)。
    candidates = [
        c for c in cognition_store.active(subject_id) if c.cred_status == "conflicted" and c.asked_at is None
    ][:ma]

    before = llm.call_count if llm is not None else 0
    proposals: list[AskProposal] = []
    for cog in candidates:
        links = cognition_store.sources_of(cog.id)
        support_ev = [e for e in (evidence_store.get(lk.evidence_id) for lk in links if lk.relation == "support") if e is not None]
        contradict_ev = [e for e in (evidence_store.get(lk.evidence_id) for lk in links if lk.relation == "contradict") if e is not None]
        support = [_brief(e) for e in support_ev]
        contradict = [_brief(e) for e in contradict_ev]
        tier = llm.tier if (llm is not None and llm.tier is not None) else "cloud"
        if llm is not None:
            question = _revisit_phrase(
                cog.content,
                [_brief(e) for e in filter_readable_by_tier(support_ev, tier)],
                [_brief(e) for e in filter_readable_by_tier(contradict_ev, tier)],
                llm, lg,
            )
        else:
            question = _revisit_template(cog.content, support, contradict, lg)
        proposals.append(
            AskProposal(
                cognition_id=cog.id, kind="conflict", hypothesis=cog.content, question=question,
                evidence=support, contradict_evidence=contradict, confidence=cog.confidence, cred_status=cog.cred_status,
            )
        )
        if mark_asked:
            cognition_store.update(cog.id, CognitionPatch(asked_at=to_iso_z(clock())))

    after = llm.call_count if llm is not None else 0
    return AskResult(proposals=proposals, llm_calls=after - before)
