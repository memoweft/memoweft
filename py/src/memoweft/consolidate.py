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
from dataclasses import dataclass, field
from collections.abc import Sequence
from typing import Any, Optional, Protocol, cast

from ._jsstr import js_trim, utf16_length
from .config import CONFIG, Config, resolve_lang
from .confidence import compute_confidence, derive_cred_status, is_hedged_stated
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
    HedgeInput,
    Lang,
    PromptAct,
    PropositionOrigin,
    Resolution,
    ResponseAct,
    SemanticResolutionInput,
)

_logger = logging.getLogger("memoweft.consolidate")

_VALID_TYPES = ("fact", "preference", "goal", "project", "state", "trait")
# ContentType 里合法、但 consolidate 不收的两值：只能由 attribute / trends 内部产生。
# 模型仍可能吐出它们（现有画像以 `- [id] (content_type) content` 回灌 prompt，标签它看得见），
# 故单列一因与拼写错误区分。与 TS 侧 OUT_OF_SCOPE_TYPES 对齐。
_OUT_OF_SCOPE_TYPES = ("hypothesis", "trend")
_VALID_FORMED = ("stated", "observed", "ruled", "confirmed", "inferred")
_VALID_RESPONSE_ACT = ("affirm", "negate", "select", "elaborate", "ask", "none", "other")
_VALID_PROMPT_ACT = ("propose", "ask", "state", "none", "other")
_VALID_ORIGIN = ("user_stated", "assistant_proposed")
_VALID_STRENGTH = ("explicit", "weak", "none")


@dataclass(slots=True)
class ContentTypeFallback:
    """写路径仪表（计量契约 · 只观测，不改变行为）：content_type 落 fact 兜底的次数，按触发因分。

    与 TS 侧 ConsolidateResult.contentTypeFallback 对齐。三者严重度不同，混计就判断不了该不该动语义：
      missing      模型没给该字段；
      invalid      给了但不在 _VALID_TYPES 六值内（拼错 / 幻觉值）；
      out_of_scope 给了 hypothesis / trend —— 这两值在 ContentType 里合法，只是 consolidate 不收。
                   这是唯一的【语义降级】：本应受 hypothesis_cap 与 2 天半衰期约束、
                   且应进 propose_ask 求证队列的推测，被兜成 fact（永不衰减、永不自动失效）并退出该队列。

    只统计【实际落库】的兜底，故该计数可直接读作「库里有多少条认知的类型是靠兜底定的」。
    """

    missing: int = 0
    invalid: int = 0
    out_of_scope: int = 0


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
    content_type_fallback: ContentTypeFallback = field(default_factory=ContentTypeFallback)


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
    # 计量随事务一同带出：放闭包外的话事务回滚时计数不回滚，会虚高（与 TS 侧同口径）。
    type_fallback: "ContentTypeFallback"


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


def _classify_type_fallback(raw: Any) -> Optional[str]:
    """判定本条 content_type 走没走兜底、因为什么。纯函数，不改变任何行为。

    返回 ContentTypeFallback 的字段名（missing / invalid / out_of_scope），类型合法时返回 None。
    与 TS 侧 classifyTypeFallback 对齐。
    """
    if raw is None or raw == "":
        return "missing"
    if raw in _VALID_TYPES:
        return None
    return "out_of_scope" if raw in _OUT_OF_SCOPE_TYPES else "invalid"


def _pick_cognition(c: dict[str, Any]) -> Optional[tuple[str, ContentType, bool, Optional[str]]]:
    """从兼容字段中提取认知；缺少类型时使用 fact，无内容时返回 None。

    第 4 个返回值是计量用的兜底原因（不改变行为），见 ContentTypeFallback。
    """
    raw = c.get("content")
    if raw is None:
        raw = c.get("new_content")
    if raw is None:
        raw = c.get("cognition")
    content = js_trim(raw) if isinstance(raw, str) else ""
    if not content:
        return None
    ct_raw = c.get("content_type")
    type_fallback = _classify_type_fallback(ct_raw)
    content_type: ContentType = cast(ContentType, ct_raw) if ct_raw in _VALID_TYPES else "fact"
    fb_raw = c.get("formed_by")
    declared = fb_raw if fb_raw in _VALID_FORMED else None
    model_says_inferred = declared is None or declared == "inferred"
    return content, content_type, model_says_inferred, type_fallback


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


# ── A5 矛盾并存护栏辅助（模块级，镜像 consolidate.ts）──────────────────
# shortlist 余弦阈值缺省：0.5（护栏量具实测：真矛盾对余弦低至 0.571 且相似度分不开矛盾/兼容，
#   判别全靠极性判、其误判率 0%，故阈值只是"进极性判"的成本闸；0.6 会漏召回，0.5 召回回满且不增误判）。
#   对齐 consolidate.ts GUARD_DEFAULT_SIMILARITY。
GUARD_DEFAULT_SIMILARITY = 0.5
# 每条候选最多做几次极性判（相似度降序前 N）：把 llm 调用量压到很小。
GUARD_DEFAULT_TOPK = 3


class _GuardEmbedder(Protocol):
    """护栏相似度所需的最小嵌入器接口（HashEmbedder 及任何 embed(texts)->向量 的实现都满足）。"""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class ContradictionGuard:
    """A5 护栏依赖（可选）：镜像 TS 的 ConsolidateDeps.contradictionGuard。"""

    embedder: _GuardEmbedder
    min_similarity: float = GUARD_DEFAULT_SIMILARITY
    top_k: int = GUARD_DEFAULT_TOPK


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度；任一为零向量或维度不齐返回 0（与 TS _cosine 同口径）。"""
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a)
    nb = sum(y * y for y in b)
    if na == 0 or nb == 0:
        return 0.0
    return float(dot / ((na**0.5) * (nb**0.5)))


def _judge_contradiction(llm: LLMClient, lang: Lang, existing_content: str, candidate_content: str) -> bool:
    """极性判断（护栏第二半，非确定性）：关于【同一个人】的两条陈述，作为当前状态是否【冲突】（不能同时为真）。
    只判「相反」不判「相关」——相似度已把候选压到同主题。
    ⚠ 提示词经护栏量具调优：旧版"保守·拿不准判否"在真实啰嗦措辞上召回仅 ~32%；改成明确纳入立场/偏好/目标/事实反转、
      看穿辩解从句与演化语气后，召回 93.5%、误判 0%。与 consolidate.ts 的 judgeContradiction 【逐字对齐】，改一处必须同改。"""
    zh = lang == "zh"
    if zh:
        sys = "\n".join(
            [
                "你比较关于【同一个人】的两条陈述，各自都当作 TA【当前】的状态，判断它们是否【冲突】——即不可能同时为真。",
                "算冲突(true)：",
                "· 偏好/态度反转：「讨厌跑步」vs「爱上跑步、是一天最爱」；「从小不吃香菜」vs「爱上香菜」。",
                "· 目标放弃/改向：「想当管理者」vs「决定继续做个人贡献者」；「要考研」vs「放弃考研去找工作」。",
                "· 事实状态改变、不能同为当前：「每周练六天」vs「已减到每周四天」；「住北京」vs「搬去上海了」。",
                "· 自我特质的重新评估。",
                "要【看穿】辩解从句、原因、时间/演化措辞（「以前」「如今」「但因为便宜才选」「渐渐变得」）——只看两条【当前】立场/事实是否互斥。",
                "不算冲突(false)：",
                "· 细化/子偏好：「爱喝咖啡」vs「尤其爱手冲」。",
                "· 强化，或不同侧面/方式：「讨厌跑步机」vs「爱户外越野跑」；「戒了含糖饮料」vs「照喝无糖黑咖啡」。",
                "· 仅程度差别，或两者本可同时为真。",
                '只输出 JSON：{"contradicts": true|false}。',
            ]
        )
        user = f"陈述甲：{existing_content}\n陈述乙：{candidate_content}\n作为同一个人的当前状态，它们冲突吗？"
    else:
        sys = "\n".join(
            [
                "Compare two statements about the SAME person, each taken as their CURRENT state, and decide if they CONFLICT — cannot both be true of the person right now.",
                "COUNT AS CONFLICT (true):",
                '· Reversed preference/attitude: "dislikes running" vs "has come to love running, favorite part of the day"; "unable to eat cilantro" vs "loves cilantro".',
                '· Abandoned/changed goal: "aiming to become a manager" vs "chose to stay an individual contributor"; "planning grad school" vs "gave up grad school for a job".',
                '· A factual state that changed and cannot both be current: "trains six days a week" vs "reduced to four days a week"; "lives in Beijing" vs "moved to Shanghai".',
                "· A re-evaluated self-trait.",
                'Look PAST justifying clauses, reasons, and time/evolution wording ("used to", "now", "but chose it because…", "has developed…"); judge only whether the two CURRENT stances/facts are incompatible.',
                "DO NOT count as conflict (false):",
                '· Refinement/sub-preference: "loves coffee" vs "especially loves pour-over".',
                '· Reinforcement, or a different facet/modality: "hates the treadmill" vs "loves outdoor trail runs"; "gave up sugary drinks" vs "still drinks black coffee".',
                "· Mere degree differences, or two things that can both be true at once.",
                'Output only JSON: {"contradicts": true|false}.',
            ]
        )
        user = f"Statement A: {existing_content}\nStatement B: {candidate_content}\nAs current states of the same person, do they conflict?"
    parsed = parse_json_object_with_repair(
        llm,
        [ChatMessage(role="system", content=sys), ChatMessage(role="user", content=user)],
        lang=lang,
    )
    return bool(parsed and parsed.get("contradicts") is True)


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
    contradiction_guard: Optional[ContradictionGuard] = None,
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

    def resolve_hedged(formed_by: FormedBy, support_ids: Sequence[str]) -> bool:
        """算一条认知的 hedged(含糊自述封顶的判据)。与 consolidate.ts 的 resolveHedged 逐位一致。

        与载体维【正交】:载体维答"谁的话",hedged 答"这话说得含不含糊"。封顶动作本身在
        compute_confidence 里(min(hedge_cap)),这里只出判据。

        **为什么是「库优先、内存兜底」而不是反过来**:文末的解析落库循环对"库里已有解析"的证据
        跳过写入(幂等),所以【库里那份才是会活下来的那份】。若让内存赢,就会出现:本轮按内存
        那份(马上要被丢弃的)算出 600,而此后每一次重算读到库里那份旧的算出 280 —— 同一条认知
        在 600/280 之间【永久分叉】。故:库里有就以库为准,内存只补库里还没有的(= 本轮新证据,
        它们的解析此刻确实还没落库 —— 落库循环在四个写循环之后)。

        semantic_resolution_store 未注入时只退化【历史证据】那一半(本轮证据仍准确),
        退化方向是 hedged=False(不封顶),与 is_hedged_stated 的"解析不出不臆造惩罚"同向 ——
        宁可少封,不可错封。

        不变式:support_ids 恒等于同一调用点传给 support_count 的那一个集合。
        """
        # 非 stated / 空支持集恒 False(is_hedged_stated 同判)——提前挡掉,省下重算期那次查表。
        if formed_by != "stated" or len(support_ids) == 0:
            return False
        stored: dict[str, HedgeInput] = {}
        if semantic_resolution_store is not None:
            for sr in semantic_resolution_store.for_evidence_ids(list(support_ids)):  # 一次批量查,无 N+1
                stored[sr.evidence_id] = HedgeInput(
                    proposition_origin=sr.proposition_origin, assertion_strength=sr.assertion_strength
                )
        view: list[Optional[HedgeInput]] = []
        for id_ in support_ids:
            hit = stored.get(id_)
            if hit is None:
                r = resolution_of.get(id_)  # 库里没有 → 回落本轮内存解析
                hit = (
                    HedgeInput(proposition_origin=r.proposition_origin, assertion_strength=r.assertion_strength)
                    if r is not None
                    else None
                )
            view.append(hit)
        return is_hedged_stated(formed_by, view)

    # ── A5 矛盾并存护栏：事务前预算「哪些 new 候选其实是对另一条认知的极性反转」。命中记两种锚点：
    #   · guard_hit_existing[候选下标]=旧认知 id：与【已入库旧认知】反转 → 挂到该旧认知；
    #   · guard_hit_new[候选下标]=更早候选下标：与【本轮更早、会落库的新候选】反转 → 挂到那条（先落库当锚点）。
    #   第二种补「同批盲区」（旧立场与反转同一轮抽出、光比旧认知会漏）。下方 new 循环据此改走 conflict。
    #   不接 embedder / 无候选 → 空 = 行为完全同旧（镜像 consolidate.ts）。
    guard_hit_existing: dict[int, str] = {}
    guard_hit_new: dict[int, int] = {}
    _guard_candidates = out.get("new") or []
    if contradiction_guard is not None and _guard_candidates:
        threshold = contradiction_guard.min_similarity
        top_k = contradiction_guard.top_k
        # 预算口径：每候选最多 2×top_k 次极性判——① 与相似旧认知最多 top_k 次、② 与本轮更早候选最多 top_k 次
        #   （① 命中即 break、跳过 ②）。默认 top_k=3 → 每候选最坏 6 次极性判；仅在护栏开启时发生。
        # 只对【会真落库】的候选（有内容 + 有可溯源支撑）算；cands 与 cand_vecs 平行，下标与下方 new 循环对齐。
        cands: list[tuple[int, str]] = []
        for idx, c in enumerate(_guard_candidates):
            p = _pick_cognition(c)
            if p is None:
                continue
            if len(pick_support(_cited_ids(c))) == 0:
                continue
            cands.append((idx, p[0]))  # p[0] = content
        if cands:
            cand_vecs: list[list[float]] = []
            exist_vecs: list[list[float]] = []
            try:
                cand_vecs = contradiction_guard.embedder.embed([t for _, t in cands])
                exist_vecs = contradiction_guard.embedder.embed([ex.content for ex in existing]) if existing else []
            except Exception as exc:  # noqa: BLE001 - 嵌入失败不该拖垮固化：记日志、退化成本轮不拦。
                _logger.warning("[memoweft/consolidate] A5 护栏嵌入失败，本轮跳过护栏：%s", exc)
            if cand_vecs:
                for k, (idx, cand_content) in enumerate(cands):
                    # ① 先比已入库旧认知。
                    ex_scored = [(ex, _cosine(cand_vecs[k], exist_vecs[j])) for j, ex in enumerate(existing)]
                    ex_short = [ex for ex, s in sorted(ex_scored, key=lambda t: t[1], reverse=True) if s >= threshold][
                        :top_k
                    ]
                    hit = False
                    for ex in ex_short:
                        if _judge_contradiction(llm, lg, ex.content, cand_content):
                            guard_hit_existing[idx] = ex.id
                            hit = True
                            break
                    if hit:
                        continue
                    # ② 再比本轮更早、且未被拦（会落库当锚点）的新候选——补同批盲区。cands[m] 的向量是 cand_vecs[m]。
                    new_scored = [
                        (cands[m][0], cands[m][1], _cosine(cand_vecs[k], cand_vecs[m]))
                        for m in range(k)
                        if cands[m][0] not in guard_hit_existing and cands[m][0] not in guard_hit_new
                    ]
                    new_short = [
                        (aidx, ac) for aidx, ac, s in sorted(new_scored, key=lambda t: t[2], reverse=True) if s >= threshold
                    ][:top_k]
                    for aidx, ac in new_short:
                        if _judge_contradiction(llm, lg, ac, cand_content):
                            guard_hit_new[idx] = aidx
                            break

    # llm_calls 计入 A5 护栏的极性判调用（及其 JSON 修复重试）——护栏在主固化请求之后才跑，
    #   沿用上面的快照会漏报 guarded 跑的调用数/成本（镜像 consolidate.ts；护栏默认关时此值不变）。
    llm_calls = llm.call_count - before

    def mutate() -> _Mutation:
        created: list[Cognition] = []
        reinforced = 0
        corrected = 0
        conflicted = 0
        fallback = ContentTypeFallback()

        def attach_contradiction(cog_id: str, contra_ids: Sequence[str]) -> bool:
            """把反证挂到旧认知并按【全链】重算把握度（conflict 分支与 A5 护栏共用同口径，
            避免"只翻 cred_status 不重算 → contradict_penalty 空转"回归）。formed_by 继承、
            不重派载体维；cred_status 交 derive_cred_status 在 contradict_count>0 下判 conflicted/contested。
            返回是否真挂上。镜像 consolidate.ts 的 attachContradiction。"""
            cog = cognition_store.get(cog_id)
            if cog is None or cog.invalid_at:
                return False
            if len(contra_ids) == 0:
                return False
            already = {s.evidence_id for s in cognition_store.sources_of(cog.id)}
            add = [i for i in contra_ids if i not in already]
            if add:
                cognition_store.add_evidence(cog.id, [EvidenceLink(evidence_id=i, relation="contradict") for i in add])
            links = cognition_store.sources_of(cog.id)
            support_ids = [lk.evidence_id for lk in links if lk.relation == "support"]
            support_count = len(support_ids)
            contradict_count = sum(1 for lk in links if lk.relation == "contradict")
            confidence = compute_confidence(
                ConfidenceInputs(
                    content_type=cog.content_type,
                    formed_by=cog.formed_by,
                    support_count=support_count,
                    contradict_count=contradict_count,
                    hedged=resolve_hedged(cog.formed_by, support_ids),
                ),
                cfg,
            )
            cognition_store.update(
                cog.id,
                CognitionPatch(
                    confidence=confidence,
                    cred_status=derive_cred_status(confidence, contradict_count, cog.content_type, cfg, support_count),
                ),
            )
            return True

        # new
        # 本轮已落库的 new 候选：候选下标 → 认知 id。供护栏「同批 new-vs-new」把后一条挂到先落库的锚点。
        created_by_idx: dict[int, str] = {}
        for idx, c in enumerate(out.get("new") or []):
            p = _pick_cognition(c)
            if p is None:
                continue
            content, content_type, model_says_inferred, type_fallback = p
            support = pick_support(_cited_ids(c))
            if len(support) == 0:
                continue
            # A5 护栏命中：这条「new」其实是对某条认知的极性反转 → 不新建那条相反行，改把它的原话当反证
            #   挂到锚点上（走 conflict 语义）。锚点=已入库旧认知，或本轮更早落库的新候选。
            anchor_id: Optional[str] = None
            if idx in guard_hit_existing:
                anchor_id = guard_hit_existing[idx]
            elif idx in guard_hit_new:
                anchor_id = created_by_idx.get(guard_hit_new[idx])
            if anchor_id is not None and attach_contradiction(anchor_id, support):
                conflicted += 1
                continue  # 不落新行、不计 type_fallback（未落库）
            # 锚点缺失（更早候选未落库/已失效）→ 退回正常新建，别把这条丢了。
            if type_fallback is not None:  # 计量：只统计实际落库的兜底（与 TS 侧同口径）
                setattr(fallback, type_fallback, getattr(fallback, type_fallback) + 1)
            formed_by = resolve_formed_by(model_says_inferred, support)
            # 接线点 1/5 · new(形成期):resolution_of 就地可用。不接则新建的含糊自述原样拿 600。
            confidence = compute_confidence(
                ConfidenceInputs(
                    content_type=content_type, formed_by=formed_by, support_count=len(support), contradict_count=0,
                    hedged=resolve_hedged(formed_by, support),  # 集合恒同 support_count 用的那一个
                ), cfg
            )
            created_cog = cognition_store.put(
                CognitionInput(
                    subject_id=subject_id, content=content, content_type=content_type, formed_by=formed_by,
                    confidence=confidence, cred_status=derive_cred_status(confidence, 0, content_type, cfg),
                    evidence=[EvidenceLink(evidence_id=i, relation="support") for i in support],
                )
            )
            created.append(created_cog)
            created_by_idx[idx] = created_cog.id  # 记为锚点：本轮后续新候选若与它反转，挂到它

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
            # support_ids 与 support_count 同源导出：hedged 的集合必须与算 support_count 的完全相同。
            support_ids = [l.evidence_id for l in links if l.relation == "support"]
            support_count = len(support_ids)
            contradict_count = sum(1 for l in links if l.relation == "contradict")
            formed_by = cog.formed_by  # 恒继承（取消就地升级）
            # 接线点 2/5 · reinforce(重算期):hedged 不落库、每次从链重派生。不接则用户再说一句
            #   相关的话触发 reinforce，封顶静默蒸发、从 280 反弹回 600+支持加分。
            confidence = compute_confidence(
                ConfidenceInputs(
                    content_type=cog.content_type, formed_by=formed_by, support_count=support_count,
                    contradict_count=contradict_count, hedged=resolve_hedged(formed_by, support_ids),
                ), cfg
            )
            cognition_store.update(
                cog.id, CognitionPatch(confidence=confidence, cred_status=derive_cred_status(confidence, contradict_count, cog.content_type, cfg, support_count))
            )
            reinforced += 1
            # 并存新 stated 认知:旧 confirmed + 本次新增(add)载体维派生成 stated → 并存一条(用 add 非 cited)。
            if cog.formed_by == "confirmed" and len(add) > 0 and resolve_formed_by(False, add) == "stated":
                # 接线点 3/5 · 并存新 stated(形成期)。**必须用 add 而非全链**:全链会混进旧的附和
                #   证据，让"没有 explicit"不成立，并存路的封顶永不触发。这是保守性倒置最刺眼的
                #   一条路径——用户点头认了 AI 猜测(旧 confirmed 280)后自己含糊复述，新认知拿满 600。
                up_conf = compute_confidence(
                    ConfidenceInputs(
                        content_type=cog.content_type, formed_by="stated", support_count=len(add),
                        contradict_count=0, hedged=resolve_hedged("stated", add),
                    ), cfg
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
            content, content_type, model_says_inferred, type_fallback = p
            support = pick_support(_cited_ids(c))
            if len(support) == 0:
                continue
            if type_fallback is not None:  # 同 new 路：correct 也会落一条新认知
                setattr(fallback, type_fallback, getattr(fallback, type_fallback) + 1)
            cognition_store.update(old.id, CognitionPatch(invalid_at=now_iso))  # 标失效、保留可溯源
            formed_by = resolve_formed_by(model_says_inferred, support)
            # 接线点 4/5 · correct(形成期，同 new 路)。不接则用户用一句含糊的话纠正旧认知时，
            #   新生认知拿 600，而被标失效的旧认知可能只有 280——"纠正"反而抬高了把握度。
            confidence = compute_confidence(
                ConfidenceInputs(
                    content_type=content_type, formed_by=formed_by, support_count=len(support), contradict_count=0,
                    hedged=resolve_hedged(formed_by, support),
                ), cfg
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
            # 模型显式点名的 conflict：把反证挂到该旧认知、按全链重算（复用 attach_contradiction，
            #   与 A5 护栏走同一段口径）。"只翻 cred_status→contradict_penalty 空转"的历史缺陷
            #   （TS 侧 #19 已修、Python 曾原样保留）与 hedged 重算期回表退化边界都收在 attach_contradiction 里。
            cog_id = resolve_cog_id(c.get("cognition_id"), "conflict")
            if cog_id is None:
                continue
            if attach_contradiction(cog_id, pick_support(_cited_ids(c))):
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
        return _Mutation(
            created=created, reinforced=reinforced, corrected=corrected, conflicted=conflicted, type_fallback=fallback
        )

    run_tx: Transaction = transaction if transaction is not None else noop_transaction
    mutation = cast(_Mutation, run_tx(mutate))

    return ConsolidateResult(
        created=mutation.created, reinforced=mutation.reinforced, corrected=mutation.corrected, conflicted=mutation.conflicted,
        processed_events=len(new_events), llm_calls=llm_calls, profile_size=len(existing), prompt_chars=prompt_chars,
        content_type_fallback=mutation.type_fallback,
    )
