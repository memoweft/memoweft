/**
 * HashEmbedder 测试。
 * 验证：确定性、维度恒定、L2 归一化（空文本全零）、相关文本余弦 > 不相关文本余弦。
 * cosine 辅助自带，不从 src 依赖内部函数。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { HashEmbedder, DEFAULT_DIM } from './hashEmbedder.ts';

// ---- 自带向量辅助（不依赖 src 内部实现）----
function dot(a: number[], b: number[]): number {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += (a[i] ?? 0) * (b[i] ?? 0);
  return s;
}
function norm(v: number[]): number {
  return Math.sqrt(dot(v, v));
}
function cosine(a: number[], b: number[]): number {
  const na = norm(a);
  const nb = norm(b);
  if (na === 0 || nb === 0) return 0;
  return dot(a, b) / (na * nb);
}

test('确定性：同一文本两次 embed 逐元素相等', async () => {
  const e = new HashEmbedder();
  const [v1] = await e.embed(['用户喜欢喝茶 and coding at night']);
  const [v2] = await e.embed(['用户喜欢喝茶 and coding at night']);
  assert.ok(v1 && v2);
  assert.deepEqual(v1, v2, '同输入必须恒同输出');
});

test('维度恒为 DIM；非空文本模长≈1，空/纯空白文本全零、模长 0', async () => {
  const e = new HashEmbedder();
  const [nonEmpty, empty, blank] = await e.embed(['hello 世界', '', '   \t\n ']);
  assert.ok(nonEmpty && empty && blank);

  assert.equal(nonEmpty.length, DEFAULT_DIM, '维度恒为 DIM');
  assert.equal(empty.length, DEFAULT_DIM);
  assert.equal(blank.length, DEFAULT_DIM);

  assert.ok(Math.abs(norm(nonEmpty) - 1) < 1e-9, '非空文本 L2 模长≈1');
  assert.ok(
    empty.every((x) => x === 0),
    '空文本全零',
  );
  assert.equal(norm(empty), 0, '空文本模长 0');
  assert.ok(
    blank.every((x) => x === 0),
    '纯空白文本无 token → 全零',
  );
  assert.equal(norm(blank), 0);
});

test('维度可配：构造参数改 dim，向量长度随之变', async () => {
  const e = new HashEmbedder(64);
  assert.equal(e.dim, 64);
  const [v] = await e.embed(['configurable dimension 维度可配']);
  assert.ok(v);
  assert.equal(v.length, 64);
  assert.ok(Math.abs(norm(v) - 1) < 1e-9);
});

test('相关文本余弦 > 不相关文本余弦（拉丁）', async () => {
  const e = new HashEmbedder();
  const [q, related, unrelated] = await e.embed([
    'the cat sat on the warm mat',
    'a cat on a warm mat',
    'quantum physics research laboratory',
  ]);
  assert.ok(q && related && unrelated);
  assert.ok(
    cosine(q, related) > cosine(q, unrelated),
    `拉丁：related=${cosine(q, related)} 应 > unrelated=${cosine(q, unrelated)}`,
  );
});

test('相关文本余弦 > 不相关文本余弦（中文，含"饮食""过敏"2 字词）', async () => {
  const e = new HashEmbedder();
  const [q, related, unrelated] = await e.embed([
    '我最近很注意饮食也担心食物过敏',
    '关于饮食和过敏我想了解更多',
    '钢琴演奏需要长期反复练习',
  ]);
  assert.ok(q && related && unrelated);
  assert.ok(
    cosine(q, related) > cosine(q, unrelated),
    `中文：related=${cosine(q, related)} 应 > unrelated=${cosine(q, unrelated)}`,
  );
});

test('2 字中文词与含它的文本有 token 重叠：char-bigram+单字', async () => {
  const e = new HashEmbedder();
  const [query, doc] = await e.embed(['饮食', '他很注意饮食健康']);
  assert.ok(query && doc);
  assert.ok(cosine(query, doc) > 0, '"饮食"必须与含它的句子有非零重叠');
});
