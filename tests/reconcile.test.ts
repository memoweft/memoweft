/**
 * A5 第二道护栏 · 全画像矛盾扫描（reconcileContradictions）。
 *
 * 第一道护栏（consolidate guardHits）只查【本轮 new 候选】——跨轮积累 / topK 截断会漏，
 * 库里就并存两条极性相反的 active 认知、对"冲突可见"隐形（见 a5CoexistGuardrail.test.ts 复现）。
 * 第二道 reconcile 对【已入库全画像】同题聚簇 + 极性判 + 命中挂反证，兜底这些残留。
 * 与护栏共用 contradiction.ts 单一真源（同极性判 + 同 attachContradiction 落库口径）。全离线。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { reconcileContradictions } from '../src/consolidation/reconcile.ts';
import { createMemoWeftCore } from '../src/index.ts';

interface Stores {
  ev: SqliteEvidenceStore;
  cog: SqliteCognitionStore;
}
function fresh(): Stores {
  return { ev: new SqliteEvidenceStore(':memory:'), cog: new SqliteCognitionStore(':memory:') };
}
function closeAll(s: Stores) {
  s.ev.close();
  s.cog.close();
}

/** 造一条 spoken 证据，返回 id。 */
function seedEvidence(s: Stores, word: string, at: string): string {
  return s.ev.put({
    subjectId: 'u',
    sourceKind: 'spoken',
    hostId: 'h',
    rawContent: word,
    occurredAt: at,
  }).id;
}
/** 直接落一条 active 认知（带 support 证据），模拟"第一道漏掉、并存在库"的残留。
 *  用递增 createdAt（每次 +1s）+ 稳定自增 id：先落的 createdAt 更早 = 锚点，锚点由不可变量确定，
 *  不受重跑时 confidence 变化导致的 active 重排影响（见幂等重跑测试）。 */
let cogClock = Date.parse('2026-07-01T00:00:00.000Z');
let cogSeq = 0;
function putCognition(s: Stores, content: string, evidenceId: string) {
  const createdAt = new Date((cogClock += 1000)).toISOString();
  const id = `cog-${cogSeq++}`;
  s.cog.insert(
    {
      id,
      subjectId: 'u',
      content,
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
      scope: null,
      validAt: null,
      invalidAt: null,
      askedAt: null,
      archivedAt: null,
      mutedAt: null,
      createdAt,
      updatedAt: createdAt,
    },
    [{ evidenceId, relation: 'support' as const }],
  );
  return { id, content };
}

/** 只回极性判 {"contradicts":bool} 的脚本 LLM（reconcile 只调 judgeContradiction）。 */
function polarityLlm(contradicts: boolean) {
  return {
    callCount: 0,
    async chat() {
      this.callCount++;
      return `{"contradicts": ${contradicts}}`;
    },
  };
}
/** 玩具嵌入器：含"咖啡/coffee"→[1,0]，含"茶/tea"→[0,1]，其余→[0.5,0.5]。同主题余弦≈1、跨主题≈0。 */
const topicEmbedder = {
  async embed(texts: string[]): Promise<number[][]> {
    return texts.map((t) =>
      /咖啡|coffee/i.test(t) ? [1, 0] : /茶|tea/i.test(t) ? [0, 1] : [0.5, 0.5],
    );
  },
};

test('reconcile 命中：并存两条同主题极性相反 → 锚点(较早)挂反证标 conflicted、不删另一条', async () => {
  const s = fresh();
  try {
    const e1 = seedEvidence(s, '我超爱喝咖啡', '2026-07-01T10:00:00.000Z');
    const e2 = seedEvidence(s, '我把咖啡戒了', '2026-08-01T10:00:00.000Z');
    const a = putCognition(s, '用户爱喝咖啡', e1); // 先落 = 较早 = 锚点
    putCognition(s, '用户不再喝咖啡了', e2);

    const r = await reconcileContradictions('u', {
      cognitionStore: s.cog,
      llm: polarityLlm(true),
      embedder: topicEmbedder,
    });

    assert.equal(r.scanned, 2, '扫描两条');
    assert.equal(r.pairsJudged, 1, '同簇一对做一次极性判');
    assert.equal(r.conflictsAttached, 1, '命中挂一次反证');
    const active = s.cog.active('u');
    assert.equal(active.length, 2, '不删：两条都还在（冲突只暴露、不消解）');
    const anchor = active.find((c) => c.id === a.id)!;
    assert.equal(anchor.credStatus, 'conflicted', '锚点"爱喝咖啡"被挂反证 → conflicted');
    assert.ok(
      active.some((c) => /不再喝咖啡/.test(c.content)),
      '反转那条仍在库',
    );
  } finally {
    closeAll(s);
  }
});

test('reconcile 相似度门：不同主题(茶 vs 咖啡) → 进不了同簇、不判不动(不误伤)', async () => {
  const s = fresh();
  try {
    const e1 = seedEvidence(s, '我爱喝咖啡', '2026-07-01T10:00:00.000Z');
    const e2 = seedEvidence(s, '我爱喝茶', '2026-08-01T10:00:00.000Z');
    putCognition(s, '用户爱喝咖啡', e1);
    putCognition(s, '用户爱喝茶', e2);

    // 即使极性判会说"矛盾"，茶与咖啡余弦≈0、进不了同簇，极性判压根不被调用。
    const llm = polarityLlm(true);
    const r = await reconcileContradictions('u', {
      cognitionStore: s.cog,
      llm,
      embedder: topicEmbedder,
    });

    assert.equal(r.pairsJudged, 0, '跨主题不进簇 → 零极性判');
    assert.equal(r.conflictsAttached, 0, '不动');
    assert.equal(llm.callCount, 0, '极性判未被调用');
    assert.ok(
      s.cog.active('u').every((c) => c.credStatus !== 'conflicted'),
      '没有误挂冲突',
    );
  } finally {
    closeAll(s);
  }
});

test('reconcile 极性门：同主题但不矛盾(爱咖啡 + 手冲咖啡) → 判 false 不挂(不误伤)', async () => {
  const s = fresh();
  try {
    const e1 = seedEvidence(s, '我爱喝咖啡', '2026-07-01T10:00:00.000Z');
    const e2 = seedEvidence(s, '我喜欢手冲咖啡', '2026-08-01T10:00:00.000Z');
    putCognition(s, '用户爱喝咖啡', e1);
    putCognition(s, '用户喜欢手冲咖啡', e2);

    const r = await reconcileContradictions('u', {
      cognitionStore: s.cog,
      llm: polarityLlm(false), // 同簇但极性判 false
      embedder: topicEmbedder,
    });

    assert.equal(r.pairsJudged, 1, '同主题进簇、判一次');
    assert.equal(r.conflictsAttached, 0, '极性判 false → 不挂');
    assert.ok(
      s.cog.active('u').every((c) => c.credStatus !== 'conflicted'),
      '没有误挂冲突',
    );
  } finally {
    closeAll(s);
  }
});

test('facade：无 embedder → 告警 + 空返回(scanned 0)，不误动画像', async () => {
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: polarityLlm(true) }); // 不注入 embedder
  try {
    const r = await core.reconcileContradictions({ subjectId: 'u' });
    assert.equal(r.scanned, 0, '无 embedder → 空返回');
    assert.equal(r.conflictsAttached, 0);
  } finally {
    core.close();
  }
});

test('facade：有 embedder + 空画像 → 正常空返回、不崩', async () => {
  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: polarityLlm(true),
    embedder: topicEmbedder,
  });
  try {
    const r = await core.reconcileContradictions({ subjectId: 'u' });
    assert.equal(r.scanned, 0, '空画像 scanned 0');
  } finally {
    core.close();
  }
});

test('reconcile 幂等重跑：第二遍 no-op（P2#1 锚点不翻转 + P2#2 不虚增）', async () => {
  const s = fresh();
  try {
    const e1 = seedEvidence(s, '我超爱喝咖啡', '2026-07-01T10:00:00.000Z');
    const e2 = seedEvidence(s, '我把咖啡戒了', '2026-08-01T10:00:00.000Z');
    const a = putCognition(s, '用户爱喝咖啡', e1); // 先落 = createdAt 较早 = 锚点
    const b = putCognition(s, '用户不再喝咖啡了', e2);
    const deps = { cognitionStore: s.cog, llm: polarityLlm(true), embedder: topicEmbedder };

    const r1 = await reconcileContradictions('u', deps);
    assert.equal(r1.conflictsAttached, 1, '首遍命中挂一次');
    assert.equal(
      s.cog.active('u').find((c) => c.id === a.id)!.credStatus,
      'conflicted',
      '锚点 conflicted',
    );
    assert.notEqual(
      s.cog.active('u').find((c) => c.id === b.id)!.credStatus,
      'conflicted',
      '反证挂锚点、不挂较晚那条',
    );

    // 第二遍：首遍 attach 已把锚点 updatedAt 推新——用不可变 createdAt 才不翻转；反证已挂 → add 空 → no-op。
    const r2 = await reconcileContradictions('u', deps);
    assert.equal(
      r2.conflictsAttached,
      0,
      'P2#2：无新反证 → 不虚增、不 update（transient 不被无限续命）',
    );
    assert.equal(
      s.cog.active('u').find((c) => c.id === a.id)!.credStatus,
      'conflicted',
      '锚点仍 conflicted',
    );
    assert.notEqual(
      s.cog.active('u').find((c) => c.id === b.id)!.credStatus,
      'conflicted',
      'P2#1：锚点不翻转，较晚那条仍未被挂反证',
    );
  } finally {
    closeAll(s);
  }
});
