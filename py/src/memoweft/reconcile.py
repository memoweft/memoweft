"""A5 全画像矛盾扫描（reconcile · 第二道护栏），与 TypeScript consolidation/reconcile.ts 逐字对齐。

对已入库 active 画像做同题聚簇 + 极性判 + 命中挂反证，兜底第一道护栏（consolidate guard_hit，只查
本轮 new 候选）漏掉的跨轮 / topK 残留矛盾。与护栏共用 contradiction.py 单一真源（同极性判提示词 +
同 attach_contradiction 落库口径）。

【默认关】：宿主主动调 reconcile_contradiction（同 expire / aggregate_trends 直接暴露算子，与写路径解耦、
可幂等重跑）。Python 无 core facade 层——宿主自行组装 deps 并传入 embedder（无 embedder 判不了同题）。

设计：_workflow-docs/design/a5-full-fix-design-2026-07-26.md（owner 批）。
  gpt-4o 33 场景×3 轮验证（a5-full-validation-result-2026-07-26.md）：残留 35%→<1.7%、净误判 ~1.5%。
⚠ 当前为【全画像扫描】（连通分量簇内两两）。增量扫描（只扫 updated_at 新鲜变更簇，design §3.3）为后续成本优化。
⚠ 改本文件算法必同步 TS src/consolidation/reconcile.ts（parity 铁律）。
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from .clock import parse_iso_ms
from .config import CONFIG, Config, resolve_lang
from .contradiction import (
    GUARD_DEFAULT_SIMILARITY,
    AttachDeps,
    GuardEmbedder,
    attach_contradiction,
    cosine,
    judge_contradiction,
)
from .llm.client import LLMClient
from .store.cognition import SqliteCognitionStore
from .store.semantic_resolution import SqliteSemanticResolutionStore
from .types import Lang

_logger = logging.getLogger("memoweft.reconcile")


@dataclass(slots=True)
class ReconcileResult:
    """reconcile 结果（成本可观测），对齐 TS ReconcileResult。"""

    #: 扫描的 active 认知数。
    scanned: int
    #: 实际做的极性判次数（成本可观测）。
    pairs_judged: int
    #: 命中「相似且极性相反」、挂 contradict 到锚点的对数。
    conflicts_attached: int
    #: 本次 reconcile 的 llm 调用数（含极性判 JSON 修复重试）。
    llm_calls: int


def _cluster_by_cosine(vecs: Sequence[Sequence[float]], threshold: float) -> list[list[int]]:
    """连通分量聚簇（同题近邻图）：余弦 ≥ 阈值连边，返回 size≥2 的簇（active 下标）。
    与 reconcile.ts 的 clusterByCosine 逐字对齐（并查集 + 路径压缩 find + parent[find(i)]=find(j) 连边）。design §3.2。"""
    n = len(vecs)
    parent = list(range(n))

    def find(x: int) -> int:
        if parent[x] == x:
            return x
        parent[x] = find(parent[x])
        return parent[x]

    for i in range(n):
        for j in range(i + 1, n):
            if cosine(vecs[i], vecs[j]) >= threshold:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        r = find(i)
        groups.setdefault(r, []).append(i)
    return [g for g in groups.values() if len(g) >= 2]


def reconcile_contradictions(
    subject_id: str,
    *,
    cognition_store: SqliteCognitionStore,
    llm: LLMClient,
    embedder: GuardEmbedder,
    semantic_resolution_store: Optional[SqliteSemanticResolutionStore] = None,
    cfg: Config = CONFIG,
    lang: Optional[Lang] = None,
    min_similarity: Optional[float] = None,
) -> ReconcileResult:
    """对 subject 的 active 画像做一遍全画像矛盾扫描。命中即走 attach_contradiction 同口径
    （挂 contradict 到锚点、全链重算、derive_cred_status 判 conflicted/contested）——不新建相反行、不删、不裁决。
    无 embedder 判不了同题：调用方须保证接了 embedder（宿主决定是否调本入口，同护栏 opt-in）。镜像 reconcile.ts。"""
    active = cognition_store.active(subject_id)
    scanned = len(active)
    if scanned < 2:
        return ReconcileResult(scanned=scanned, pairs_judged=0, conflicts_attached=0, llm_calls=0)

    threshold = min_similarity if min_similarity is not None else GUARD_DEFAULT_SIMILARITY
    lg = lang if lang is not None else resolve_lang()
    before = llm.call_count

    # 嵌入失败不改数据、显式退化（同护栏：不静默改画像）。
    try:
        vecs = embedder.embed([c.content for c in active])
    except Exception as exc:  # noqa: BLE001 - 嵌入失败不该拖垮扫描：记日志、退化成本次不扫。
        _logger.warning("[memoweft/reconcile] 嵌入失败，跳过本次全画像扫描：%s", exc)
        return ReconcileResult(
            scanned=scanned, pairs_judged=0, conflicts_attached=0, llm_calls=llm.call_count - before
        )

    attach_deps = AttachDeps(
        cognition_store=cognition_store, config=cfg, semantic_resolution_store=semantic_resolution_store
    )

    pairs_judged = 0
    conflicts_attached = 0
    for cluster in _cluster_by_cosine(vecs, threshold):
        for a in range(len(cluster)):
            for b in range(a + 1, len(cluster)):
                i = cluster[a]
                j = cluster[b]
                pairs_judged += 1
                if not judge_contradiction(llm, lg, active[i].content, active[j].content):
                    continue
                # 锚点 = 较早入库（视作「旧立场」）；反证 = 较晚那条的 support 证据。
                #   ⚠ 用不可变的 (created_at, id) 全序，绝不用 updated_at：updated_at 会被 attach_contradiction /
                #   reinforce 推新，且 attach 改 confidence 会让 active 重排——两者都会在重跑后翻转锚点、破坏幂等（Codex P2#1）。
                #   created_at 相同（同轮多条认知可同毫秒）时用不可变 id 兜底 tie-break，使锚点与 active 顺序/confidence 无关。
                #   与护栏「旧认知当锚点、新反证挂上」同向；不新建相反行、不删（冲突只暴露、不消解）。
                ci, cj = active[i], active[j]
                ti, tj = parse_iso_ms(ci.created_at), parse_iso_ms(cj.created_at)
                if ti < tj or (ti == tj and ci.id < cj.id):
                    anchor, other = ci, cj
                else:
                    anchor, other = cj, ci
                contra_ids = [
                    s.evidence_id for s in cognition_store.sources_of(other.id) if s.relation == "support"
                ]
                if attach_contradiction(anchor.id, contra_ids, attach_deps):
                    conflicts_attached += 1

    return ReconcileResult(
        scanned=scanned,
        pairs_judged=pairs_judged,
        conflicts_attached=conflicts_attached,
        llm_calls=llm.call_count - before,
    )
