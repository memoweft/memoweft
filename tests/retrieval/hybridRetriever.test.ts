/**
 * HybridRetriever 测试（Phase 1 · §14.4：RRF 融合多通道）。先红后绿、零网络、零系统时间。
 *
 * 覆盖：
 *  - RRF 数学：mock 排名表手算 1/(rrfK+rank) 之和，双通道命中 > 单通道 rank1（含平票稳定序）。
 *  - rank 定序：用通道返回顺序，不按 score 重排。
 *  - 互补召回：真实 VectorRetriever(HashEmbedder) + KeywordRetriever，query 让向量命中 A、关键词命中 B，
 *    单臂各漏一个，hybrid 的 topK 同时含 A、B。
 *  - 配置：kCandidate 截断候选、rrfK 影响 RRF 分。
 *  - 扇出：indexAll 后两通道各自可召回；移除项后 hybrid 不再返回。
 *  - 边界：空 query 透传各通道得 [] → hybrid []；所有通道空 → []；channels=[] → []；close() 不抛。
 *  - 确定性：同语料 + query 两次逐位相同。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { HybridRetriever } from '../../src/retrieval/hybridRetriever.ts';
import { VectorRetriever } from '../../src/retrieval/vectorRetriever.ts';
import { KeywordRetriever } from '../../src/retrieval/keywordRetriever.ts';
import { NullRetriever } from '../../src/retrieval/nullRetriever.ts';
import { HashEmbedder } from './hashEmbedder.ts';
import type { Retriever, RetrievalHit } from '../../src/retrieval/retriever.ts';

/** 直接吐预设排名表的 mock 通道（不索引）：search 返回 table 的前 topK 个，用于精确验证 RRF 数学。 */
class MockRetriever implements Retriever {
  private readonly table: RetrievalHit[];
  constructor(table: RetrievalHit[]) {
    this.table = table;
  }
  async indexAll(_items: Array<{ id: string; text: string }>): Promise<void> {
    /* mock 不索引 */
  }
  async search(_query: string, topK: number): Promise<RetrievalHit[]> {
    return this.table.slice(0, topK);
  }
}

/** 记录收到的 query 并恒返回 [] 的 mock：验证 hybrid 把 query 透传各通道。 */
class RecordingRetriever implements Retriever {
  lastQuery: string | undefined;
  async indexAll(_items: Array<{ id: string; text: string }>): Promise<void> {}
  async search(query: string, _topK: number): Promise<RetrievalHit[]> {
    this.lastQuery = query;
    return [];
  }
}

/** 记录 close 是否被调用的 mock：验证 close() 扇出。 */
class ClosableRetriever implements Retriever {
  closed = false;
  async indexAll(_items: Array<{ id: string; text: string }>): Promise<void> {}
  async search(_query: string, _topK: number): Promise<RetrievalHit[]> {
    return [];
  }
  close(): void {
    this.closed = true;
  }
}

const rrf = (k: number, rank: number): number => 1 / (k + rank);
const approx = (actual: number, expected: number, msg: string): void => {
  assert.ok(Math.abs(actual - expected) < 1e-12, `${msg}（实得 ${actual}，期望 ${expected}）`);
};

test('RRF 数学：双通道命中的 doc 压过单通道 rank1，且平票序稳定（rrfK=60）', async () => {
  // ch1: X(rank1) A(rank2) M(rank3)；ch2: Y(rank1) A(rank2)。A 双通道各 rank2。
  const ch1 = new MockRetriever([
    { id: 'X', score: 0.9 },
    { id: 'A', score: 0.8 },
    { id: 'M', score: 0.7 },
  ]);
  const ch2 = new MockRetriever([
    { id: 'Y', score: 0.9 },
    { id: 'A', score: 0.6 },
  ]);
  const h = new HybridRetriever([ch1, ch2], { rrfK: 60 });
  const hits = await h.search('q', 10);

  // 手算：A = 1/62 + 1/62；X = Y = 1/61；M = 1/63。
  const byId = Object.fromEntries(hits.map((x) => [x.id, x.score]));
  approx(byId.A!, rrf(60, 2) + rrf(60, 2), 'A = 双通道 rank2 贡献之和');
  approx(byId.X!, rrf(60, 1), 'X = ch1 rank1');
  approx(byId.Y!, rrf(60, 1), 'Y = ch2 rank1');
  approx(byId.M!, rrf(60, 3), 'M = ch1 rank3');
  assert.ok(byId.A! > byId.X!, '双通道命中的 A 严格高于单通道 rank1 的 X');

  // 顺序确定：A 最高；X、Y 平票（1/61），X 先出现（ch1）排前；M 最低。
  assert.deepEqual(
    hits.map((x) => x.id),
    ['A', 'X', 'Y', 'M'],
    'RRF 降序 + 平票按首次出现顺序稳定',
  );
});

test('rank 用通道返回顺序，不按 score 重排', async () => {
  // 通道故意返回 score 升序（P<Q），但 P 在数组前——hybrid 应视 P 为 rank1（RRF 分更高）。
  const ch = new MockRetriever([
    { id: 'P', score: 0.1 },
    { id: 'Q', score: 0.9 },
  ]);
  const h = new HybridRetriever([ch], { rrfK: 60 });
  const hits = await h.search('q', 5);
  assert.equal(hits[0]!.id, 'P', 'P 按返回顺序为 rank1');
  approx(hits[0]!.score, rrf(60, 1), 'P 得 rank1 贡献');
  approx(hits[1]!.score, rrf(60, 2), 'Q 得 rank2 贡献');
});

test('互补召回：向量命中 A(2字中文) / 关键词命中 B(nut⊂peanut)，hybrid 同时召回 A、B', async () => {
  // 语料：A=纯中文谈饮食；B=英文 peanut butter；C=无关干扰。
  const corpus = [
    { id: 'A', text: '他注意饮食' },
    { id: 'B', text: 'peanut butter sandwich' },
    { id: 'C', text: 'the cat sat on the warm rug' },
  ];
  // query 两概念："饮食"(2字中文) 走向量、"nut" 走关键词 trigram（命中 peanut 的子串）。
  const query = '饮食 nut';

  const vector = new VectorRetriever(':memory:', new HashEmbedder());
  const keyword = new KeywordRetriever(':memory:');
  const hybrid = new HybridRetriever([vector, keyword]);
  try {
    await hybrid.indexAll(corpus);

    // 向量臂：语义命中 A（score>0）；对 B 无信号（"nut" 与 "peanut" 是不同整词 token → cosine 0）。
    const vHits = await vector.search(query, 10);
    const vA = vHits.find((x) => x.id === 'A');
    const vB = vHits.find((x) => x.id === 'B');
    assert.ok(vA && vA.score > 0, '向量臂语义命中 A');
    assert.ok(!vB || vB.score === 0, '向量臂对 B 无语义信号（漏 B）');

    // 关键词臂：trigram "nut" 命中 B(peanut)；结构性漏掉 A（"饮食"<3字符无 trigram，A 无匹配 term）。
    const kHits = await keyword.search(query, 10);
    assert.ok(kHits.some((x) => x.id === 'B'), '关键词臂命中 B');
    assert.ok(!kHits.some((x) => x.id === 'A'), '关键词臂漏掉 A（2字中文 trigram 无输出，D-0001）');

    // 融合：hybrid 的 topK 同时含 A、B（各臂各补一个）。
    const hHits = await hybrid.search(query, 3);
    const ids = hHits.map((x) => x.id);
    assert.ok(ids.includes('A'), 'hybrid 召回 A（来自向量臂）');
    assert.ok(ids.includes('B'), 'hybrid 召回 B（来自关键词臂）');
  } finally {
    hybrid.close();
  }
});

test('配置：kCandidate 极小截断候选（rank2 的共识 doc 不进候选）', async () => {
  // ch1: [A, B]；ch2: [C, B]。B 在两通道均 rank2。
  const ch1 = new MockRetriever([
    { id: 'A', score: 0.9 },
    { id: 'B', score: 0.8 },
  ]);
  const ch2 = new MockRetriever([
    { id: 'C', score: 0.9 },
    { id: 'B', score: 0.8 },
  ]);

  // kCandidate=1：各通道只回 top1（A、C），B 从不进候选 → hybrid 无 B。
  const tiny = new HybridRetriever([ch1, ch2], { kCandidate: 1 });
  const tinyHits = await tiny.search('q', 10);
  assert.ok(!tinyHits.some((x) => x.id === 'B'), 'kCandidate=1 截断，B 不进候选');

  // kCandidate=10：B 双通道 rank2 都进候选，融合分 1/62+1/62 最高。
  const wide = new HybridRetriever([ch1, ch2], { kCandidate: 10 });
  const wideHits = await wide.search('q', 10);
  assert.ok(wideHits.some((x) => x.id === 'B'), 'kCandidate 放大后 B 进候选被融合');
  assert.equal(wideHits[0]!.id, 'B', 'B 双通道共识，RRF 分最高');
});

test('配置：rrfK 影响 RRF 分', async () => {
  const ch = new MockRetriever([{ id: 'A', score: 0.9 }]);
  const s0 = (await new HybridRetriever([ch], { rrfK: 0 }).search('q', 5))[0]!.score;
  const s60 = (await new HybridRetriever([ch], { rrfK: 60 }).search('q', 5))[0]!.score;
  approx(s0, rrf(0, 1), 'rrfK=0 时 rank1 = 1/(0+1)');
  approx(s60, rrf(60, 1), 'rrfK=60 时 rank1 = 1/(60+1)');
  assert.ok(s0 > s60, '同名次下 rrfK 越小 RRF 分越大 → rrfK 确实影响结果');
});

test('扇出：indexAll 后两通道各自可召回；移除项后 hybrid 不再返回', async () => {
  const vector = new VectorRetriever(':memory:', new HashEmbedder());
  const keyword = new KeywordRetriever(':memory:');
  const hybrid = new HybridRetriever([vector, keyword]);
  try {
    await hybrid.indexAll([
      { id: 'a', text: 'peanut butter sandwich' },
      { id: 'b', text: 'quantum physics research laboratory' },
    ]);

    // 扇出成功：两通道各自都索引了同一批 items。
    assert.ok((await vector.search('peanut', 5)).some((x) => x.id === 'a'), '向量通道可召回 a');
    assert.ok((await keyword.search('quantum', 5)).some((x) => x.id === 'b'), '关键词通道可召回 b');
    // hybrid 融合后召回 a。
    assert.ok((await hybrid.search('peanut', 5)).some((x) => x.id === 'a'), 'hybrid 召回 a');

    // 移除 a（替换式重建，只留 b）→ hybrid 不再返回 a。
    await hybrid.indexAll([{ id: 'b', text: 'quantum physics research laboratory' }]);
    assert.ok(
      !(await hybrid.search('peanut', 5)).some((x) => x.id === 'a'),
      '移除后 hybrid 不再返回 a',
    );
  } finally {
    hybrid.close();
  }
});

test('边界：空 query 透传各通道（得 []）→ hybrid 返回 []', async () => {
  const c1 = new RecordingRetriever();
  const c2 = new RecordingRetriever();
  const h = new HybridRetriever([c1, c2]);
  assert.deepEqual(await h.search('', 5), [], '各通道得 [] → hybrid []');
  assert.equal(c1.lastQuery, '', 'query 透传到通道 1');
  assert.equal(c2.lastQuery, '', 'query 透传到通道 2');
});

test('边界：所有通道都空（无索引）→ []', async () => {
  const vector = new VectorRetriever(':memory:', new HashEmbedder());
  const keyword = new KeywordRetriever(':memory:');
  const hybrid = new HybridRetriever([vector, keyword]);
  try {
    assert.deepEqual(await hybrid.search('anything', 5), [], '空通道 → []');
  } finally {
    hybrid.close();
  }
});

test('边界：channels=[] → []', async () => {
  const h = new HybridRetriever([]);
  assert.deepEqual(await h.search('anything', 5), [], '无通道 → []');
});

test('边界：close() 关闭所有实现了 close 的通道，不抛（缺 close 的通道跳过）', () => {
  const closable = new ClosableRetriever();
  const noClose = new NullRetriever(); // 未实现 close，应被 typeof 守卫跳过
  const h = new HybridRetriever([closable, noClose]);
  assert.doesNotThrow(() => h.close(), 'close() 不抛');
  assert.ok(closable.closed, '实现了 close 的通道被关闭');
});

test('确定性：同语料 + 同 query 两次结果逐位相同', async () => {
  const corpus = [
    { id: 'a', text: 'peanut butter sandwich' },
    { id: 'b', text: 'quantum physics research laboratory' },
    { id: 'c', text: '他注意饮食均衡' },
  ];
  const build = async (): Promise<RetrievalHit[]> => {
    const vector = new VectorRetriever(':memory:', new HashEmbedder());
    const keyword = new KeywordRetriever(':memory:');
    const hybrid = new HybridRetriever([vector, keyword]);
    await hybrid.indexAll(corpus);
    const hits = await hybrid.search('peanut quantum', 5);
    hybrid.close();
    return hits;
  };
  const first = await build();
  const second = await build();
  assert.deepEqual(second, first, '同语料 + query → 结果逐位确定');
});
