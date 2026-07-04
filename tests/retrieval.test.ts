/**
 * 召回测试（地图 cell 15）。用 stub 嵌入器，不依赖网络。
 * stub 已改造为**可计数**：记录 embed 被调用次数与收到的文本，用于验证增量索引只嵌入 Δ。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { VectorRetriever } from '../src/retrieval/vectorRetriever.ts';
import { NullRetriever } from '../src/retrieval/nullRetriever.ts';

/**
 * 确定性 stub 嵌入器：按关键词存在与否编码成向量（够测余弦排序）。
 * 可计数：calls = embed 被调用次数；texts = 历次收到的全部文本（按序累积）。
 */
function stubEmbedder() {
  const vocab = ['茶', '钢琴', '睡', '项目'];
  const vec = (t: string) => vocab.map((w) => (t.includes(w) ? 1 : 0));
  const stub = {
    calls: 0,
    texts: [] as string[],
    async embed(texts: string[]) {
      stub.calls += 1;
      stub.texts.push(...texts);
      return texts.map(vec);
    },
  };
  return stub;
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

test('VectorRetriever：自开连接设 busy_timeout=5000（向量表与主库同文件，多进程写不裸抛）', () => {
  const r = new VectorRetriever(':memory:', stubEmbedder());
  try {
    // Node 24 实测：PRAGMA busy_timeout 结果列名是 timeout，取 .timeout 别断言裸数字。
    // @ts-expect-error 测试内探 private db（只读 pragma，不改行为）
    const row = r.db.prepare('PRAGMA busy_timeout').get() as { timeout: number };
    assert.equal(row.timeout, 5000);
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

// ---- 增量索引（T4）：以下用例验证嵌入调用量 = Δ，而非 N ----

test('VectorRetriever 增量：(a) 首次 indexAll N 条 → embed 恰收到 N 条文本', async () => {
  const e = stubEmbedder();
  const r = new VectorRetriever(':memory:', e);
  try {
    await r.indexAll([
      { id: 'a', text: '用户喜欢喝茶' },
      { id: 'b', text: '用户在学钢琴' },
      { id: 'c', text: '用户在做项目' },
    ]);
    assert.equal(e.texts.length, 3, '首次全量：3 条都要嵌入');
    const hits = await r.search('我想喝茶', 2);
    assert.equal(hits[0]!.id, 'a', 'search 仍可命中');
  } finally {
    r.close();
  }
});

test('VectorRetriever 增量：(b) 相同 items 再 indexAll → embed 收到 0 条文本', async () => {
  const e = stubEmbedder();
  const r = new VectorRetriever(':memory:', e);
  const items = [
    { id: 'a', text: '用户喜欢喝茶' },
    { id: 'b', text: '用户在学钢琴' },
  ];
  try {
    await r.indexAll(items);
    const textsBefore = e.texts.length;
    const callsBefore = e.calls;
    await r.indexAll(items);
    assert.equal(e.texts.length, textsBefore, '内容没变：一条都不该重新嵌入');
    assert.equal(e.calls, callsBefore, '甚至不该打嵌入接口');
  } finally {
    r.close();
  }
});

test('VectorRetriever 增量：(c) 改1条+删1条+增1条 → embed 恰收到 2 条；被删 id 不再命中', async () => {
  const e = stubEmbedder();
  const r = new VectorRetriever(':memory:', e);
  try {
    await r.indexAll([
      { id: 'a', text: '用户喜欢喝茶' },
      { id: 'b', text: '用户在学钢琴' },
      { id: 'c', text: '用户在做项目' },
    ]);
    const textsBefore = e.texts.length;
    // a 内容变更、b 删除、d 新增，c 原样保留。
    await r.indexAll([
      { id: 'a', text: '用户喜欢喝茶也爱睡觉' },
      { id: 'c', text: '用户在做项目' },
      { id: 'd', text: '用户睡得很晚' },
    ]);
    assert.equal(e.texts.length - textsBefore, 2, '只嵌入 变更的a + 新增的d');
    const hits = await r.search('钢琴', 10);
    assert.ok(!hits.some((h) => h.id === 'b'), '被删的 b 不再出现在结果里');
    assert.equal(hits.length, 3, '剩 a/c/d 三条');
  } finally {
    r.close();
  }
});

test('VectorRetriever 增量：(d) indexAll([]) 清空全表 → search 返回空', async () => {
  const e = stubEmbedder();
  const r = new VectorRetriever(':memory:', e);
  try {
    await r.indexAll([{ id: 'a', text: '喝茶' }]);
    await r.indexAll([]);
    assert.deepEqual(await r.search('茶', 5), [], '替换式语义：空集合 = 全清');
  } finally {
    r.close();
  }
});

test('VectorRetriever 增量：(e) 旧 schema（无 hash 列）→ 构造不抛错，随后索引/召回正常', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mw-retrieval-'));
  const dbPath = join(dir, 'old-schema.db');
  try {
    // 手工造一个旧版库：vectors 表没有 hash 列，还塞了一条旧数据。
    const old = new DatabaseSync(dbPath);
    old.exec('CREATE TABLE vectors (id TEXT PRIMARY KEY, vec TEXT NOT NULL)');
    old.prepare('INSERT INTO vectors (id, vec) VALUES (?, ?)').run('legacy', '[1,0,0,0]');
    old.close();

    // 构造即触发"重建式迁移"（索引是可重建资产，宁可重建不带病迁移）。
    const r = new VectorRetriever(dbPath, stubEmbedder());
    try {
      await r.indexAll([{ id: 'a', text: '用户喜欢喝茶' }]);
      const hits = await r.search('喝茶', 5);
      assert.equal(hits.length, 1, '旧数据已随重建清掉，只剩新索引');
      assert.equal(hits[0]!.id, 'a');
    } finally {
      r.close();
    }
  } finally {
    rmSync(dir, { recursive: true, force: true }); // 临时 db 用完即清，不留残留
  }
});

test('NullRetriever：index 不做事、search 永远空', async () => {
  const r = new NullRetriever();
  await r.indexAll([{ id: 'a', text: 'x' }]);
  assert.deepEqual(await r.search('x', 5), []);
});
