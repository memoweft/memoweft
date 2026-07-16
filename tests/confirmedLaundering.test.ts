/**
 * 附和洗白 · 对抗测试（D-0033 Phase 1b —— 结构墙的核心靶）。
 *
 * 靶心:AI 诱导性提问风暴 + 用户连答"是的",不能被洗成可信画像。断言【哪怕提示词判漏,结构墙也拦得住】:
 *   ① 3a/3d:AI 上文永无证据 id → 附和认知只溯源到【用户那句真话】,助手输出永不成为可溯源证据;
 *   ② 封顶:纯附和(formedBy 恒 confirmed)攒再多支持也 ≤480 < limited,永不"有一定把握/稳定";
 *   ③ 聚合排除:confirmed 认知不进 trends 规则聚合 → 洗不成更可信的 ruled 趋势;
 *   ④ 升级门:只有【用户主动亲口(spoken)+ LLM 判 stated】才破顶 → 附和/观察/纯连答都升不上去。
 * 全离线(脚本 LLM 逐轮驱动,不依赖网络)。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import { aggregateTrends } from '../src/background/trends.ts';

interface Stores { ev: SqliteEvidenceStore; evt: SqliteEventStore; cog: SqliteCognitionStore; }
function fresh(): Stores {
  return { ev: new SqliteEvidenceStore(':memory:'), evt: new SqliteEventStore(':memory:'), cog: new SqliteCognitionStore(':memory:') };
}
function closeAll(s: Stores) { s.ev.close(); s.evt.close(); s.cog.close(); }

/** 造一条"孤儿附和"证据 + 覆盖它的事件;返回证据 id。 */
function assent(s: Stores, at: string, userWord: string, aiTurn: string): string {
  const e = s.ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: userWord, occurredAt: at, precedingAiContext: aiTurn });
  s.evt.put({ subjectId: 'u', summary: `用户对"${aiTurn}"回应"${userWord}"`, occurredAt: at, evidenceIds: [e.id] });
  return e.id;
}

// ── ① 3a/3d:AI 上文永不成为可溯源证据 ──

test('3a:附和认知只溯源到【用户那句真话】,AI 上文/任何非证据 id 都被 support 白名单挡掉', async () => {
  const s = fresh();
  try {
    const eId = assent(s, '2026-06-01T08:00:00.000Z', '是的', '你喜欢爬山吧?');
    // LLM 既引真证据 id,又【企图】把 AI 上文当来源引一个捏造 id → 后者必须被 pickSupport 滤掉
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"new":[{"content":"用户喜欢爬山","content_type":"preference","formed_by":"confirmed","support_evidence_ids":["${eId}","ai-context-fabricated-id"]}]}`; } };
    const r = await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub });
    assert.equal(r.created.length, 1, '产出一条附和认知');
    const c = r.created[0]!;
    assert.equal(c.formedBy, 'confirmed', '附和 → formed_by=confirmed');
    const sources = s.cog.sourcesOf(c.id);
    assert.equal(sources.length, 1, '只挂 1 条来源(捏造 id 被白名单滤掉)');
    assert.equal(sources[0]!.evidenceId, eId, '来源是【用户那句真话】的证据 id');
    // 每条来源都能在证据表查到真行,且没有一条内容是那句 AI 话 → 助手输出没成为证据
    for (const link of sources) {
      const row = s.ev.get(link.evidenceId);
      assert.ok(row, '来源指向真实证据行');
      assert.notEqual(row!.rawContent, '你喜欢爬山吧?', 'AI 那句从未作为证据落库/被溯源');
    }
  } finally { closeAll(s); }
});

test('3a:一条附和只引到捏造的 AI-上文 id(无真证据支撑)→ 不产出无溯源认知', async () => {
  const s = fresh();
  try {
    assent(s, '2026-06-01T08:00:00.000Z', '嗯', '你其实很内向对吧?');
    // LLM 只引一个不存在的 id(模拟"把 AI 上文当唯一来源")→ pickSupport 空 → 跳过、不落库
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"new":[{"content":"用户很内向","content_type":"trait","formed_by":"confirmed","support_evidence_ids":["ai-context-only-fake"]}]}`; } };
    const r = await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub });
    assert.equal(r.created.length, 0, '无真证据支撑 → 不产出');
    assert.equal(s.cog.all('u').length, 0, '库里没有凭 AI 上文硬编的认知');
  } finally { closeAll(s); }
});

// ── ② 封顶:纯附和攒再多也爬不上 limited/stable ──

test('封顶:诱导风暴 + 连答"是的"(LLM 恒判 confirmed)→ 攒 8 轮支持仍 ≤480、永不 limited/stable', async () => {
  const s = fresh();
  let cogId = '';
  try {
    // 第 0 轮:附和产出 confirmed 认知
    const e0 = assent(s, '2026-06-01T00:00:00.000Z', '是的', '你喜欢爬山吧?');
    const stub0 = { callCount: 0, async chat() { this.callCount++; return `{"new":[{"content":"用户喜欢爬山","content_type":"preference","formed_by":"confirmed","support_evidence_ids":["${e0}"]}]}`; } };
    const r0 = await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub0 });
    cogId = r0.created[0]!.id;
    assert.equal(s.cog.get(cogId)!.formedBy, 'confirmed');

    // 第 1..8 轮:每轮新一句"是的",LLM 强化同一条认知(不标 stated = 纯附和)
    for (let i = 1; i <= 8; i++) {
      const eI = assent(s, `2026-06-0${i + 1}T00:00:00.000Z`, '是的', `你还是喜欢爬山吧?(第${i}次)`);
      const stub = { callCount: 0, async chat() { this.callCount++; return `{"reinforce":[{"cognition_id":"${cogId}","support_evidence_ids":["${eI}"]}]}`; } };
      await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub });
    }

    const c = s.cog.get(cogId)!;
    assert.equal(c.formedBy, 'confirmed', '纯附和 → formed_by 始终 confirmed（没被洗成 stated）');
    assert.ok(c.confidence <= 480, `纯附和封顶 ≤480（实际 ${c.confidence}）`);
    assert.ok(c.credStatus !== 'limited' && c.credStatus !== 'stable', `永不 limited/stable（实际 ${c.credStatus}）`);
  } finally { closeAll(s); }
});

// ── ③ 聚合排除:confirmed 不进 trends ──

test('聚合排除:一堆 confirmed 的 state 认知 → trends 规则聚合把它们全排除,不洗成 ruled 趋势', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  const now = new Date('2026-06-30T00:00:00.000Z');
  try {
    // 5 条"附和产出"的 state 认知（formedBy=confirmed），各挂窗口内证据 —— 频次远超 trendMinCount
    for (let i = 0; i < 5; i++) {
      const e = ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: '是的', occurredAt: `2026-06-2${i}T08:00:00.000Z`, precedingAiContext: '你最近是不是很累?' });
      cog.put({ subjectId: 'u', content: '用户最近很累', contentType: 'state', formedBy: 'confirmed', confidence: 280, credStatus: 'candidate', evidence: [{ evidenceId: e.id, relation: 'support' }] });
    }
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"trends":[{"content":"用户最近持续疲惫","based_on_evidence_ids":[]}]}`; } };
    const r = await aggregateTrends('u', { evidenceStore: ev, cognitionStore: cog, llm: stub }, now);
    assert.equal(r.trends.length, 0, 'confirmed 的 state 被排除 → 聚不出趋势');
    assert.equal(stub.callCount, 0, 'confirmed 全排除后不够频 → 根本不调模型（诱导风暴洗不成 ruled）');
  } finally { ev.close(); cog.close(); }
});

// ── ④ 升级门:只有【用户主动亲口 + LLM 判 stated】才破顶 ──

test('升级:confirmed 认知被【用户主动亲口(spoken)+ LLM 判 stated】印证 → 升 stated、破 480 封顶', async () => {
  const s = fresh();
  try {
    // 先有一条附和产的 confirmed 认知
    const e0 = assent(s, '2026-06-01T00:00:00.000Z', '是的', '你喜欢爬山吧?');
    const stub0 = { callCount: 0, async chat() { this.callCount++; return `{"new":[{"content":"用户喜欢爬山","content_type":"preference","formed_by":"confirmed","support_evidence_ids":["${e0}"]}]}`; } };
    const cogId = (await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub0 })).created[0]!.id;
    assert.equal(s.cog.get(cogId)!.formedBy, 'confirmed');
    assert.ok(s.cog.get(cogId)!.confidence <= 480);

    // 用户【主动亲口】自我披露（spoken、无 AI 上文），LLM 把这次强化判为 stated
    const e1 = s.ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: '我其实特别喜欢爬山,每个周末都去', occurredAt: '2026-06-05T00:00:00.000Z' });
    s.evt.put({ subjectId: 'u', summary: '用户主动说很喜欢爬山', occurredAt: e1.occurredAt, evidenceIds: [e1.id] });
    const stub1 = { callCount: 0, async chat() { this.callCount++; return `{"reinforce":[{"cognition_id":"${cogId}","support_evidence_ids":["${e1.id}"],"formed_by":"stated"}]}`; } };
    await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub1 });

    const c = s.cog.get(cogId)!;
    assert.equal(c.formedBy, 'stated', '主动亲口印证 → 升级 stated');
    assert.ok(c.confidence > 480, `破 480 封顶（实际 ${c.confidence}）`);
    assert.ok(c.credStatus === 'limited' || c.credStatus === 'stable', `升上 limited/stable（实际 ${c.credStatus}）`);
  } finally { closeAll(s); }
});

test('升级门 · 护栏:LLM 判 stated 但支撑原话【非 spoken(observed)】→ 不升级(observed 永不能升 stated)', async () => {
  const s = fresh();
  try {
    const e0 = assent(s, '2026-06-01T00:00:00.000Z', '是的', '你喜欢爬山吧?');
    const stub0 = { callCount: 0, async chat() { this.callCount++; return `{"new":[{"content":"用户喜欢爬山","content_type":"preference","formed_by":"confirmed","support_evidence_ids":["${e0}"]}]}`; } };
    const cogId = (await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub0 })).created[0]!.id;

    // observed 证据（显式开 cloud 读,好过 tier 门进 validEvidence);LLM 谎标 stated
    const e1 = s.ev.put({ subjectId: 'u', sourceKind: 'observed', hostId: 'h', rawContent: '观察到用户周末在山里', occurredAt: '2026-06-05T00:00:00.000Z', allowCloudRead: true, allowInference: true });
    s.evt.put({ subjectId: 'u', summary: '观察到爬山行为', occurredAt: e1.occurredAt, evidenceIds: [e1.id] });
    const stub1 = { callCount: 0, async chat() { this.callCount++; return `{"reinforce":[{"cognition_id":"${cogId}","support_evidence_ids":["${e1.id}"],"formed_by":"stated"}]}`; } };
    await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub1 });

    const c = s.cog.get(cogId)!;
    assert.equal(c.formedBy, 'confirmed', '无 spoken 支撑 → 结构护栏挡住升级,保持 confirmed');
    assert.ok(c.confidence <= 480, `仍封顶 ≤480（实际 ${c.confidence}）`);
  } finally { closeAll(s); }
});

test('升级门:纯连答"是的"强化(LLM 未标 stated)→ 不升级(Phase 1b 缺省不升,须 Phase 2 提示词教标)', async () => {
  const s = fresh();
  try {
    const e0 = assent(s, '2026-06-01T00:00:00.000Z', '是的', '你喜欢爬山吧?');
    const stub0 = { callCount: 0, async chat() { this.callCount++; return `{"new":[{"content":"用户喜欢爬山","content_type":"preference","formed_by":"confirmed","support_evidence_ids":["${e0}"]}]}`; } };
    const cogId = (await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub0 })).created[0]!.id;

    // 又一句 spoken"是的"（有 spoken 支撑,但 LLM 没标 stated）→ 判据①不满足 → 不升级
    const e1 = assent(s, '2026-06-05T00:00:00.000Z', '是的', '你真的喜欢爬山吧?');
    const stub1 = { callCount: 0, async chat() { this.callCount++; return `{"reinforce":[{"cognition_id":"${cogId}","support_evidence_ids":["${e1}"]}]}`; } };
    await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub1 });

    const c = s.cog.get(cogId)!;
    assert.equal(c.formedBy, 'confirmed', '未标 stated → 保持 confirmed（连答"是的"洗不上去）');
    assert.ok(c.confidence <= 480, `仍封顶 ≤480（实际 ${c.confidence}）`);
  } finally { closeAll(s); }
});
