/**
 * recall（共享召回：超取再截断）的【规模行为】离线夹具。
 *
 * 背景（§15.3 eval 规模盲区）：召回「超取再截断」用固定倍数 overfetchFactor 取
 *   poolSize = topK×overfetchFactor 条候选、逐条过门、凑够 topK 即停。它修的是小规模欠填
 *   （前 K 名被门挡不补位），但倍数固定、非自适应：规模大且【前 poolSize 名里被门挡的条数过多】时，
 *   候选池会被过滤穷尽、凑不满 topK，排在 poolSize 之外的合格认知取不到——规模下欠填依旧发生。
 *   现有 eval 语料单 subject 认知≤2、poolSize 永远取尽全库，这条边界零触发。
 *
 * 本套用例全离线（stub Retriever 直接给定名次表，不调嵌入器/模型），把 overfetch 的
 *   【缓解范围】与【失效边界】各钉一条：
 *     - RC01：门挡数 ≥ 池容量 → 池内合格者被挤空 → 召回欠填（返空），尽管库里有 topK 条合格但在池外。
 *     - RC02（对照）：门挡数 < overfetch 余量 → 超取成功补位、凑满 topK——证明缓解在其设计范围内有效，
 *       不是把 overfetch 一竿子打成没用（诚实分级：报边界、也认它管用的那段）。
 *
 * 断言语义、读 config 当前值（topK/overfetchFactor/minEffectiveConfidence），不写死魔数。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteCognitionStore } from '../../src/cognition/store.ts';
import { recallCognitions } from '../../src/retrieval/recall.ts';
import { config } from '../../src/config.ts';
import type { Retriever, RetrievalHit } from '../../src/retrieval/retriever.ts';

const NOW = new Date(Date.UTC(2026, 0, 15));

/** stub Retriever：给定一张按分降序的名次表，search 取前 topK（=recall 传入的 poolSize）。indexAll no-op。 */
function stubRetriever(ranked: RetrievalHit[]): Retriever {
  return {
    async indexAll() {
      /* 召回夹具不索引 */
    },
    async search(_query: string, topK: number): Promise<RetrievalHit[]> {
      return ranked.slice(0, topK);
    },
  };
}

/** put 一条 preference 认知（不衰减、eff=confidence）；muted=true 则随后 mute（召回门挡）。返回 id。 */
function putCog(store: SqliteCognitionStore, tag: string, muted: boolean): string {
  const c = store.put({
    subjectId: 'owner',
    content: `偏好 ${tag}`,
    contentType: 'preference', // 不在 halfLifeDays → 不衰减，eff=confidence=600 ≥ minEffectiveConfidence
    formedBy: 'stated',
    confidence: 600,
    credStatus: 'stable',
  });
  if (muted) store.update(c.id, { mutedAt: NOW.toISOString() });
  return c.id;
}

test('RC01 门挡数 ≥ 池容量 → 召回欠填返空（池外合格认知取不到）', async () => {
  const { topK, overfetchFactor } = config.retrieval;
  const poolSize = topK * overfetchFactor; // 默认 5×4=20
  const store = new SqliteCognitionStore(':memory:');
  try {
    // 前 poolSize 名全 muted（被门挡），额外 topK 条合格但排在池外。
    const muted = Array.from({ length: poolSize }, (_, i) => putCog(store, `muted-${i}`, true));
    const good = Array.from({ length: topK }, (_, i) => putCog(store, `good-${i}`, false));
    // 名次表：muted 在前（高分占满池），good 在后（池外）。
    const ranked: RetrievalHit[] = [
      ...muted.map((id, i) => ({ id, score: 1 - i * 0.001 })),
      ...good.map((id, i) => ({ id, score: 0.1 - i * 0.001 })),
    ];
    const out = await recallCognitions(
      'q',
      'owner',
      { retriever: stubRetriever(ranked), cognitionStore: store },
      config,
      NOW,
    );
    assert.equal(out.length, 0, '池内 poolSize 名全被门挡 → 凑不出一条，欠填返空');
    // 反证「库里确实有 topK 条合格认知」——只是排在池外、固定倍数 overfetch 够不到。
    const qualifying = good.filter((id) => {
      const c = store.get(id)!;
      return !c.invalidAt && !c.archivedAt && !c.mutedAt;
    });
    assert.equal(qualifying.length, topK, `库里有 ${topK} 条合格认知，因排在 poolSize 之外取不到`);
  } finally {
    store.close();
  }
});

test('RC02 门挡数 < overfetch 余量 → 超取成功补位、凑满 topK（缓解在设计范围内有效）', async () => {
  const { topK, overfetchFactor } = config.retrieval;
  const poolSize = topK * overfetchFactor;
  const gatedCount = topK; // 远小于余量 poolSize-topK（默认 15）
  const store = new SqliteCognitionStore(':memory:');
  try {
    const muted = Array.from({ length: gatedCount }, (_, i) => putCog(store, `m-${i}`, true));
    const good = Array.from({ length: topK }, (_, i) => putCog(store, `g-${i}`, false));
    // muted 在前占名次，但池容量（poolSize）足以把后面的 good 一并纳入 → 逐条过门后补满 topK。
    const ranked: RetrievalHit[] = [
      ...muted.map((id, i) => ({ id, score: 1 - i * 0.001 })),
      ...good.map((id, i) => ({ id, score: 0.5 - i * 0.001 })),
    ];
    assert.ok(gatedCount + good.length <= poolSize, '前提：门挡+合格都落在同一个候选池内');
    const out = await recallCognitions(
      'q',
      'owner',
      { retriever: stubRetriever(ranked), cognitionStore: store },
      config,
      NOW,
    );
    assert.equal(out.length, topK, '超取把被门挡的名额补位、凑满 topK（overfetch 在其余量内有效）');
    assert.ok(
      out.every((r) => r.content.startsWith('偏好 g-')),
      '返回的全是合格认知，muted 的一条不漏进来',
    );
  } finally {
    store.close();
  }
});
