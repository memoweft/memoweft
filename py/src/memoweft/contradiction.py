"""矛盾判别与落库的【单一真源】，与 TypeScript consolidation/contradiction.ts 逐字对齐。

A5 护栏（consolidate 内 guard_hit）与全画像 reconcile（reconcile_contradictions）共用同一段
极性判 + 挂反证口径，杜绝两处漂移。从 consolidate.py 抽出（逐字保持行为）：consolidate 保留薄包装
委托到这里、绑定自己的 deps；reconcile 直接调用。改一处 = 两条路径同步变（避免 #19 / A5 口径分叉）。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Optional, Protocol

from .config import CONFIG, Config
from .confidence import compute_confidence, derive_cred_status, is_hedged_stated
from .llm.client import ChatMessage, LLMClient
from .llm.json_repair import parse_json_object_with_repair
from .store.cognition import SqliteCognitionStore
from .store.semantic_resolution import SqliteSemanticResolutionStore
from .types import (
    AssertionStrength,
    CognitionPatch,
    ConfidenceInputs,
    EvidenceLink,
    FormedBy,
    HedgeInput,
    Lang,
    PropositionOrigin,
)

# shortlist 余弦阈值缺省：0.5（护栏量具实测：真矛盾对余弦低至 0.571 且相似度分不开矛盾/兼容，
#   判别全靠极性判、其误判率 0%，故阈值只是"进极性判"的成本闸；0.6 会漏召回，0.5 召回回满且不增误判）。
#   对齐 contradiction.ts GUARD_DEFAULT_SIMILARITY。护栏与 reconcile 同用。
GUARD_DEFAULT_SIMILARITY = 0.5
# 每条候选最多做几次极性判（相似度降序前 N）：把 llm 调用量压到很小。
GUARD_DEFAULT_TOPK = 3


class GuardEmbedder(Protocol):
    """护栏/reconcile 相似度所需的最小嵌入器接口（HashEmbedder 及任何 embed(texts)->向量 的实现都满足）。"""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度；任一为零向量或维度不齐返回 0（与 TS cosine 同口径）。"""
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a)
    nb = sum(y * y for y in b)
    if na == 0 or nb == 0:
        return 0.0
    return float(dot / ((na**0.5) * (nb**0.5)))


def judge_contradiction(llm: LLMClient, lang: Lang, existing_content: str, candidate_content: str) -> bool:
    """极性判断（护栏第二半，非确定性）：关于【同一个人】的两条陈述，作为当前状态是否【冲突】（不能同时为真）。
    只判「相反」不判「相关」——相似度已把候选压到同主题。
    ⚠ 提示词经护栏量具调优：旧版"保守·拿不准判否"在真实啰嗦措辞上召回仅 ~32%；改成明确纳入立场/偏好/目标/事实反转、
      看穿辩解从句与演化语气后，召回 93.5%、误判 0%。与 contradiction.ts 的 judgeContradiction 【逐字对齐】，改一处必须同改。"""
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


class HedgeResolutionView(Protocol):
    """hedged 重算期读表所需的最小视图（is_hedged_stated 只看这两维）。
    consolidate 的 _ResEntry 与 store 的 SemanticResolution 都结构满足。对齐 TS HedgeResolutionView。"""

    proposition_origin: Optional[PropositionOrigin]
    assertion_strength: Optional[AssertionStrength]


@dataclass(frozen=True, slots=True)
class HedgeDeps:
    """resolve_hedged / attach_contradiction 的共享依赖：hedged 重算期查解析（库优先、内存兜底）。
    对齐 TS contradiction.ts 的 HedgeDeps。"""

    #: 语义解析 store（可选）：重算期查历史证据的解析（走 for_evidence_ids 批量）。
    semantic_resolution_store: Optional[SqliteSemanticResolutionStore] = None
    #: 本轮内存解析（consolidate 有；reconcile 事后扫描无 → 省略即 None）。
    resolution_of: Optional[Mapping[str, HedgeResolutionView]] = None


@dataclass(frozen=True, slots=True)
class AttachDeps:
    """attach_contradiction 的依赖：改认知库 + 配置 + hedged 重算期查解析。对齐 TS AttachDeps。"""

    cognition_store: SqliteCognitionStore
    config: Config = CONFIG
    semantic_resolution_store: Optional[SqliteSemanticResolutionStore] = None
    resolution_of: Optional[Mapping[str, HedgeResolutionView]] = None


def resolve_hedged(formed_by: FormedBy, support_ids: Sequence[str], deps: HedgeDeps) -> bool:
    """含糊自述（hedged）判定：stated + 有支持集时，按解析视图判是否含糊。与 consolidate.ts 的 resolveHedged 逐位一致。

    与载体维【正交】：载体维答"谁的话"，hedged 答"这话说得含不含糊"。封顶动作本身在 compute_confidence
    里（min(hedge_cap)），这里只出判据。**库优先、内存兜底**：落库循环对"库里已有解析"的证据跳过写入
    （幂等），所以【库里那份才是会活下来的那份】；若让内存赢，同一条认知会在 600/280 间永久分叉。
    semantic_resolution_store 未注入时只退化【历史证据】那一半（本轮证据仍准确），退化方向是
    hedged=False（不封顶），与 is_hedged_stated 的"解析不出不臆造惩罚"同向——宁可少封，不可错封。

    ⚠ **不变式**：support_ids 恒等于同一调用点传给 support_count 的那一个集合。
    """
    # 非 stated / 空支持集恒 False（is_hedged_stated 同判）——提前挡掉，省下重算期那次查表。
    if formed_by != "stated" or len(support_ids) == 0:
        return False
    stored: dict[str, HedgeInput] = {}
    if deps.semantic_resolution_store is not None:
        for sr in deps.semantic_resolution_store.for_evidence_ids(list(support_ids)):  # 一次批量查，无 N+1
            stored[sr.evidence_id] = HedgeInput(
                proposition_origin=sr.proposition_origin, assertion_strength=sr.assertion_strength
            )
    view: list[Optional[HedgeInput]] = []
    for id_ in support_ids:
        hit = stored.get(id_)
        if hit is None:
            r = deps.resolution_of.get(id_) if deps.resolution_of is not None else None  # 库里没有 → 回落本轮内存解析
            hit = (
                HedgeInput(proposition_origin=r.proposition_origin, assertion_strength=r.assertion_strength)
                if r is not None
                else None
            )
        view.append(hit)
    return is_hedged_stated(formed_by, view)


def attach_contradiction(cog_id: str, contra_ids: Sequence[str], deps: AttachDeps) -> bool:
    """把「反证原话」挂到旧认知上并按【全链】重算把握度（conflict 分支、A5 护栏、reconcile 共用同口径，
    避免 #19 的"只翻 cred_status 不重算 → contradict_penalty 空转"回归）。返回是否真挂上。
    formed_by 继承旧认知、不重派载体维（重算期约束）；cred_status 交 derive_cred_status 在
    contradict_count>0 下判 conflicted/contested（保住"冲突只暴露、不消解"不变量）。镜像 TS attachContradiction。"""
    cog = deps.cognition_store.get(cog_id)
    if cog is None or cog.invalid_at:
        return False
    if len(contra_ids) == 0:  # 没引到冲突原话 → 不凭空标冲突（证据完整性规则）
        return False
    already = {s.evidence_id for s in deps.cognition_store.sources_of(cog.id)}
    add = [i for i in contra_ids if i not in already]
    # 反证全已挂（add 空）→ 幂等 no-op：不重算、不刷 updated_at（否则定期 reconcile 每轮 update 会让
    #   transient 认知永不 expire）、不虚增 conflicts_attached（Codex P2#2）。同 reinforce 对重复证据的 no-op。
    if not add:
        return False
    deps.cognition_store.add_evidence(cog.id, [EvidenceLink(evidence_id=i, relation="contradict") for i in add])
    links = deps.cognition_store.sources_of(cog.id)
    support_ids = [lk.evidence_id for lk in links if lk.relation == "support"]
    support_count = len(support_ids)
    contradict_count = sum(1 for lk in links if lk.relation == "contradict")
    confidence = compute_confidence(
        ConfidenceInputs(
            content_type=cog.content_type,
            formed_by=cog.formed_by,
            support_count=support_count,
            contradict_count=contradict_count,
            hedged=resolve_hedged(
                cog.formed_by,
                support_ids,
                HedgeDeps(
                    semantic_resolution_store=deps.semantic_resolution_store, resolution_of=deps.resolution_of
                ),
            ),
        ),
        deps.config,
    )
    deps.cognition_store.update(
        cog.id,
        CognitionPatch(
            confidence=confidence,
            cred_status=derive_cred_status(confidence, contradict_count, cog.content_type, deps.config, support_count),
        ),
    )
    return True
