/**
 * KeywordRetriever 测试：FTS5 trigram + BM25 关键词召回。
 * 全部使用 ':memory:' 数据库，不访问网络或嵌入模型。
 *
 * 覆盖：term 命中相关 doc / BM25 稀有词更靠前 / 3 字中文命中且 2 字中文返回 []。
 *       增量（改内容反映更新、移除 id 不再召回、空集合清空）/ FTS5 特殊字符不抛错 /
 *       空 query → [] / 确定性。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { KeywordRetriever, FtsUnavailableError } from '../../src/retrieval/keywordRetriever.ts';

test('index 小语料 → search 按 term 命中相关 doc', async () => {
  const r = new KeywordRetriever(':memory:');
  try {
    await r.indexAll([
      { id: 'a', text: 'he is allergic to peanut and shellfish' },
      { id: 'b', text: 'the cat sat on the warm mat' },
      { id: 'c', text: 'quantum physics research laboratory' },
    ]);
    const hits = await r.search('peanut allergy', 5);
    assert.ok(hits.length >= 1, '应召回含 peanut 的 doc');
    assert.equal(hits[0]!.id, 'a', '最相关 = 含 peanut 的 a');
    assert.ok(hits[0]!.score > 0, 'score 取正向（越大越相关）');
  } finally {
    r.close();
  }
});

test('BM25：更稀有的词把其 doc 排更前', async () => {
  const r = new KeywordRetriever(':memory:');
  try {
    // 'zebra' 稀有（只在 d1）；'apple' 常见（d2..d5）。query 两词 OR，稀有词 doc 应排最前。
    await r.indexAll([
      { id: 'd1', text: 'zebra grazing quietly' },
      { id: 'd2', text: 'apple orchard morning' },
      { id: 'd3', text: 'apple pie recipe' },
      { id: 'd4', text: 'apple juice fresh' },
      { id: 'd5', text: 'apple tree garden' },
    ]);
    const hits = await r.search('zebra apple', 5);
    assert.equal(hits[0]!.id, 'd1', '稀有词 zebra 的 doc 排第一');
    const d1 = hits.find((h) => h.id === 'd1')!;
    const commonBest = Math.max(...hits.filter((h) => h.id !== 'd1').map((h) => h.score));
    assert.ok(d1.score > commonBest, '稀有词 doc 的 score 严格高于任一常见词 doc');
  } finally {
    r.close();
  }
});

test('CJK：3 字中文 query 命中；2 字中文 query 返回 [](：trigram <3 字符无输出)', async () => {
  const r = new KeywordRetriever(':memory:');
  try {
    await r.indexAll([
      { id: 'z1', text: '他很注意饮食健康也担心食物过敏' },
      { id: 'z2', text: '量子物理研究非常有趣需要长期投入' },
    ]);
    // 3 字中文 → trigram 有输出，命中。
    const hit3 = await r.search('饮食健', 5);
    assert.ok(
      hit3.some((h) => h.id === 'z1'),
      '3 字中文 query 应命中含它的 doc',
    );
    // 2 字中文 → trigram 无 trigram token，MATCH 返回 0 = 预期（向量通道兜底，非本类的活）。
    const hit2 = await r.search('饮食', 5);
    assert.deepEqual(hit2, [], '2 字中文 query 返回 []（trigram 限制）');
  } finally {
    r.close();
  }
});

test('增量：indexAll 改内容后 search 反映更新；移除的 id 不再召回', async () => {
  const r = new KeywordRetriever(':memory:');
  try {
    await r.indexAll([
      { id: 'a', text: 'user enjoys drinking green tea' },
      { id: 'b', text: 'user practices the piano daily' },
      { id: 'c', text: 'user works on the project' },
    ]);
    // 改 a 的内容（改词），删 b（不再传），保留 c。
    await r.indexAll([
      { id: 'a', text: 'user enjoys hiking mountain trails' },
      { id: 'c', text: 'user works on the project' },
    ]);

    // 旧内容 tea 不再命中 a。
    const teaHits = await r.search('tea', 5);
    assert.ok(!teaHits.some((h) => h.id === 'a'), '改内容后旧词 tea 不再命中 a');
    // 新内容 hiking 命中 a。
    const hikeHits = await r.search('hiking', 5);
    assert.ok(
      hikeHits.some((h) => h.id === 'a'),
      '新词 hiking 命中更新后的 a',
    );
    // 被移除的 b 不再召回。
    const pianoHits = await r.search('piano', 5);
    assert.ok(!pianoHits.some((h) => h.id === 'b'), '移除的 b 不再召回');
  } finally {
    r.close();
  }
});

test('增量：相同 items 再 indexAll 不改变召回（内容未变）', async () => {
  const r = new KeywordRetriever(':memory:');
  const items = [
    { id: 'a', text: 'peanut butter sandwich' },
    { id: 'b', text: 'chocolate milkshake dessert' },
  ];
  try {
    await r.indexAll(items);
    const first = await r.search('peanut chocolate', 5);
    await r.indexAll(items);
    const second = await r.search('peanut chocolate', 5);
    assert.deepEqual(second, first, '内容未变：两次 indexAll 后召回一致');
  } finally {
    r.close();
  }
});

test('增量：indexAll([]) 清空全表 → search 返回 []', async () => {
  const r = new KeywordRetriever(':memory:');
  try {
    await r.indexAll([{ id: 'a', text: 'peanut butter' }]);
    await r.indexAll([]);
    assert.deepEqual(await r.search('peanut', 5), [], '空集合 = 全清');
  } finally {
    r.close();
  }
});

test('query 含 FTS5 特殊字符（" * ( ) : ^）不抛错', async () => {
  const r = new KeywordRetriever(':memory:');
  try {
    await r.indexAll([{ id: 'a', text: 'plain document with peanut inside' }]);
    // 每个都不该抛 MATCH 语法错；含 peanut 的那条应仍能召回 a。
    await assert.doesNotReject(() => r.search('"', 5));
    await assert.doesNotReject(() => r.search('*', 5));
    await assert.doesNotReject(() => r.search('(', 5));
    await assert.doesNotReject(() => r.search('peanut* (OR) ^2 "quote:', 5));
    const hits = await r.search('peanut* (OR) ^2 "quote:', 5);
    assert.ok(
      hits.some((h) => h.id === 'a'),
      '消毒后 peanut 仍命中 a',
    );
  } finally {
    r.close();
  }
});

test('空 query / 纯空白 query → []', async () => {
  const r = new KeywordRetriever(':memory:');
  try {
    await r.indexAll([{ id: 'a', text: 'anything here' }]);
    assert.deepEqual(await r.search('', 5), [], '空串 → []');
    assert.deepEqual(await r.search('   \t\n ', 5), [], '纯空白 → []');
  } finally {
    r.close();
  }
});

test('确定性：同语料 + 同 query 两次结果逐字相同', async () => {
  const corpus = [
    { id: 'a', text: 'rare xylophone melody echoes' },
    { id: 'b', text: 'common word repeated word again word' },
    { id: 'c', text: 'another common word here word' },
  ];
  const r1 = new KeywordRetriever(':memory:');
  const r2 = new KeywordRetriever(':memory:');
  try {
    await r1.indexAll(corpus);
    await r2.indexAll(corpus);
    const h1 = await r1.search('xylophone word', 5);
    const h2 = await r2.search('xylophone word', 5);
    assert.deepEqual(h2, h1, '同语料 + 同 query → 结果确定');
  } finally {
    r1.close();
    r2.close();
  }
});

test('unicode61 tokenizer 可配（纯英文场景），基本召回可用', async () => {
  const r = new KeywordRetriever(':memory:', { tokenizer: 'unicode61' });
  try {
    await r.indexAll([
      { id: 'a', text: 'the quick brown fox' },
      { id: 'b', text: 'lazy dog sleeps' },
    ]);
    const hits = await r.search('fox', 5);
    assert.ok(
      hits.some((h) => h.id === 'a'),
      'unicode61 下 fox 命中 a',
    );
  } finally {
    r.close();
  }
});

test('FtsUnavailableError 是具名错误，供 Core 工厂选择回退检索器', () => {
  // 这里不模拟 FTS5 缺失（当前 node:sqlite 自带 FTS5），只锁死错误类的形状与可 instanceof 判定。
  const e = new FtsUnavailableError('probe');
  assert.ok(e instanceof Error, '继承自 Error');
  assert.equal(e.name, 'FtsUnavailableError', 'name 具名，便于工厂分支判定');
});
