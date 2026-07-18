"""画像增量更新，与 TypeScript consolidate 实现保持行为一致。

处理未消化的新事件,连同现有画像给 LLM,判断新事件对画像的影响,输出四类:
  new / reinforce(含并存新 stated 认知)/ correct(旧失效保留、采纳新)/ conflict(标 conflicted、不消解)。
把握度由规则计算而非采信模型自报；推测保持低置信；失效判断保留完整溯源。

关键兼容约束：私有 _resolve_evidence_id 只去除 `ev-`（不同于共享 resolve_echoed_id 的 `ev-|cog-`）；
  pick_support 保序去重用 dict.fromkeys;四分支顺序;reinforce 并存用 add 非 cited;MIN_ID_PREFIX 从 config 读。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional, cast

from ._jsstr import js_trim, utf16_length
from .config import CONFIG, Config, resolve_lang
from .confidence import compute_confidence, derive_cred_status
from .formed_by import derive_formed_by
from .echoed_id import resolve_echoed_id
from .llm.client import ChatMessage, LLMClient
from .llm.json_repair import parse_json_object_with_repair
from .llm.prompts import get_prompt, prompt_text
from .privacy import filter_readable_by_tier
from .source_label import ai_context_suffix, source_label
from .store.cognition import SqliteCognitionStore
from .store.event import SqliteEventStore
from .store.evidence import SqliteEvidenceStore
from .store.semantic_resolution import SqliteSemanticResolutionStore
from .store.transaction import Transaction, noop_transaction
from .types import (
    AssertionStrength,
    CarrierInput,
    Cognition,
    CognitionInput,
    CognitionPatch,
    ContentType,
    ConfidenceInputs,
    EvidenceLink,
    FormedBy,
    Lang,
    PromptAct,
    PropositionOrigin,
    Resolution,
    ResponseAct,
    SemanticResolutionInput,
)

_logger = logging.getLogger("memoweft.consolidate")

_VALID_TYPES = ("fact", "preference", "goal", "project", "state", "trait")
_VALID_FORMED = ("stated", "observed", "ruled", "confirmed", "inferred")
_VALID_RESPONSE_ACT = ("affirm", "negate", "select", "elaborate", "ask", "none", "other")
_VALID_PROMPT_ACT = ("propose", "ask", "state", "none", "other")
_VALID_ORIGIN = ("user_stated", "assistant_proposed")
_VALID_STRENGTH = ("explicit", "weak", "none")


@dataclass(slots=True)
class ConsolidateResult:
    created: list[Cognition]
    reinforced: int
    corrected: int
    conflicted: int
    processed_events: int
    llm_calls: int
    profile_size: int
    prompt_chars: int


@dataclass(slots=True)
class _Carrier:
    source_kind: str
    preceding_ai_context: Optional[str]


@dataclass(slots=True)
class _EventView:
    summary: str
    occurred_at: str
    utterances: list[tuple[str, str]]  # (evidence_id, text)


@dataclass(slots=True)
class _ResEntry:
    resolved_content: str
    response_act: Optional[ResponseAct]
    prompt_act: Optional[PromptAct]
    proposition_origin: Optional[PropositionOrigin]
    assertion_strength: Optional[AssertionStrength]
    required_context: Optional[str]


@dataclass(slots=True)
class _Mutation:
    created: list[Cognition]
    reinforced: int
    corrected: int
    conflicted: int


def _cited_ids(c: dict[str, Any]) -> list[str]:
    """读取候选引用的证据 id；兼容字段别名，并保留 TS `?? []` 对空数组的语义。"""
    v = c.get("support_evidence_ids")
    if v is not None:
        return list(v)
    v = c.get("evidence_ids")
    if v is not None:
        return list(v)
    return []


def _resolve_evidence_id(
    raw: Optional[str], whitelist: set[str], tag_to_evidence_id: dict[str, str], cfg: Config
) -> Optional[str]:
    """将模型返回的 evidence id 解析为白名单内的唯一 id；无法解析时返回 None。

    顺序为标号 tag、精确匹配、仅去除 `ev-` 后的唯一前缀匹配；未知、歧义或过短前缀返回 None。
    此处不能复用会同时去除 `ev-|cog-` 的 resolve_echoed_id，否则以 cog- 开头的 evidence id 可能误匹配。
    """
    if not raw:
        return None
    by_tag = tag_to_evidence_id.get(js_trim(raw))
    if by_tag is not None and by_tag in whitelist:
        return by_tag
    if raw in whitelist:
        return raw
    bare = re.sub(r"^ev-", "", raw, count=1, flags=re.IGNORECASE)
    if utf16_length(bare) < cfg.min_id_prefix:
        return None
    hit: Optional[str] = None
    for id_ in whitelist:
        if not id_.startswith(bare):
            continue
        if hit is not None:
            return None  # 歧义 → 不猜
        hit = id_
    return hit


def _pick_cognition(c: dict[str, Any]) -> Optional[tuple[str, ContentType, bool]]:
    """从兼容字段中提取认知；缺少类型时使用 fact，无内容时返回 None。"""
    raw = c.get("content")
    if raw is None:
        raw = c.get("new_content")
    if raw is None:
        raw = c.get("cognition")
    content = js_trim(raw) if isinstance(raw, str) else ""
    if not content:
        return None
    ct_raw = c.get("content_type")
    content_type: ContentType = cast(ContentType, ct_raw) if ct_raw in _VALID_TYPES else "fact"
    fb_raw = c.get("formed_by")
    declared = fb_raw if fb_raw in _VALID_FORMED else None
    model_says_inferred = declared is None or declared == "inferred"
    return content, content_type, model_says_inferred


def _build_messages(
    existing: list[Cognition], events: list[_EventView], lang: Lang
) -> tuple[list[ChatMessage], dict[str, str]]:
    """构造 prompt，并返回短标号到真实 evidence id 的映射。"""
    zh = lang == "zh"
    if existing:
        profile = "\n".join(f"- [{c.id}] ({c.content_type}) {c.content}" for c in existing)
    else:
        profile = "（空）" if zh else "(none)"
    tag_to_evidence_id: dict[str, str] = {}
    n = 0
    material_parts: list[str] = []
    for e in events:
        head = f"· {'事件' if zh else 'Event'} ({e.occurred_at[:16]}) {e.summary}"
        line_parts: list[str] = []
        for uid, text in e.utterances:
            n += 1
            tag = f"e{n}"  # 跨事件连续编号
            tag_to_evidence_id[tag] = uid
            line_parts.append(f"    - [{tag}] {text}")
        lines = "\n".join(line_parts)
        material_parts.append(f"{head}\n{lines}" if lines else head)
    material = "\n".join(material_parts)
    if zh:
        body = f"【现有画像】：\n{profile}\n\n【新材料】：\n{material}"
    else:
        body = f"[Existing profile]:\n{profile}\n\n[New material]:\n{material}"
    messages = [
        ChatMessage(role="system", content=prompt_text("consolidate", lang)),
        ChatMessage(role="user", content=body),
    ]
    return messages, tag_to_evidence_id


def consolidate(
    subject_id: str,
    *,
    event_store: SqliteEventStore,
    evidence_store: SqliteEvidenceStore,
    cognition_store: SqliteCognitionStore,
    llm: LLMClient,
    semantic_resolution_store: Optional[SqliteSemanticResolutionStore] = None,
    transaction: Optional[Transaction] = None,
    cfg: Config = CONFIG,
    now_iso: str = "",
    lang: Optional[Lang] = None,
) -> ConsolidateResult:
    """执行增量合并；now_iso 是 correct 分支使用的失效时间戳，由调用方或注入时钟提供。"""
    new_events = event_store.unconsolidated(subject_id)
    if len(new_events) == 0:
        return ConsolidateResult(
            created=[], reinforced=0, corrected=0, conflicted=0, processed_events=0, llm_calls=0, profile_size=0, prompt_chars=0
        )

    existing = cognition_store.active(subject_id)
    existing_ids = {c.id for c in existing}

    def resolve_cog_id(raw_id: Optional[str], op: str) -> Optional[str]:
        id_ = resolve_echoed_id(raw_id, existing_ids)
        if raw_id and id_ is None:
            _logger.warning("[memoweft/consolidate] %s 引用的 cognition_id 认不出(模型写的:%r)", op, raw_id)
        return id_

    valid_evidence: set[str] = set()
    spoken_evidence: set[str] = set()
    carrier_of: dict[str, _Carrier] = {}
    tier = llm.tier if llm.tier is not None else "cloud"
    lg = lang if lang is not None else resolve_lang()

    events: list[_EventView] = []
    for ev in new_events:
        evidences = [e for e in (evidence_store.get(eid) for eid in event_store.evidence_of(ev.id)) if e is not None]
        utterances: list[tuple[str, str]] = []
        for e in [x for x in filter_readable_by_tier(evidences, tier) if x.allow_inference]:
            valid_evidence.add(e.id)
            if e.source_kind == "spoken":
                spoken_evidence.add(e.id)
            ai_ctx = evidence_store.preceding_ai_context_of(e.id)
            carrier_of[e.id] = _Carrier(source_kind=e.source_kind, preceding_ai_context=ai_ctx)
            text = source_label(e.source_kind, lg) + e.raw_content + ai_context_suffix(ai_ctx, lg)
            utterances.append((e.id, text))
        events.append(_EventView(summary=ev.summary, occurred_at=ev.occurred_at, utterances=utterances))

    messages, tag_to_evidence_id = _build_messages(existing, events, lg)
    prompt_chars = sum(utf16_length(m.content) for m in messages)
    before = llm.call_count
    out: dict[str, Any] = parse_json_object_with_repair(llm, messages, lang=lg) or {}
    llm_calls = llm.call_count - before

    def pick_support(ids: list[str]) -> list[str]:
        resolved = (_resolve_evidence_id(i, valid_evidence, tag_to_evidence_id, cfg) for i in ids)
        return list(dict.fromkeys(r for r in resolved if r is not None))

    # resolutionOf:规整 + 收窄(白名单 spokenEvidence);落库与派生共用这一份。
    resolution_of: dict[str, _ResEntry] = {}
    for r in out.get("resolutions") or []:
        eid = _resolve_evidence_id(r.get("evidence_id"), spoken_evidence, tag_to_evidence_id, cfg)
        if not eid:
            continue
        rc = r.get("resolved_content")
        resolved = js_trim(rc) if isinstance(rc, str) else ""
        if not resolved:
            continue
        if eid in resolution_of:
            continue  # 同证据先到先得
        ra = r.get("response_act")
        pa = r.get("prompt_act")
        po = r.get("proposition_origin")
        as_ = r.get("assertion_strength")
        rq = r.get("required_context")
        rqt = js_trim(rq) if isinstance(rq, str) else ""
        resolution_of[eid] = _ResEntry(
            resolved_content=resolved,
            response_act=cast(ResponseAct, ra) if ra in _VALID_RESPONSE_ACT else None,
            prompt_act=cast(PromptAct, pa) if pa in _VALID_PROMPT_ACT else None,
            proposition_origin=cast(PropositionOrigin, po) if po in _VALID_ORIGIN else None,
            assertion_strength=cast(AssertionStrength, as_) if as_ in _VALID_STRENGTH else None,
            required_context=rqt or None,
        )

    raw_resolution_count = len(out.get("resolutions") or [])
    if raw_resolution_count > 0 and len(resolution_of) == 0 and len(spoken_evidence) > 0:
        _logger.warning(
            "[memoweft/consolidate] 模型产了 %d 条解析、却一条都没落地(spoken 证据 %d 条、llmCalls=%d)——多半是 evidence_id 认不出",
            raw_resolution_count, len(spoken_evidence), llm_calls,
        )

    def resolve_formed_by(model_says_inferred: bool, support_ids: list[str]) -> FormedBy:
        if model_says_inferred:
            return "inferred"
        inputs: list[CarrierInput] = []
        for id_ in support_ids:
            c = carrier_of.get(id_)
            res = resolution_of.get(id_)
            inputs.append(
                CarrierInput(
                    source_kind=cast(Any, c.source_kind) if c is not None else "observed",
                    preceding_ai_context=c.preceding_ai_context if c is not None else None,
                    resolution=Resolution(response_act=res.response_act, proposition_origin=res.proposition_origin)
                    if res is not None
                    else None,
                )
            )
        return derive_formed_by(inputs) or "inferred"

    def mutate() -> _Mutation:
        created: list[Cognition] = []
        reinforced = 0
        corrected = 0
        conflicted = 0

        # new
        for c in out.get("new") or []:
            p = _pick_cognition(c)
            if p is None:
                continue
            content, content_type, model_says_inferred = p
            support = pick_support(_cited_ids(c))
            if len(support) == 0:
                continue
            formed_by = resolve_formed_by(model_says_inferred, support)
            confidence = compute_confidence(
                ConfidenceInputs(content_type=content_type, formed_by=formed_by, support_count=len(support), contradict_count=0), cfg
            )
            created.append(
                cognition_store.put(
                    CognitionInput(
                        subject_id=subject_id, content=content, content_type=content_type, formed_by=formed_by,
                        confidence=confidence, cred_status=derive_cred_status(confidence, 0, content_type, cfg),
                        evidence=[EvidenceLink(evidence_id=i, relation="support") for i in support],
                    )
                )
            )

        # reinforce
        for c in out.get("reinforce") or []:
            cog_id = resolve_cog_id(c.get("cognition_id"), "reinforce")
            cog = cognition_store.get(cog_id) if cog_id else None
            if cog is None or cog.invalid_at:
                continue
            cited = pick_support(_cited_ids(c))
            if len(cited) == 0:
                continue
            already = {s.evidence_id for s in cognition_store.sources_of(cog.id)}
            add = [i for i in cited if i not in already]
            if add:
                cognition_store.add_evidence(cog.id, [EvidenceLink(evidence_id=i, relation="support") for i in add])
            links = cognition_store.sources_of(cog.id)
            support_count = sum(1 for l in links if l.relation == "support")
            contradict_count = sum(1 for l in links if l.relation == "contradict")
            formed_by = cog.formed_by  # 恒继承（取消就地升级）
            confidence = compute_confidence(
                ConfidenceInputs(content_type=cog.content_type, formed_by=formed_by, support_count=support_count, contradict_count=contradict_count), cfg
            )
            cognition_store.update(
                cog.id, CognitionPatch(confidence=confidence, cred_status=derive_cred_status(confidence, contradict_count, cog.content_type, cfg))
            )
            reinforced += 1
            # 并存新 stated 认知:旧 confirmed + 本次新增(add)载体维派生成 stated → 并存一条(用 add 非 cited)。
            if cog.formed_by == "confirmed" and len(add) > 0 and resolve_formed_by(False, add) == "stated":
                up_conf = compute_confidence(
                    ConfidenceInputs(content_type=cog.content_type, formed_by="stated", support_count=len(add), contradict_count=0), cfg
                )
                created.append(
                    cognition_store.put(
                        CognitionInput(
                            subject_id=subject_id, content=cog.content, content_type=cog.content_type, formed_by="stated",
                            confidence=up_conf, cred_status=derive_cred_status(up_conf, 0, cog.content_type, cfg),
                            evidence=[EvidenceLink(evidence_id=i, relation="support") for i in add],
                        )
                    )
                )

        # correct
        for c in out.get("correct") or []:
            old_id = resolve_cog_id(c.get("cognition_id"), "correct")
            old = cognition_store.get(old_id) if old_id else None
            p = _pick_cognition(c)
            if old is None or old.invalid_at or p is None:
                continue
            content, content_type, model_says_inferred = p
            support = pick_support(_cited_ids(c))
            if len(support) == 0:
                continue
            cognition_store.update(old.id, CognitionPatch(invalid_at=now_iso))  # 标失效、保留可溯源
            formed_by = resolve_formed_by(model_says_inferred, support)
            confidence = compute_confidence(
                ConfidenceInputs(content_type=content_type, formed_by=formed_by, support_count=len(support), contradict_count=0), cfg
            )
            created.append(
                cognition_store.put(
                    CognitionInput(
                        subject_id=subject_id, content=content, content_type=content_type, formed_by=formed_by,
                        confidence=confidence, cred_status=derive_cred_status(confidence, 0, content_type, cfg),
                        evidence=[EvidenceLink(evidence_id=i, relation="support") for i in support],
                    )
                )
            )
            corrected += 1

        # conflict
        for c in out.get("conflict") or []:
            cog_id = resolve_cog_id(c.get("cognition_id"), "conflict")
            cog = cognition_store.get(cog_id) if cog_id else None
            if cog is None or cog.invalid_at:
                continue
            contra = pick_support(_cited_ids(c))
            if len(contra) == 0:
                continue
            already = {s.evidence_id for s in cognition_store.sources_of(cog.id)}
            add = [i for i in contra if i not in already]
            if add:
                cognition_store.add_evidence(cog.id, [EvidenceLink(evidence_id=i, relation="contradict") for i in add])
            cognition_store.update(cog.id, CognitionPatch(cred_status="conflicted"))
            conflicted += 1

        # resolutions 落库(幂等 + resolverVersion 绑 prompt 版本)
        resolver_version = f"consolidate@{get_prompt('consolidate').version}"
        for eid, r in resolution_of.items():
            if semantic_resolution_store is not None and semantic_resolution_store.of_evidence(eid) is not None:
                continue
            if semantic_resolution_store is not None:
                semantic_resolution_store.put(
                    SemanticResolutionInput(
                        evidence_id=eid, resolved_content=r.resolved_content, resolver_version=resolver_version,
                        response_act=r.response_act, prompt_act=r.prompt_act, proposition_origin=r.proposition_origin,
                        assertion_strength=r.assertion_strength, required_context=r.required_context,
                    )
                )
        event_store.mark_consolidated([e.id for e in new_events])
        return _Mutation(created=created, reinforced=reinforced, corrected=corrected, conflicted=conflicted)

    run_tx: Transaction = transaction if transaction is not None else noop_transaction
    mutation = cast(_Mutation, run_tx(mutate))

    return ConsolidateResult(
        created=mutation.created, reinforced=mutation.reinforced, corrected=mutation.corrected, conflicted=mutation.conflicted,
        processed_events=len(new_events), llm_calls=llm_calls, profile_size=len(existing), prompt_chars=prompt_chars,
    )
