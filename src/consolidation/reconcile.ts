/**
 * A5 全画像矛盾扫描（reconcile · 第二道护栏）：对已入库 active 画像做同题聚簇 + 极性判 + 命中挂反证，
 *   兜底第一道护栏（consolidate guardHits，只查本轮 new 候选）漏掉的跨轮 / topK 残留矛盾。
 *   与护栏共用 contradiction.ts 单一真源（同极性判提示词 + 同 attachContradiction 落库口径）。
 *
 * 设计：_workflow-docs/design/a5-full-fix-design-2026-07-26.md（owner 批）。
 *   gpt-4o 33 场景×3 轮验证（a5-full-validation-result-2026-07-26.md）：残留 35%→<1.7%、净误判 ~1.5%。
 * 【默认关】：宿主经 core.reconcileContradictions 主动调（同 expire/aggregateTrends，与写路径解耦、可幂等重跑）。
 *
 * ⚠ 当前为【全画像扫描】（连通分量簇内两两）。增量扫描（只扫 updatedAt 新鲜的变更簇，design §3.3）为后续成本优化。
 * ⚠ 改本文件算法必同步 Python py/src/memoweft/reconcile.py（parity 铁律）。
 */
import type { CognitionStore } from '../cognition/store.ts';
import type { LLMClient } from '../llm/client.ts';
import type { Embedder } from '../retrieval/embedder.ts';
import type { SemanticResolutionStore } from '../interaction/semanticResolutionStore.ts';
import { resolveLang, type MemoWeftConfig } from '../config.ts';
import {
  GUARD_DEFAULT_SIMILARITY,
  cosine,
  judgeContradiction,
  attachContradiction,
} from './contradiction.ts';

export interface ReconcileDeps {
  cognitionStore: CognitionStore;
  /** 极性判模型（写路径小快模型即可）。 */
  llm: LLMClient;
  /** 嵌入器：同题聚簇的成本闸（reconcile 靠相似度筛同主题，无 embedder 判不了）。 */
  embedder: Embedder;
  /** 语义解析 store（可选）：命中重算 hedged 时读历史证据的解析（同 consolidate 全链重算）。 */
  semanticResolutionStore?: SemanticResolutionStore;
  config?: MemoWeftConfig;
  /** 同题簇余弦阈值；缺省 GUARD_DEFAULT_SIMILARITY（与护栏同，只作成本闸、非判据）。 */
  minSimilarity?: number;
}

export interface ReconcileResult {
  /** 扫描的 active 认知数。 */
  scanned: number;
  /** 实际做的极性判次数（成本可观测）。 */
  pairsJudged: number;
  /** 命中「相似且极性相反」、挂 contradict 到锚点的对数。 */
  conflictsAttached: number;
  /** 本次 reconcile 的 llm 调用数（含极性判 JSON 修复重试）。 */
  llmCalls: number;
}

/**
 * 连通分量聚簇（同题近邻图）：余弦 ≥ 阈值连边，返回 size≥2 的簇（active 下标）。design §3.2。
 * 纯确定性——export 供 gen-shared-assets 产出 parity/reconcile.json 夹具（Python `_cluster_by_cosine` 逐位比对）；
 *   不进 index.ts、不上 api-surface。极性判是 LLM、不进夹具（同护栏）。
 */
export function clusterByCosine(
  vecs: readonly (readonly number[])[],
  threshold: number,
): number[][] {
  const n = vecs.length;
  const parent = Array.from({ length: n }, (_, i) => i);
  const find = (x: number): number => (parent[x] === x ? x : (parent[x] = find(parent[x]!)));
  for (let i = 0; i < n; i++)
    for (let j = i + 1; j < n; j++)
      if (cosine(vecs[i]!, vecs[j]!) >= threshold) parent[find(i)] = find(j);
  const groups = new Map<number, number[]>();
  for (let i = 0; i < n; i++) {
    const r = find(i);
    if (!groups.has(r)) groups.set(r, []);
    groups.get(r)!.push(i);
  }
  return [...groups.values()].filter((g) => g.length >= 2);
}

/**
 * 对 subject 的 active 画像做一遍全画像矛盾扫描。命中即走 attachContradiction 同口径
 *   （挂 contradict 到锚点、全链重算、deriveCredStatus 判 conflicted/contested）——不新建相反行、不删、不裁决。
 * 无 embedder 判不了同题：调用方应保证接了 embedder（facade 层无 embedder 时不暴露本入口 + 告警）。
 */
export async function reconcileContradictions(
  subjectId: string,
  deps: ReconcileDeps,
): Promise<ReconcileResult> {
  const active = deps.cognitionStore.active(subjectId);
  const scanned = active.length;
  if (scanned < 2) return { scanned, pairsJudged: 0, conflictsAttached: 0, llmCalls: 0 };

  const threshold = deps.minSimilarity ?? GUARD_DEFAULT_SIMILARITY;
  const lang = resolveLang(deps.config);
  const before = deps.llm.callCount;

  // 嵌入失败不改数据、显式退化（同护栏：不静默改画像）。
  let vecs: number[][];
  try {
    vecs = await deps.embedder.embed(active.map((c) => c.content));
  } catch (e) {
    console.warn(
      `[memoweft/reconcile] 嵌入失败，跳过本次全画像扫描：${e instanceof Error ? e.message : String(e)}`,
    );
    return { scanned, pairsJudged: 0, conflictsAttached: 0, llmCalls: deps.llm.callCount - before };
  }

  const attachDeps = {
    cognitionStore: deps.cognitionStore,
    config: deps.config,
    semanticResolutionStore: deps.semanticResolutionStore,
  };

  let pairsJudged = 0;
  let conflictsAttached = 0;
  for (const cluster of clusterByCosine(vecs, threshold)) {
    for (let a = 0; a < cluster.length; a++) {
      for (let b = a + 1; b < cluster.length; b++) {
        const i = cluster[a]!;
        const j = cluster[b]!;
        pairsJudged++;
        if (!(await judgeContradiction(deps.llm, lang, active[i]!.content, active[j]!.content)))
          continue;
        // 锚点 = 较早入库（updatedAt 早，视作「旧立场」）；反证 = 较晚那条的 support 证据。
        //   与护栏「旧认知当锚点、新反证挂上」同向；不新建相反行、不删（冲突只暴露、不消解）。
        const [anchor, other] =
          new Date(active[i]!.updatedAt).getTime() <= new Date(active[j]!.updatedAt).getTime()
            ? [active[i]!, active[j]!]
            : [active[j]!, active[i]!];
        const contraIds = deps.cognitionStore
          .sourcesOf(other.id)
          .filter((s) => s.relation === 'support')
          .map((s) => s.evidenceId);
        if (attachContradiction(anchor.id, contraIds, attachDeps)) conflictsAttached++;
      }
    }
  }

  return { scanned, pairsJudged, conflictsAttached, llmCalls: deps.llm.callCount - before };
}
