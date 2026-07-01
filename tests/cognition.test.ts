/**
 * 认知层 + 把握度 + consolidate 测试（地图 cell 15：离线护栏）。
 * consolidate 用 stub LLM，不依赖网络。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { computeConfidence, deriveCredStatus } from '../src/consolidation/confidence.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import { updateProfile } from '../src/consolidation/updateProfile.ts';
import { NullRetriever } from '../src/retrieval/nullRetriever.ts';

test('把握度：推测最低、支持加分、反对→冲突', () => {
  const inferred = computeConfidence({ contentType: 'fact', formedBy: 'inferred', supportCount: 1, contradictCount: 0 });
  const stated = computeConfidence({ contentType: 'fact', formedBy: 'stated', supportCount: 1, contradictCount: 0 });
  assert.ok(stated > inferred, '亲口 > 推测');
  const more = computeConfidence({ contentType: 'fact', formedBy: 'stated', supportCount: 4, contradictCount: 0 });
  assert.ok(more > stated, '更多支持证据 → 更高');
  assert.equal(deriveCredStatus(800, 0, 'fact'), 'stable');
  assert.equal(deriveCredStatus(200, 0, 'fact'), 'candidate');
  assert.equal(deriveCredStatus(800, 1, 'fact'), 'conflicted', '有反对证据 → 冲突中');
});

test('分型时间策略 v1：临时类（state）封顶、永不稳定（Bug B）', () => {
  // 临时状态即使多次支持，也封顶、不进稳定。
  const stateHi = computeConfidence({ contentType: 'state', formedBy: 'stated', supportCount: 9, contradictCount: 0 });
  const prefHi = computeConfidence({ contentType: 'preference', formedBy: 'stated', supportCount: 9, contradictCount: 0 });
  assert.ok(stateHi <= 300, `state 封顶 ≤300（实际 ${stateHi}）`);
  assert.ok(prefHi > stateHi, '稳定类（偏好）置信高于临时类（state）——修正排序倒挂');
  assert.equal(deriveCredStatus(stateHi, 0, 'state'), 'low', 'state 永不进稳定，最多低置信');
  assert.equal(deriveCredStatus(1000, 0, 'state'), 'low', 'state 即便高分也不稳定');
});

test('认知存储：put + 溯源链 + all + update + remove', () => {
  const s = new SqliteCognitionStore(':memory:');
  try {
    const c = s.put({
      subjectId: 'owner',
      content: '用户喜欢被直接指出问题',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
      evidence: [{ evidenceId: 'e1', relation: 'support' }],
    });
    assert.deepEqual(s.sourcesOf(c.id), [{ evidenceId: 'e1', relation: 'support' }]);
    assert.equal(s.all('owner').length, 1);

    const u = s.update(c.id, { confidence: 800, credStatus: 'stable' });
    assert.equal(u?.confidence, 800);
    assert.equal(s.get(c.id)?.credStatus, 'stable');

    assert.equal(s.remove(c.id), true);
    assert.equal(s.get(c.id), null);
    assert.deepEqual(s.sourcesOf(c.id), [], '溯源链一并删');
  } finally {
    s.close();
  }
});

test('consolidate 增量 · new：新增 + DLA 自算 + 标已消化', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const e1 = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '我喜欢喝茶' });
    const event = evt.put({ subjectId: 'owner', summary: '用户喜欢喝茶', occurredAt: e1.occurredAt, evidenceIds: [e1.id] });
    const stub = {
      callCount: 0,
      async chat() {
        this.callCount++;
        return `{"new":[{"content":"用户喜欢喝茶","content_type":"preference","formed_by":"stated","support_evidence_ids":["${e1.id}"],"confidence":999}]}`;
      },
    };
    const r = await consolidate('owner', { eventStore: evt, evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r.created.length, 1);
    assert.notEqual(r.created[0]!.confidence, 999, '不采信 LLM 自报');
    assert.deepEqual(cog.sourcesOf(r.created[0]!.id), [{ evidenceId: e1.id, relation: 'support' }], '溯源回原话');
    // 事件已消化 → 再 consolidate 无新事件、不调模型
    const r2 = await consolidate('owner', { eventStore: evt, evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r2.processedEvents, 0);
    assert.equal(stub.callCount, 1, '没新事件不再调模型');
  } finally {
    ev.close();
    evt.close();
    cog.close();
  }
});

test('consolidate · 证据级引用：一事件多原话，认知只挂被引的那条（去污染·地基债）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    // 一个事件覆盖 3 条无关原话
    const eTea = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '我喜欢喝茶' });
    const eAge = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '我今年26岁' });
    const eSleep = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '昨晚没睡好' });
    const event = evt.put({ subjectId: 'owner', summary: '用户聊了喝茶/年龄/睡眠', occurredAt: eTea.occurredAt, evidenceIds: [eTea.id, eAge.id, eSleep.id] });
    // LLM 只为"喜欢喝茶"引了喝茶那条原话
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"new":[{"content":"用户喜欢喝茶","content_type":"preference","formed_by":"stated","support_evidence_ids":["${eTea.id}"]}]}`; } };
    void event;
    const r = await consolidate('owner', { eventStore: evt, evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r.created.length, 1);
    const links = cog.sourcesOf(r.created[0]!.id);
    assert.equal(links.length, 1, '只挂被引的那 1 条原话，不再吞下同事件的 26岁/没睡好');
    assert.equal(links[0]!.evidenceId, eTea.id);
  } finally {
    ev.close(); evt.close(); cog.close();
  }
});

test('consolidate · 没引到有效原话 id → 跳过不落（地基债 fork：宁缺毋滥）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const e1 = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '我喜欢喝茶' });
    evt.put({ subjectId: 'owner', summary: '用户喜欢喝茶', occurredAt: e1.occurredAt, evidenceIds: [e1.id] });
    // LLM 编造 / 漏引证据 id
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"new":[{"content":"用户喜欢喝茶","content_type":"preference","formed_by":"stated","support_evidence_ids":["ev-不存在"]}]}`; } };
    const r = await consolidate('owner', { eventStore: evt, evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r.created.length, 0, '没可溯源原话 → 不落该认知');
    assert.equal(cog.all('owner').length, 0);
  } finally {
    ev.close(); evt.close(); cog.close();
  }
});

test('consolidate 增量 · correct：纠正 → 旧失效保留、新采纳（M6）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const old = cog.put({ subjectId: 'owner', content: '用户喜欢喝茶', contentType: 'preference', formedBy: 'stated', confidence: 600, credStatus: 'limited' });
    const e2 = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '其实我现在不喝茶了，改喝咖啡' });
    const event = evt.put({ subjectId: 'owner', summary: '用户表示现在不喝茶改喝咖啡', occurredAt: e2.occurredAt, evidenceIds: [e2.id] });
    const stub = {
      callCount: 0,
      async chat() {
        this.callCount++;
        return `{"correct":[{"cognition_id":"${old.id}","content":"用户现在喝咖啡，不喝茶了","content_type":"preference","formed_by":"stated","support_evidence_ids":["${e2.id}"]}]}`;
      },
    };
    const r = await consolidate('owner', { eventStore: evt, evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r.corrected, 1);
    assert.ok(cog.get(old.id)?.invalidAt, '旧判断标失效');
    assert.equal(cog.all('owner').length, 2, '旧的保留 + 新的');
    const act = cog.active('owner');
    assert.equal(act.length, 1, '只剩新的活跃');
    assert.equal(act[0]!.content, '用户现在喝咖啡，不喝茶了');
  } finally {
    ev.close();
    evt.close();
    cog.close();
  }
});

test('consolidate 增量 · reinforce：强化 → 置信升、补挂证据', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const e0 = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '我喜欢喝茶' });
    const c0 = cog.put({ subjectId: 'owner', content: '用户喜欢喝茶', contentType: 'preference', formedBy: 'stated', confidence: 600, credStatus: 'limited', evidence: [{ evidenceId: e0.id, relation: 'support' }] });
    const e1 = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '又泡了壶茶' });
    const event = evt.put({ subjectId: 'owner', summary: '用户又泡茶', occurredAt: e1.occurredAt, evidenceIds: [e1.id] });
    const before = cog.get(c0.id)!.confidence;
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"reinforce":[{"cognition_id":"${c0.id}","support_evidence_ids":["${e1.id}"]}]}`; } };
    const r = await consolidate('owner', { eventStore: evt, evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r.reinforced, 1);
    assert.ok(cog.get(c0.id)!.confidence > before, '置信升（多了支持证据）');
    assert.equal(cog.sourcesOf(c0.id).length, 2, '补挂新证据');
  } finally {
    ev.close();
    evt.close();
    cog.close();
  }
});

test('updateProfile：M4 归因自动并进（更新画像顺带对新现象产假设）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    // 已沉淀好的现象 + 一条观察证据（都已被事件覆盖且已消化 → distill/consolidate 不再动它们）。
    const eSleep = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '昨晚没睡好', occurredAt: '2026-06-23T08:00:00.000Z' });
    const eSleep2 = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '还是没睡好', occurredAt: '2026-06-23T08:05:00.000Z' });
    const eGame = ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: '游戏开到凌晨3:30', occurredAt: '2026-06-23T03:30:00.000Z' });
    const event = evt.put({ subjectId: 'owner', summary: '用户昨晚没睡好；游戏开到3:30', occurredAt: eSleep.occurredAt, evidenceIds: [eSleep.id, eSleep2.id, eGame.id] });
    evt.markConsolidated([event.id]);
    // 现象【反复出现】≥2 条支撑，满足④攒够门槛，归因才触发（治慢④）。
    cog.put({ subjectId: 'owner', content: '用户昨晚没睡好', contentType: 'state', formedBy: 'stated', confidence: 250, credStatus: 'low', evidence: [{ evidenceId: eSleep.id, relation: 'support' }, { evidenceId: eSleep2.id, relation: 'support' }] });
    // distill/consolidate 无新料 → 不调模型；唯一一次调用来自 attribute。
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"hypotheses":[{"content":"可能因为玩游戏太晚导致没睡好","based_on_evidence_ids":["${eGame.id}"]}]}`; } };
    const r = await updateProfile('owner', { evidenceStore: ev, eventStore: evt, cognitionStore: cog, retriever: new NullRetriever(), llm: stub });
    assert.equal(r.attributed.hypotheses.length, 1, '更新画像顺带产出假设');
    assert.equal(stub.callCount, 1, '只有 attribute 调了模型（distill/consolidate 无新料）');
    assert.ok(cog.active('owner').some((c) => c.contentType === 'hypothesis'), '假设进了画像');
    assert.ok(r.timings && typeof r.timings.totalMs === 'number' && r.timings.totalMs >= 0, '治慢②：带各步耗时 timings');
  } finally {
    ev.close(); evt.close(); cog.close();
  }
});

test('updateProfile：嵌入器挂了也不回滚画像（索引是读路径优化，失败不挡）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  // 索引必炸的 retriever（模拟 Ollama 没启动 → fetch failed）。
  const brokenRetriever = {
    async indexAll() { throw new Error('fetch failed'); },
    async search() { return []; },
  };
  try {
    ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '我喜欢喝茶' });
    const stub = {
      callCount: 0,
      async chat() {
        this.callCount++;
        if (this.callCount === 1) return '用户表示喜欢喝茶。';
        const evId = evt.evidenceOf(evt.all('owner')[0]!.id)[0]!;
        return `{"new":[{"content":"用户喜欢喝茶","content_type":"preference","formed_by":"stated","support_evidence_ids":["${evId}"]}]}`;
      },
    };
    const r = await updateProfile('owner', { evidenceStore: ev, eventStore: evt, cognitionStore: cog, retriever: brokenRetriever, llm: stub });
    assert.equal(r.consolidated.created.length, 1, '画像照常生成（不因索引失败回滚）');
    assert.equal(r.indexed, 0, '没索引成功');
    assert.ok(r.indexError && r.indexError.includes('fetch failed'), '索引错误被捕获上报，而非抛出');
    assert.equal(cog.active('owner').length, 1, '认知已落库');
  } finally {
    ev.close(); evt.close(); cog.close();
  }
});

test('updateProfile：一键 distill + 增量 consolidate（新对话自动进画像）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '我喜欢喝茶' });
    const stub = {
      callCount: 0,
      async chat() {
        this.callCount++;
        if (this.callCount === 1) return '用户表示喜欢喝茶。'; // distill
        const evId = evt.evidenceOf(evt.all('owner')[0]!.id)[0]!;
        return `{"new":[{"content":"用户喜欢喝茶","content_type":"preference","formed_by":"stated","support_evidence_ids":["${evId}"]}]}`;
      },
    };
    const r = await updateProfile('owner', { evidenceStore: ev, eventStore: evt, cognitionStore: cog, retriever: new NullRetriever(), llm: stub });
    assert.ok(r.distilled.event, '自动整理出事件');
    assert.equal(r.consolidated.created.length, 1, '并生成画像');
    assert.equal(r.indexError, null, '索引成功');
    assert.equal(cog.active('owner')[0]!.content, '用户喜欢喝茶');
  } finally {
    ev.close();
    evt.close();
    cog.close();
  }
});
