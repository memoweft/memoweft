/**
 * 召回测试（地图 cell 15）。用 stub 嵌入器，不依赖网络。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { VectorRetriever } from '../src/retrieval/vectorRetriever.ts';
import { NullRetriever } from '../src/retrieval/nullRetriever.ts';

/** 确定性 stub 嵌入器：按关键词存在与否编码成向量（够测余弦排序）。 */
function stubEmbedder() {
  const vocab = ['茶', '钢琴', '睡', '项目'];
  const vec = (t: string) => vocab.map((w) => (t.includes(w) ? 1 : 0));
  return { async embed(texts: string[]) { return texts.map(vec); } };
}

test('VectorRetriever：余弦召回，最相关排第一', async () => {
  const r = new VectorRetriever(':memory:', stubEmbedder());
  try {
    await r.indexAll([
      { id: 'a', text: '用户喜欢喝茶' },
      { id: 'b', text: '用户在学钢琴' },
      { id: 'c', text: '用户在做项目' },
    ]);
    const hits = await r.search('我想喝茶', 2);
    assert.ok(hits.length <= 2);
    assert.equal(hits[0]!.id, 'a', '最相关 = 茶');
  } finally {
    r.close();
  }
});

test('VectorRetriever：indexAll 替换式重建（旧的清掉）', async () => {
  const r = new VectorRetriever(':memory:', stubEmbedder());
  try {
    await r.indexAll([{ id: 'a', text: '喝茶' }]);
    await r.indexAll([{ id: 'b', text: '钢琴' }]);
    const hits = await r.search('钢琴', 5);
    assert.equal(hits.length, 1, '只剩替换后的');
    assert.equal(hits[0]!.id, 'b');
  } finally {
    r.close();
  }
});

test('VectorRetriever：空索引 → 空召回', async () => {
  const r = new VectorRetriever(':memory:', stubEmbedder());
  try {
    assert.deepEqual(await r.search('茶', 5), []);
  } finally {
    r.close();
  }
});

test('NullRetriever：index 不做事、search 永远空', async () => {
  const r = new NullRetriever();
  await r.indexAll([{ id: 'a', text: 'x' }]);
  assert.deepEqual(await r.search('x', 5), []);
});
