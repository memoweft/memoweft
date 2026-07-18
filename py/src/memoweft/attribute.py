"""M4 归因 / 可解释假设 —— 移植自 src/attribution/attribute.ts。

从【现象】(一条 state 认知)出发,拉时间窗内证据,让 LLM 推"为什么"→ 可解释假设(低置信封顶、挂证据、可推翻)。
纪律:假设只当假设(formed_by=inferred + hypothesis_cap 封顶);禁 state→state(一个抱怨不解释另一个抱怨);
  ④治脑补(现象要攒够 min_phenomenon_support 条支撑才归因);隐私门 tier + inference。同步(见 D-0043)。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from ._jsstr import js_trim
from .clock import Clock, ms_to_iso, parse_iso_ms, system_clock, to_iso_z
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
class AttributedHypothesis:
    cognition: Cognition
    phenomenon: str
    based_on_evidence_ids: list[str]


@dataclass(slots=True)
class AttributeResult:
    hypotheses: list[AttributedHypothesis]
    considered_phenomena: int
    llm_calls: int


def _minus_hours(iso: str, hours: int) -> str:
    """减 hours 小时 → ISO。对齐 attribute.ts:91-93。"""
    return ms_to_iso(parse_iso_ms(iso) - hours * 3_600_000)


def _build_messages(
    phenomenon: str, evidences: Sequence[tuple[str, str, str, str]], lang: Lang
) -> tuple[list[ChatMessage], dict[str, str]]:
    """发短标号 [e1](D-0036),tag_to_id 落库前翻回真 id。对齐 attribute.ts:61-88。"""
    tag_to_id: dict[str, str] = {}
    parts: list[str] = []
    for i, (eid, source_kind, occurred_at, text) in enumerate(evidences):
        tag = f"e{i + 1}"
        tag_to_id[tag] = eid
        parts.append(f"- [{tag}] ({source_kind} {occurred_at[:16]}) {text}")
    listing = "\n".join(parts)
    if lang == "zh":
        body = f"【现象】：{phenomenon}\n\n【可能相关的行为/观察证据（只能从这里选原因）】：\n{listing}"
    else:
        body = (
            f"[Phenomenon]: {phenomenon}\n\n"
            f"[Possibly relevant behavior/observation evidence (causes may only be chosen from here)]:\n{listing}"
        )
    messages = [
        ChatMessage(role="system", content=prompt_text("attribute", lang)),
        ChatMessage(role="user", content=body),
    ]
    return messages, tag_to_id


def attribute(
    subject_id: str,
    *,
    evidence_store: SqliteEvidenceStore,
    cognition_store: SqliteCognitionStore,
    llm: LLMClient,
    cfg: Config = CONFIG,
    clock: Clock = system_clock,
    lang: Optional[Lang] = None,
) -> AttributeResult:
    """对未归因的 state 现象做一次归因。对齐 attribute.ts:99-213。"""
    lg = lang if lang is not None else resolve_lang()
    a = cfg.attribution
    active = cognition_store.active(subject_id)
    states = [c for c in active if c.content_type == "state"]
    hypos = [c for c in active if c.content_type == "hypothesis"]

    def support_of(cog_id: str) -> list[str]:
        return [lk.evidence_id for lk in cognition_store.sources_of(cog_id) if lk.relation == "support"]

    # state 现象自身的证据只能当现象 side,不能当原因(禁 state→state)。
    state_evidence: set[str] = set()
    for s in states:
        state_evidence.update(support_of(s.id))
    # 已有假设引用过的证据 → 判某现象是否已归因(按现象去重)。
    hypo_ref_evidence: set[str] = set()
    for h in hypos:
        hypo_ref_evidence.update(support_of(h.id))

    def is_attributed(phenom_id: str) -> bool:
        return any(i in hypo_ref_evidence for i in support_of(phenom_id))

    # ④治脑补:现象要攒够 ≥min_phenomenon_support 条支撑;按 updated_at 降序取最近 max_phenomena_per_run 个。
    pool = [c for c in states if not is_attributed(c.id) and len(support_of(c.id)) >= a.min_phenomenon_support]
    phenomena = sorted(pool, key=lambda c: c.updated_at, reverse=True)[: a.max_phenomena_per_run]

    before = llm.call_count
    hypotheses: list[AttributedHypothesis] = []
    considered = 0
    upper_bound = to_iso_z(clock())  # 窗口上界"此刻",吸收录入时差

    for phenom in phenomena:
        phenom_evidences = sorted(
            [e for e in (evidence_store.get(i) for i in support_of(phenom.id)) if e is not None],
            key=lambda e: e.occurred_at,
            reverse=True,  # 最晚在前
        )
        anchor_evidence = phenom_evidences[0] if phenom_evidences else None
        anchor = anchor_evidence.occurred_at if anchor_evidence is not None else phenom.created_at

        # 候选原因:[anchor-window_hours, 此刻] 内、可推断、且不支撑任何 state 现象;再过 tier 读门。
        causes = filter_readable_by_tier(
            [
                e
                for e in evidence_store.by_time_range(_minus_hours(anchor, a.window_hours), upper_bound)
                if e.allow_inference and e.id not in state_evidence
            ],
            llm.tier if llm.tier is not None else "cloud",
        )
        if len(causes) == 0:
            continue  # 没有行为/观察类原因 → 不硬编
        considered += 1
        candidates = [(e.id, e.source_kind, e.occurred_at, e.summary or e.raw_content) for e in causes]
        candidate_ids = {c[0] for c in candidates}
        messages, tag_to_id = _build_messages(phenom.content, candidates, lg)
        out: dict[str, Any] = parse_json_object_with_repair(llm, messages, lang=lg) or {}
        for raw in out.get("hypotheses") or []:
            rc = raw.get("content")
            if rc is None:
                rc = raw.get("hypothesis")
            content = js_trim(rc) if isinstance(rc, str) else ""
            if not content:
                continue
            based_raw = raw.get("based_on_evidence_ids") or []
            cited = list(
                dict.fromkeys(
                    r for r in (resolve_echoed_id(i, candidate_ids, tag_to_id) for i in based_raw) if r is not None
                )
            )[: a.max_causes_per_hypothesis]
            if len(cited) == 0:
                continue  # 没引到真实原因 → 不硬编
            # 支撑 = ≤N 条原因 + 1 个现象锚点(让"已归因"判定有据)。
            anchor_ids = [anchor_evidence.id] if anchor_evidence is not None else []
            based_on = list(dict.fromkeys([*cited, *anchor_ids]))
            raw_conf = compute_confidence(
                ConfidenceInputs(content_type="hypothesis", formed_by="inferred", support_count=len(based_on), contradict_count=0), cfg
            )
            confidence = min(raw_conf, a.hypothesis_cap)  # 假设级封顶:低声说
            cognition = cognition_store.put(
                CognitionInput(
                    subject_id=subject_id, content=content, content_type="hypothesis", formed_by="inferred",
                    confidence=confidence, cred_status=derive_cred_status(confidence, 0, "hypothesis", cfg),
                    evidence=[EvidenceLink(evidence_id=i, relation="support") for i in based_on],
                )
            )
            hypotheses.append(
                AttributedHypothesis(cognition=cognition, phenomenon=phenom.content, based_on_evidence_ids=based_on)
            )

    return AttributeResult(hypotheses=hypotheses, considered_phenomena=considered, llm_calls=llm.call_count - before)
