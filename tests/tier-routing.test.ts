/**
 * cloud/local tier 路由 · 离线护栏（第 6 步·档2）。
 *
 * 验收：写路径隐私关按【当前写模型 tier】决定筛哪个授权位——
 *   tier=cloud 筛 allowCloudRead（旧行为，逐字节不变）；tier=local 筛 allowLocalRead（本地模型能读 observed）。
 * 并锁三件事：① 覆盖修复(D8)：被挡证据留 pending、换 tier 后能被补消化；
 *   ② inference 门(D4)：inference=false 的证据两个 tier 都进不了画像（distill/consolidate 三处一致）；
 *   ③ 挂账信号 tierBlockedCount：只数 tier 读不到的（不含 inference=false）。
 * 全用 stub LLM（tier 挂在 stub 上），不依赖网络。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { filterReadableByTier } from '../src/evidence/privacy.ts';
import { distill } from '../src/distillation/distill.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import type { ChatMessage } from '../src/llm/client.ts';

const CLOUD_TXT = '可以上云的话';
const LOCAL_TXT = '机密不许上云的话';

/** stub LLM：捕获收到的 messages；可选带 tier（不带 = 缺省 cloud）。 */
function makeStub(reply: string, tier?: 'cloud' | 'local') {
  const state = { seen: '', callCount: 0 };
  const stub = {
    callCount: 0,
    ...(tier ? { tier } : {}),
    async chat(msgs: ChatMessage[]): Promise<string> {
      this.callCount++;
      state.callCount = this.callCount;
      state.seen = JSON.stringify(msgs);
      return reply;
    },
  };
  return { stub, state };
}

test('filterReadableByTier：cloud 筛 allowCloudRead / local 筛 allowLocalRead / 缺省 cloud', () => {
  const items = [
    { id: 'a', allowCloudRead: true, allowLocalRead: false },
    { id: 'b', allowCloudRead: false, allowLocalRead: true },
    { id: 'c', allowCloudRead: true, allowLocalRead: true },
  ];
  assert.deepEqual(filterReadableByTier(items, 'cloud').map((x) => x.id), ['a', 'c']);
  assert.deepEqual(filterReadableByTier(items, 'local').map((x) => x.id), ['b', 'c']);
  assert.deepEqual(filterReadableByTier(items).map((x) => x.id), ['a', 'c'], '缺省 = cloud');
});

test('distill·tier=local：observed(cloud=false, local=true) 被消化进 event 并覆盖（闭环）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  try {
    // observed → observedDefaults：local=true / cloud=false / inference=true
    const eObs = ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: LOCAL_TXT, allowCloudRead: false, occurredAt: '2026-06-23T08:00:00.000Z' });
    const { stub, state } = makeStub('一个事件摘要', 'local');
    const r = await distill('owner', { evidenceStore: ev, eventStore: evt, llm: stub });
    assert.ok(state.seen.includes(LOCAL_TXT), '本地模型能读 observed 原话 → 进 prompt');
    assert.ok(r.event, '建出事件');
    assert.equal(r.tierBlockedCount, 0, '本地 tier 下 observed 不算被挡');
    const covered = new Set(evt.coveredEvidenceIds('owner'));
    assert.ok(covered.has(eObs.id), 'observed 被覆盖（已消化）');
  } finally {
    ev.close(); evt.close();
  }
});

test('覆盖修复(D8)·先云后本地：cloud tier 挂账的 observed，换 local tier 后被补消化', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  try {
    const eCloud = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: CLOUD_TXT, allowCloudRead: true, occurredAt: '2026-06-23T08:00:00.000Z' });
    const eObs = ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: LOCAL_TXT, allowCloudRead: false, occurredAt: '2026-06-23T08:01:00.000Z' });

    // 第 1 轮：cloud tier（stub 不带 tier → 缺省 cloud）。eCloud 消化+覆盖；eObs 被挡、留 pending。
    const r1 = await distill('owner', { evidenceStore: ev, eventStore: evt, llm: makeStub('事件1').stub });
    assert.ok(r1.event, 'R1 建事件（cloud 可读的 eCloud）');
    assert.equal(r1.tierBlockedCount, 1, 'R1 有 1 条 observed 被 cloud tier 挡住');
    let covered = new Set(evt.coveredEvidenceIds('owner'));
    assert.ok(covered.has(eCloud.id) && !covered.has(eObs.id), 'eCloud 覆盖、eObs 留 pending（没被静默吞）');

    // 第 2 轮：换 local tier。eObs 现在可读 → 被补消化。
    const { stub, state } = makeStub('事件2', 'local');
    const r2 = await distill('owner', { evidenceStore: ev, eventStore: evt, llm: stub });
    assert.ok(r2.event, 'R2 换本地模型后 observed 被补消化、建事件');
    assert.ok(state.seen.includes(LOCAL_TXT), 'observed 原话进了本地模型 prompt');
    assert.equal(r2.tierBlockedCount, 0, 'R2 无挂账');
    covered = new Set(evt.coveredEvidenceIds('owner'));
    assert.ok(covered.has(eObs.id), 'observed 现在被覆盖（补消化成功）');
  } finally {
    ev.close(); evt.close();
  }
});

test('inference 门·distill：inference=false 的证据不进 event（即便 cloud=true）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  try {
    const eOk = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: CLOUD_TXT, allowCloudRead: true, allowInference: true, occurredAt: '2026-06-23T08:00:00.000Z' });
    const eNoInfer = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: LOCAL_TXT, allowCloudRead: true, allowInference: false, occurredAt: '2026-06-23T08:01:00.000Z' });
    const { stub, state } = makeStub('事件'); // cloud tier
    const r = await distill('owner', { evidenceStore: ev, eventStore: evt, llm: stub });
    assert.ok(state.seen.includes(CLOUD_TXT), 'inference=true 的进 prompt');
    assert.ok(!state.seen.includes(LOCAL_TXT), 'inference=false 的不进 prompt（即便 cloud=true）');
    assert.equal(r.tierBlockedCount, 0, 'inference=false 不计入 tierBlockedCount（读得到、只是不许推理）');
    const covered = new Set(evt.coveredEvidenceIds('owner'));
    assert.ok(covered.has(eOk.id) && !covered.has(eNoInfer.id), 'inference=false 不被覆盖、留 pending（可重开授权后消化）');
  } finally {
    ev.close(); evt.close();
  }
});

test('inference 门·tier=local 也拦：inference=false 的 observed 不进 event', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  try {
    // observed 但显式撤销推理授权：local=true / cloud=false / inference=false
    ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: LOCAL_TXT, allowCloudRead: false, allowInference: false, occurredAt: '2026-06-23T08:00:00.000Z' });
    const { stub, state } = makeStub('事件', 'local');
    const r = await distill('owner', { evidenceStore: ev, eventStore: evt, llm: stub });
    assert.equal(state.callCount, 0, 'inference=false → 无可消化材料，不调模型');
    assert.equal(r.event, null, '不建事件');
    assert.equal(r.tierBlockedCount, 0, 'inference=false 不算 tier 挡住（本地读得到，只是不许推理）');
  } finally {
    ev.close(); evt.close();
  }
});

test('consolidate·tier=local：observed(cloud=false, local=true) 进 prompt 且能当合法支撑（进画像）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const eObs = ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: LOCAL_TXT, allowCloudRead: false });
    evt.put({ subjectId: 'owner', summary: '一个事件', occurredAt: eObs.occurredAt, evidenceIds: [eObs.id] });
    const { stub, state } = makeStub(
      `{"new":[{"content":"用户的某条认知","content_type":"preference","formed_by":"stated","support_evidence_ids":["${eObs.id}"]}]}`,
      'local',
    );
    await consolidate('owner', { eventStore: evt, evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.ok(state.seen.includes(LOCAL_TXT), '本地模型能读 observed 原话 → 进 prompt');
    const created = cog.active('owner');
    assert.equal(created.length, 1, '生成 1 条认知（observed 进了画像）');
    const links = cog.sourcesOf(created[0]!.id);
    assert.ok(links.some((l) => l.evidenceId === eObs.id), 'observed 成了合法支撑');
  } finally {
    ev.close(); evt.close(); cog.close();
  }
});

test('inference 门·consolidate：inference=false 的证据不进 prompt、不成支撑（cloud tier）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const eOk = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: CLOUD_TXT, allowCloudRead: true, allowInference: true });
    const eNoInfer = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: LOCAL_TXT, allowCloudRead: true, allowInference: false });
    evt.put({ subjectId: 'owner', summary: '一个事件', occurredAt: eOk.occurredAt, evidenceIds: [eOk.id, eNoInfer.id] });
    const { stub, state } = makeStub(
      `{"new":[{"content":"用户的某条认知","content_type":"preference","formed_by":"stated","support_evidence_ids":["${eOk.id}","${eNoInfer.id}"]}]}`,
    );
    await consolidate('owner', { eventStore: evt, evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.ok(state.seen.includes(CLOUD_TXT), 'inference=true 的进 prompt');
    assert.ok(!state.seen.includes(LOCAL_TXT), 'inference=false 的没进 prompt（D4 行为变更：授权位真生效）');
    const created = cog.active('owner');
    assert.equal(created.length, 1, '生成 1 条认知');
    const links = cog.sourcesOf(created[0]!.id);
    assert.ok(links.some((l) => l.evidenceId === eOk.id), 'inference=true 证据成支撑');
    assert.ok(!links.some((l) => l.evidenceId === eNoInfer.id), 'inference=false 证据没成为支撑');
  } finally {
    ev.close(); evt.close(); cog.close();
  }
});
