/**
 * reconcile（A5 第二道全画像护栏）的【规模行为】离线夹具。
 *
 * 背景（§15.3 eval 规模盲区）：现有 eval 语料全是 1~3 条证据 / 2~4 条认知的小场景，
 *   单一 subject 下最多 2 条认知共存——reconcile 的「全画像同题聚簇 + 簇内两两极性判」
 *   这条路径在小场景里永远 size≤1、零触发。规模触发型行为（连通分量传递性、O(N²) 极性判、
 *   反证锚点堆积）只有 N≥5 才显形，此前只能靠 dogfood(gpt-4o) 兜底、无确定性回归钉子。
 *
 * 本套用例把这些规模行为钉成【全离线、确定性】的回归基线（stub embedder 给可控余弦矩阵、
 *   stub LLM 给固定极性、注入递增时钟固定锚点顺序）——不调任何真模型、不烧钱、不改核心算法，
 *   只断言【结构不变量】：
 *     - RS01/02：clusterByCosine 的连通分量语义——余弦阈值连边【具传递性】，会把首尾本不相似的
 *       条目并进同一巨簇（传递链），松散相关内容因此被强行送极性判。
 *     - RS03：簇内极性判次数 = C(size,2)——量化 O(N²) 成本二次爆炸（30 条同题≈435 次/轮）。
 *     - RS04：反证【恒挂 (createdAt,id) 最早那条锚点】——大簇下最老认知吸收全簇反证、呈递减阶梯。
 *     - RS05：最老锚点被规模化反证经 contradictPenalty（无 count 封顶、仅 minConfidence 触底）
 *       打到 minConfidence 且判 conflicted，而较新的那条毫发无伤——量化「锚点选择在规模下的正确性副作用」。
 *
 * 这些断言既是防 reconcile 行为漂移的回归钉子，也把过去只在记忆里口传的规模隐患
 *   （传递性巨簇 / 反证堆积）变成可执行、可复核的事实，供 owner 判断是否值得改算法。
 *   断言语义、不锚魔数：数值型断言（如触底=minConfidence）读 config 当前值，不写死。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteCognitionStore } from '../../src/cognition/store.ts';
import { reconcileContradictions, clusterByCosine } from '../../src/consolidation/reconcile.ts';
import { cosine, GUARD_DEFAULT_SIMILARITY } from '../../src/consolidation/contradiction.ts';
import { config } from '../../src/config.ts';
import type { Clock } from '../../src/clock.ts';
import type { Embedder } from '../../src/retrieval/embedder.ts';
import type { LLMClient } from '../../src/llm/client.ts';

/** 递增时钟：每次调用 +stepMs，保证每条认知 createdAt 严格递增（锚点=最早那条，绕开 randomUUID 的 id tie-break 不确定性）。 */
function steppingClock(startMs = Date.UTC(2026, 0, 1), stepMs = 1000): Clock {
  let t = startMs;
  return () => {
    const d = new Date(t);
    t += stepMs;
    return d;
  };
}

/** stub embedder：按 content 文本返回预置向量（余弦矩阵完全可控）；给不出向量即抛（防夹具悄悄退化）。 */
function stubEmbedder(byText: ReadonlyMap<string, readonly number[]>): Embedder {
  return {
    async embed(texts: string[]): Promise<number[][]> {
      return texts.map((t) => {
        const v = byText.get(t);
        if (!v) throw new Error(`stubEmbedder: 无预置向量 for ${JSON.stringify(t)}`);
        return [...v];
      });
    },
  };
}

/** stub 极性判 LLM：恒定裁决 + callCount 自增（reconcile 用前后差算 llmCalls）。返回合法 JSON 一次解析成功、不触发重试。 */
function stubJudge(verdict: boolean): LLMClient {
  let calls = 0;
  return {
    get callCount() {
      return calls;
    },
    async chat(): Promise<string> {
      calls++;
      return `{"contradicts": ${verdict}}`;
    },
  };
}

/** 2D 单位向量（deg 角）：便于用夹角精确控制余弦（cos(夹角)）。 */
function unit(deg: number): number[] {
  const r = (deg * Math.PI) / 180;
  return [Math.cos(r), Math.sin(r)];
}

/**
 * 造 n 条同题 preference 认知的紧簇：向量按 5° 步进（最大夹角 (n-1)×5°，n≤12 时两两余弦≥cos(55°)>0.5
 *   → 全部两两连边 → 一个 size=n 连通分量）。createdAt 严格递增（锚点=第 0 条，确定）。
 *   每条带 1 条唯一 support 证据（attachContradiction 重算读它；evidenceId 只需唯一字符串、无须建 evidence 表）。
 */
function buildTightCluster(n: number): {
  store: SqliteCognitionStore;
  byText: Map<string, number[]>;
  cogs: ReturnType<SqliteCognitionStore['put']>[];
} {
  const store = new SqliteCognitionStore(':memory:', steppingClock());
  const byText = new Map<string, number[]>();
  const cogs: ReturnType<SqliteCognitionStore['put']>[] = [];
  for (let i = 0; i < n; i++) {
    const content = `饮食偏好条目 ${i}`;
    byText.set(content, unit(i * 5));
    cogs.push(
      store.put({
        subjectId: 'owner',
        content,
        contentType: 'preference',
        formedBy: 'stated',
        confidence: 600,
        credStatus: 'limited',
        evidence: [{ evidenceId: `ev-${i}`, relation: 'support' }],
      }),
    );
  }
  return { store, byText, cogs };
}

// ============================================================================
// RS01/02 · clusterByCosine 连通分量语义（纯函数，不涉及 store / LLM）
// ============================================================================

test('RS01 传递性巨簇：45°链相邻相似→全链并成一个簇，首尾余弦为负却同簇', () => {
  // 相邻夹角 45°（cos≈0.707≥阈值）连边；隔项 90°（cos≈0）、首尾 180°（cos=-1）本不连——
  // 但并查集的传递性把整条链并成一个连通分量。规模下同一话题的近义句会这样连成巨簇。
  const chain = [unit(0), unit(45), unit(90), unit(135), unit(180)];
  const clusters = clusterByCosine(chain, GUARD_DEFAULT_SIMILARITY);
  assert.equal(clusters.length, 1, '整条相似链并成一个连通分量');
  assert.deepEqual(
    [...clusters[0]!].sort((a, b) => a - b),
    [0, 1, 2, 3, 4],
    '全部 5 条进同一簇',
  );
  // 缺陷点白纸黑字：首尾余弦低于阈值（本不该判它俩），仅因相邻传递被并簇 → 仍会被强行送两两极性判。
  assert.ok(
    cosine(chain[0]!, chain[4]!) < GUARD_DEFAULT_SIMILARITY,
    '首尾余弦 < 阈值：证明传递性把不相似条目卷进同簇',
  );
});

test('RS02 独立话题各自成簇、孤立点不入 size≥2 簇', () => {
  // A（0/8/16°）内部两两 cos≥cos(16°)>0.5 成簇；B（90/98°）成簇；两话题最近夹角 74°(cos≈0.28)<0.5 不连；
  // 孤立点 180° 与 A/B 都 <0.5。验证聚簇不会把无关话题错并（传递性只在真·相似链上发作）。
  const vecs = [unit(0), unit(8), unit(16), unit(90), unit(98), unit(180)];
  const clusters = clusterByCosine(vecs, GUARD_DEFAULT_SIMILARITY);
  const sizes = clusters.map((c) => c.length).sort((a, b) => a - b);
  assert.deepEqual(sizes, [2, 3], '话题A 3 条 + 话题B 2 条，两个独立簇');
  const covered = new Set(clusters.flat());
  assert.ok(!covered.has(5), '孤立点（下标 5）不进任何 size≥2 簇');
});

// ============================================================================
// RS03 · 簇内极性判次数 = C(N,2)：量化 O(N²) 成本二次爆炸
// ============================================================================

test('RS03 N=6 同题簇 → 簇内两两极性判 15 次（C(6,2)），LLM 调用等量', async () => {
  const { store, byText } = buildTightCluster(6);
  try {
    const r = await reconcileContradictions('owner', {
      cognitionStore: store,
      llm: stubJudge(true),
      embedder: stubEmbedder(byText),
    });
    assert.equal(r.scanned, 6, '扫描 6 条 active');
    assert.equal(
      r.pairsJudged,
      15,
      'C(6,2)=15：极性判次数随簇 size 二次增长（30 条同题≈435 次/轮）',
    );
    assert.equal(r.llmCalls, 15, '每对一次 LLM 调用（JSON 一次解析成功、无重试）');
  } finally {
    store.close();
  }
});

// ============================================================================
// RS04/05 · 反证恒挂最早锚点：大簇下最老认知吸收全簇反证并被压到触底
// ============================================================================

test('RS04 反证恒堆最老锚点：contradictCount 按入库顺序呈递减阶梯', async () => {
  const { store, cogs, byText } = buildTightCluster(6);
  try {
    await reconcileContradictions('owner', {
      cognitionStore: store,
      llm: stubJudge(true),
      embedder: stubEmbedder(byText),
    });
    const contradictOf = (id: string) =>
      store.sourcesOf(id).filter((s) => s.relation === 'contradict').length;
    const counts = cogs.map((c) => contradictOf(c.id));
    // 每对 (i,j) i<j 都把较晚那条的反证挂到较早那条 → c0 作锚点 5 次、c5 从不作锚点 0 次。
    assert.deepEqual(
      counts,
      [5, 4, 3, 2, 1, 0],
      '越早入库吸收越多反证：最老那条堆 n-1 条、最新那条 0 条',
    );
  } finally {
    store.close();
  }
});

test('RS05 最老锚点被规模化反证打到 minConfidence 且判 conflicted，较新的毫发无伤', async () => {
  const { store, cogs, byText } = buildTightCluster(6);
  try {
    await reconcileContradictions('owner', {
      cognitionStore: store,
      llm: stubJudge(true),
      embedder: stubEmbedder(byText),
    });
    const conf = cogs.map((c) => store.get(c.id)!.confidence);
    // c0：base 600 + 支持 0 - 5×contradictPenalty(120)=−600 → clamp 到 minConfidence（penalty 无 count 封顶、仅触底兜住）。
    assert.equal(
      conf[0],
      config.consolidation.minConfidence,
      '最老锚点被 5 条反证压到触底（contradictPenalty 无封顶）',
    );
    assert.equal(store.get(cogs[0]!.id)!.credStatus, 'conflicted', '反证占优 → 判 conflicted');
    // c5：从不作锚点、无反证 → confidence 保持初始 600、状态不变。
    assert.equal(conf[5], 600, '最新那条从不作锚点、confidence 不受规模化反证影响');
    // 单调：越早入库置信越低（反证阶梯的直接后果）——规模下老认知被系统性压没、新认知逃逸。
    for (let i = 1; i < conf.length; i++) {
      assert.ok(conf[i]! >= conf[i - 1]!, `入库越晚置信越高（i=${i}）`);
    }
  } finally {
    store.close();
  }
});
