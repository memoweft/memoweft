/**
 * 写路径仪表测试（计量契约：只观测，不改变行为）。
 *
 * 验证 consolidate / updateProfile 的计量字段：
 *   - profileSize = 本轮注入 prompt 的 active 认知条数（画像多大）；
 *   - promptChars = buildMessages 产物全部 content 字符数之和（prompt 多大）；
 *   - 无新事件早退 → 两值均 0（0 = 本轮未执行整理）；
 *   - updateProfile.metrics 从 consolidate 结果原样透传。
 * 全部用 stub LLM + 内存库（:memory:），不依赖网络、不留运行时残留。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import { updateProfile } from '../src/consolidation/updateProfile.ts';
import { NullRetriever } from '../src/retrieval/nullRetriever.ts';

/** stub LLM：返回合法的"空产出"JSON（四类全空）——只走流程不动画像，专测计量。 */
function emptyOutputStub() {
  return {
    callCount: 0,
    async chat() {
      this.callCount++;
      return '{"new":[],"reinforce":[],"correct":[],"conflict":[]}';
    },
  };
}

test('写路径仪表 · 有新事件：profileSize = 预置 active 条数、promptChars > 0', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    // 预置 2 条 active 认知（本轮会被拼进 prompt 的"现有画像"）。
    cog.put({
      subjectId: 'owner',
      content: '用户喜欢喝茶',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
    });
    cog.put({
      subjectId: 'owner',
      content: '用户偏好纸质笔记本',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
    });
    // 预置 1 个未消化事件（触发本轮真正执行整理）。
    const e1 = ev.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 'h',
      rawContent: '周末去爬山了',
    });
    evt.put({
      subjectId: 'owner',
      summary: '用户周末去爬山',
      occurredAt: e1.occurredAt,
      evidenceIds: [e1.id],
    });

    const stub = emptyOutputStub();
    const r = await consolidate('owner', {
      eventStore: evt,
      evidenceStore: ev,
      cognitionStore: cog,
      llm: stub,
    });
    assert.equal(r.profileSize, 2, 'profileSize = 预置的 active 认知条数');
    assert.ok(r.promptChars > 0, `promptChars 应 > 0（实际 ${r.promptChars}）`);
    assert.equal(r.created.length, 0, '空产出 → 画像零变化（指标只观测）');
  } finally {
    ev.close();
    evt.close();
    cog.close();
  }
});

test('写路径仪表 · 无未消化事件早退：两值均为 0（0 = 本轮未执行整理）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    // 有画像但没新事件 → 早退分支，不调模型、计量归 0。
    cog.put({
      subjectId: 'owner',
      content: '用户喜欢喝茶',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
    });
    const stub = emptyOutputStub();
    const r = await consolidate('owner', {
      eventStore: evt,
      evidenceStore: ev,
      cognitionStore: cog,
      llm: stub,
    });
    assert.equal(r.profileSize, 0, '早退 → profileSize = 0');
    assert.equal(r.promptChars, 0, '早退 → promptChars = 0');
    assert.equal(stub.callCount, 0, '早退不调模型');
  } finally {
    ev.close();
    evt.close();
    cog.close();
  }
});

test('写路径仪表 · 经 updateProfile：result.metrics 与 consolidate 值一致', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    // 预置 1 条 active 认知（preference，不是 state 现象 → attribute 不触发、不调模型）。
    cog.put({
      subjectId: 'owner',
      content: '用户喜欢喝茶',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
    });
    // 证据已被事件覆盖（distill 无待提炼、不调模型）、事件未消化（consolidate 会执行）。
    const e1 = ev.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 'h',
      rawContent: '周末去爬山了',
    });
    evt.put({
      subjectId: 'owner',
      summary: '用户周末去爬山',
      occurredAt: e1.occurredAt,
      evidenceIds: [e1.id],
    });

    const stub = emptyOutputStub();
    const r = await updateProfile('owner', {
      evidenceStore: ev,
      eventStore: evt,
      cognitionStore: cog,
      retriever: new NullRetriever(),
      llm: stub,
    });
    assert.equal(stub.callCount, 1, '只有 consolidate 调了模型（distill 无料、attribute 无现象）');
    assert.equal(
      r.metrics.profileSize,
      r.consolidated.profileSize,
      'metrics.profileSize 从 consolidate 透传',
    );
    assert.equal(
      r.metrics.promptChars,
      r.consolidated.promptChars,
      'metrics.promptChars 从 consolidate 透传',
    );
    assert.equal(r.metrics.profileSize, 1, '本轮注入 prompt 的 active 认知 = 预置的 1 条');
    assert.ok(r.metrics.promptChars > 0, `promptChars 应 > 0（实际 ${r.metrics.promptChars}）`);
  } finally {
    ev.close();
    evt.close();
    cog.close();
  }
});
