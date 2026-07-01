/**
 * 隐私开关接线 · 离线护栏（4-A 前置修复 + 7-A Cloud Guard 验收，地图 cell 8 隐私规则）。
 *
 * 验收：所有【吃证据 + 调（云端）LLM】的写路径步骤，喂 prompt 前都按 allowCloudRead 过滤——
 * cloud=false 的原话【不进 prompt】、也进不了云端所授认知的支撑；cloud=true 的【照常进】。
 * 覆盖六处：distill / consolidate / attribute（核心三步）+ trends / proposeAsk / revisitConflicts（7-A 补齐）。
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
import { aggregateTrends } from '../src/background/trends.ts';
import { proposeAsk } from '../src/asking/proposeAsk.ts';
import { revisitConflicts } from '../src/asking/revisitConflicts.ts';
import type { ChatMessage } from '../src/llm/client.ts';

const CLOUD_TXT = '可以上云的话';
const LOCAL_TXT = '机密不许上云的话';
const LOCAL2_TXT = '另一条机密不许上云的话';

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

test('trends：cloud=false 状态证据不进云端 prompt、也进不了趋势支撑', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  const now = new Date('2026-06-30T00:00:00.000Z');
  try {
    // 3 条允许上云 + 1 条不许上云，各挂一条 state 认知（都在窗口内）。
    const cloudTexts = ['很烦', '又没睡好', '提不起劲'];
    const cloudIds: string[] = [];
    cloudTexts.forEach((t, i) => {
      const e = ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: t, allowCloudRead: true, occurredAt: `2026-06-2${i}T08:00:00.000Z` });
      cog.put({ subjectId: 'u', content: `用户${t}`, contentType: 'state', formedBy: 'stated', confidence: 250, credStatus: 'low', evidence: [{ evidenceId: e.id, relation: 'support' }] });
      cloudIds.push(e.id);
    });
    const eLocal = ev.put({ subjectId: 'u', sourceKind: 'observed', hostId: 'h', rawContent: LOCAL_TXT, allowCloudRead: false, occurredAt: '2026-06-23T08:00:00.000Z' });
    cog.put({ subjectId: 'u', content: '用户某机密状态', contentType: 'state', formedBy: 'observed', confidence: 250, credStatus: 'low', evidence: [{ evidenceId: eLocal.id, relation: 'support' }] });

    let seen = '';
    const stub = {
      callCount: 0,
      // LLM 硬引所有 id（含 cloud=false）——cloud=false 应被 windowEvidence 挡掉、进不了支撑。
      async chat(msgs: ChatMessage[]): Promise<string> {
        this.callCount++; seen = JSON.stringify(msgs);
        return `{"trends":[{"content":"用户最近持续情绪低落","based_on_evidence_ids":${JSON.stringify([...cloudIds, eLocal.id])}}]}`;
      },
    };
    const r = await aggregateTrends('u', { evidenceStore: ev, cognitionStore: cog, llm: stub }, now);
    assert.ok(seen.includes(cloudTexts[0]!), 'cloud=true 状态原话进了 prompt');
    assert.ok(!seen.includes(LOCAL_TXT), 'cloud=false 状态原话没进 prompt');
    assert.equal(r.trends.length, 1, '聚出 1 条趋势');
    const links = cog.sourcesOf(r.trends[0]!.id).map((l) => l.evidenceId);
    assert.ok(!links.includes(eLocal.id), 'cloud=false 证据没成为趋势支撑');
    assert.ok(cloudIds.every((id) => links.includes(id)), '3 条 cloud=true 证据都成了支撑');
  } finally {
    ev.close(); cog.close();
  }
});

test('proposeAsk：cloud=false 支撑不进云端提问 prompt；宿主展示保留完整', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const eCloud = ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: CLOUD_TXT, allowCloudRead: true });
    const eLocal = ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: LOCAL_TXT, allowCloudRead: false });
    // 低置信假设（可问，把握度落在 confidenceBand 内），挂两条支撑：一云一本地。
    cog.put({
      subjectId: 'owner', content: '用户可能因为熬夜导致没睡好', contentType: 'hypothesis', formedBy: 'inferred',
      confidence: 250, credStatus: 'low',
      evidence: [{ evidenceId: eCloud.id, relation: 'support' }, { evidenceId: eLocal.id, relation: 'support' }],
    });
    let seen = '';
    const stub = { callCount: 0, async chat(msgs: ChatMessage[]): Promise<string> { this.callCount++; seen = JSON.stringify(msgs); return '你是不是熬夜了？'; } };
    const r = await proposeAsk('owner', { cognitionStore: cog, evidenceStore: ev, llm: stub });
    assert.equal(r.proposals.length, 1, '产出 1 条提问建议');
    assert.equal(stub.callCount, 1, '调了措辞模型');
    assert.ok(seen.includes(CLOUD_TXT), 'cloud=true 证据进了提问 prompt');
    assert.ok(!seen.includes(LOCAL_TXT), 'cloud=false 证据没进提问 prompt');
    // 过滤只作用于喂云端的那份：返回给宿主展示的 evidence 仍是完整两条（展示归宿主）。
    const shownIds = r.proposals[0]!.evidence.map((e) => e.id);
    assert.ok(shownIds.includes(eCloud.id) && shownIds.includes(eLocal.id), '宿主展示保留完整两条证据');
  } finally {
    ev.close(); cog.close();
  }
});

test('revisitConflicts：cloud=false 的正/反证据都不进云端提问 prompt', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const eCloud = ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: CLOUD_TXT, allowCloudRead: true });
    const eLocalSup = ev.put({ subjectId: 'u', sourceKind: 'observed', hostId: 'h', rawContent: LOCAL_TXT, allowCloudRead: false });
    const eLocalCon = ev.put({ subjectId: 'u', sourceKind: 'observed', hostId: 'h', rawContent: LOCAL2_TXT, allowCloudRead: false });
    cog.put({
      subjectId: 'u', content: '用户喜欢喝茶', contentType: 'preference', formedBy: 'stated',
      confidence: 600, credStatus: 'conflicted',
      evidence: [
        { evidenceId: eCloud.id, relation: 'support' },
        { evidenceId: eLocalSup.id, relation: 'support' },
        { evidenceId: eLocalCon.id, relation: 'contradict' },
      ],
    });
    let seen = '';
    const stub = { callCount: 0, async chat(msgs: ChatMessage[]): Promise<string> { this.callCount++; seen = JSON.stringify(msgs); return '你现在到底更常喝哪种？'; } };
    const r = await revisitConflicts('u', { cognitionStore: cog, evidenceStore: ev, llm: stub });
    assert.equal(r.proposals.length, 1, '复看那条冲突');
    assert.equal(stub.callCount, 1, '调了措辞模型');
    assert.ok(seen.includes(CLOUD_TXT), 'cloud=true 证据进了提问 prompt');
    assert.ok(!seen.includes(LOCAL_TXT), 'cloud=false 支撑证据没进 prompt');
    assert.ok(!seen.includes(LOCAL2_TXT), 'cloud=false 反对证据没进 prompt');
  } finally {
    ev.close(); cog.close();
  }
});
