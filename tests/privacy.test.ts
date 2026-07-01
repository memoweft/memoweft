/**
 * 隐私开关接线 · 离线护栏（4-A 前置修复，地图 cell 8 隐私规则）。
 *
 * 验收：写路径三处（distill / consolidate / attribute）把证据喂给（云端）LLM 前，
 * 按 allowCloudRead 过滤——cloud=false 的原话【不进 prompt】；cloud=true 的【照常进】。
 * 全用 stub LLM（捕获它收到的 messages），不依赖网络。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { filterCloudReadable } from '../src/evidence/privacy.ts';
import { distill } from '../src/distillation/distill.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import { attribute } from '../src/attribution/attribute.ts';
import type { ChatMessage } from '../src/llm/client.ts';

const CLOUD_TXT = '可以上云的话';
const LOCAL_TXT = '机密不许上云的话';

test('filterCloudReadable：只留 allowCloudRead=true，顺序保留', () => {
  const items = [
    { id: 'a', allowCloudRead: true },
    { id: 'b', allowCloudRead: false },
    { id: 'c', allowCloudRead: true },
  ];
  assert.deepEqual(filterCloudReadable(items).map((x) => x.id), ['a', 'c']);
});

test('distill：cloud=false 的原话不进喂给 LLM 的 prompt；cloud=true 的照常进', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  try {
    const eCloud = ev.put({
      subjectId: 'owner', sourceKind: 'spoken', hostId: 'h',
      rawContent: CLOUD_TXT, allowCloudRead: true, occurredAt: '2026-06-23T08:00:00.000Z',
    });
    const eLocal = ev.put({
      subjectId: 'owner', sourceKind: 'observed', hostId: 'h',
      rawContent: LOCAL_TXT, allowCloudRead: false, occurredAt: '2026-06-23T08:01:00.000Z',
    });
    let seen = '';
    const stub = {
      callCount: 0,
      async chat(msgs: ChatMessage[]): Promise<string> { this.callCount++; seen = JSON.stringify(msgs); return '一个事件摘要'; },
    };
    const r = await distill('owner', { evidenceStore: ev, eventStore: evt, llm: stub });
    assert.ok(seen.includes(CLOUD_TXT), 'cloud=true 的原话进了 prompt');
    assert.ok(!seen.includes(LOCAL_TXT), 'cloud=false 的原话没进 prompt');
    assert.ok(r.event, '仍产出事件');
    // 推荐方案落地：cloud=false 证据照样【算被事件覆盖】，不会每轮被重复重捞。
    const covered = new Set(evt.coveredEvidenceIds('owner'));
    assert.ok(covered.has(eCloud.id) && covered.has(eLocal.id), 'cloud=false 证据也算被覆盖（不重捞）');
  } finally {
    ev.close(); evt.close();
  }
});

test('consolidate：cloud=false 的原话不进 prompt、也进不了合法支撑', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const eCloud = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: CLOUD_TXT, allowCloudRead: true });
    const eLocal = ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: LOCAL_TXT, allowCloudRead: false });
    evt.put({ subjectId: 'owner', summary: '一个事件', occurredAt: eCloud.occurredAt, evidenceIds: [eCloud.id, eLocal.id] });
    let seen = '';
    const stub = {
      callCount: 0,
      // 即便 LLM 硬引 cloud=false 的 id，也应被合法集挡掉（不成为支撑）。
      async chat(msgs: ChatMessage[]): Promise<string> {
        this.callCount++; seen = JSON.stringify(msgs);
        return `{"new":[{"content":"用户的某条认知","content_type":"preference","formed_by":"stated","support_evidence_ids":["${eCloud.id}","${eLocal.id}"]}]}`;
      },
    };
    await consolidate('owner', { eventStore: evt, evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.ok(seen.includes(CLOUD_TXT), 'cloud=true 的原话进了 prompt');
    assert.ok(!seen.includes(LOCAL_TXT), 'cloud=false 的原话没进 prompt');
    const created = cog.active('owner');
    assert.equal(created.length, 1, '生成 1 条认知');
    const links = cog.sourcesOf(created[0]!.id);
    assert.ok(links.some((l) => l.evidenceId === eCloud.id), '挂了 cloud=true 证据当支撑');
    assert.ok(!links.some((l) => l.evidenceId === eLocal.id), 'cloud=false 证据没成为支撑');
  } finally {
    ev.close(); evt.close(); cog.close();
  }
});

test('attribute：cloud=false 的候选原因不进喂给 LLM 的 prompt', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const eSleep = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '昨晚没睡好', occurredAt: '2026-06-23T08:00:00.000Z' });
    const eSleep2 = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '还是没睡好', occurredAt: '2026-06-23T08:05:00.000Z' });
    // 两条候选原因：都 observed + 可推断；一条允许上云、一条不许。
    ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: CLOUD_TXT, allowCloudRead: true, allowInference: true, occurredAt: '2026-06-23T03:30:00.000Z' });
    ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: LOCAL_TXT, allowCloudRead: false, allowInference: true, occurredAt: '2026-06-23T03:35:00.000Z' });
    // 现象反复出现≥2 条支撑，过④门槛（minPhenomenonSupport）才会归因。
    cog.put({ subjectId: 'owner', content: '用户昨晚没睡好', contentType: 'state', formedBy: 'stated', confidence: 250, credStatus: 'low', evidence: [{ evidenceId: eSleep.id, relation: 'support' }, { evidenceId: eSleep2.id, relation: 'support' }] });
    let seen = '';
    const stub = {
      callCount: 0,
      async chat(msgs: ChatMessage[]): Promise<string> { this.callCount++; seen = JSON.stringify(msgs); return '{"hypotheses":[]}'; },
    };
    await attribute('owner', { evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(stub.callCount, 1, '有云端可读候选 → 调了模型');
    assert.ok(seen.includes(CLOUD_TXT), 'cloud=true 候选进了 prompt');
    assert.ok(!seen.includes(LOCAL_TXT), 'cloud=false 候选没进 prompt');
  } finally {
    ev.close(); cog.close();
  }
});
