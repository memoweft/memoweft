/**
 * 阶段 3 离线护栏（地图 cell 15）：M4 归因 + M5 带证据主动询问 + 接回纠正闭环。
 * 全用 stub LLM，不依赖网络。验收场景：游戏到3:30(observed) → 没睡好 → 推假设 → 带证据问 → 用户否定 → 修正。
 * 治慢④（2026-07-01）：现象要【攒够≥minPhenomenonSupport 条支撑】才归因——本文件现象都挂 ≥2 条。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { attribute } from '../src/attribution/attribute.ts';
import { proposeAsk } from '../src/asking/proposeAsk.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import { config } from '../src/config.ts';

/** 搭好"没睡好"现象（反复出现≥2 条支撑，过④门槛）+ "游戏到3:30"观察证据，返回 store 与关键 id。 */
function setupScenario() {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  // 现象证据：用户【反复】说"没睡好"（≥2 条，满足④攒够门槛；时间相近，anchor 不越过 eGame）。
  const eSleep = ev.put({
    subjectId: 'owner', sourceKind: 'spoken', hostId: 'h',
    rawContent: '昨晚没睡好', occurredAt: '2026-06-23T08:00:00.000Z',
  });
  const eSleep2 = ev.put({
    subjectId: 'owner', sourceKind: 'spoken', hostId: 'h',
    rawContent: '还是没睡好', occurredAt: '2026-06-23T08:05:00.000Z',
  });
  // 观察证据：凌晨 3:30 游戏还开着（时间窗内、可推断）
  const eGame = ev.put({
    subjectId: 'owner', sourceKind: 'observed', hostId: 'h',
    rawContent: '游戏开到凌晨 3:30', occurredAt: '2026-06-23T03:30:00.000Z',
  });
  // 现象认知（state），挂两条"没睡好"证据（反复出现）
  const phenom = cog.put({
    subjectId: 'owner', content: '用户昨晚没睡好', contentType: 'state', formedBy: 'stated',
    confidence: 250, credStatus: 'low',
    evidence: [{ evidenceId: eSleep.id, relation: 'support' }, { evidenceId: eSleep2.id, relation: 'support' }],
  });
  return { ev, cog, eSleep, eGame, phenom };
}

test('M4 归因：现象 + 时间窗观察证据 → 低置信可解释假设（挂证据、可推翻）', async () => {
  const { ev, cog, eGame } = setupScenario();
  try {
    const stub = {
      callCount: 0,
      async chat() {
        this.callCount++;
        return `{"hypotheses":[{"content":"可能因为玩游戏太晚导致没睡好","based_on_evidence_ids":["${eGame.id}"]}]}`;
      },
    };
    const r = await attribute('owner', { evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r.hypotheses.length, 1, '产出 1 条假设');
    const h = r.hypotheses[0]!.cognition;
    assert.equal(h.contentType, 'hypothesis');
    assert.equal(h.formedBy, 'inferred', '假设是推测');
    assert.ok(h.confidence <= config.attribution.hypothesisCap, `低置信封顶 ≤${config.attribution.hypothesisCap}（实际 ${h.confidence}）`);
    assert.ok(['candidate', 'low'].includes(h.credStatus), '只敢低声说');
    assert.ok(cog.sourcesOf(h.id).some((l) => l.evidenceId === eGame.id), '挂着观察证据可溯源');
  } finally {
    ev.close(); cog.close();
  }
});

test('M4 归因：支撑精简 —— 现象积累一堆杂证据，假设也只挂 ≤2 原因 + 1 锚点（防爆炸）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    // 现象"没睡好"被污染：挂了 5 条乱七八糟的支撑证据（模拟事件覆盖多原话的老毛病）。
    const junk = ['我今年26岁', '我喜欢喝茶', '一般单身怎么找女朋友', '喜欢剑来', '昨晚没睡好'].map((t, i) =>
      ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: t, occurredAt: `2026-06-23T08:0${i}:00.000Z` }),
    );
    const eGame = ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: '游戏开到凌晨3:30', occurredAt: '2026-06-23T03:30:00.000Z' });
    cog.put({ subjectId: 'owner', content: '用户昨晚没睡好', contentType: 'state', formedBy: 'stated', confidence: 250, credStatus: 'low', evidence: junk.map((e) => ({ evidenceId: e.id, relation: 'support' as const })) });
    // LLM 乱引 3 条原因（含 2 条 junk-看着像原因的），应被硬封顶到 2，且 junk 中的 state 证据不在候选里。
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"hypotheses":[{"content":"可能因为玩游戏太晚导致没睡好","based_on_evidence_ids":["${eGame.id}","${eGame.id}","fake"]}]}`; } };
    const r = await attribute('owner', { evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r.hypotheses.length, 1);
    const links = cog.sourcesOf(r.hypotheses[0]!.cognition.id);
    assert.ok(links.length <= 3, `支撑精简（≤2 原因+1 锚点，实际 ${links.length}）`);
    assert.ok(links.some((l) => l.evidenceId === eGame.id), '原因证据在');
  } finally {
    ev.close(); cog.close();
  }
});

test('M4 归因：现象之外无可推断证据 → 不硬编假设；已归因 → 不重复', async () => {
  const { ev, cog, eGame } = setupScenario();
  try {
    let calls = 0;
    const stub = {
      callCount: 0,
      async chat() { this.callCount++; calls++; return `{"hypotheses":[{"content":"可能因为玩游戏太晚导致没睡好","based_on_evidence_ids":["${eGame.id}"]}]}`; },
    };
    const r1 = await attribute('owner', { evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r1.hypotheses.length, 1);
    // 再跑：现象已被假设引用过 → 跳过、不再调模型
    const r2 = await attribute('owner', { evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r2.hypotheses.length, 0, '已归因不重复产假设');
    assert.equal(calls, 1, '已归因现象不再调模型');
  } finally {
    ev.close(); cog.close();
  }
});

test('M4 归因：LLM 编造不存在的证据 id → 该假设被丢弃（防自证/幻觉）', async () => {
  const { ev, cog } = setupScenario();
  try {
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"hypotheses":[{"content":"瞎猜的","based_on_evidence_ids":["ev-不存在"]}]}`; } };
    const r = await attribute('owner', { evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r.hypotheses.length, 0, '没引用真实证据的假设不采纳');
  } finally {
    ev.close(); cog.close();
  }
});

test('M4 归因：禁 state→state —— 没有行为/观察证据时不拿"另一个抱怨"硬解释', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    // 两个 state 现象，各【反复出现】≥2 条（满足④门槛才进循环）；证据全是主观感受、无任何 observed/行为证据。
    const eSleep = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '昨晚没睡好', occurredAt: '2026-06-23T08:00:00.000Z' });
    const eSleep2 = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '还是没睡好', occurredAt: '2026-06-23T08:05:00.000Z' });
    const eAnnoyed = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '有点烦', occurredAt: '2026-06-23T08:10:00.000Z' });
    const eAnnoyed2 = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '还是很烦', occurredAt: '2026-06-23T08:15:00.000Z' });
    cog.put({ subjectId: 'owner', content: '用户昨晚没睡好', contentType: 'state', formedBy: 'stated', confidence: 250, credStatus: 'low', evidence: [{ evidenceId: eSleep.id, relation: 'support' }, { evidenceId: eSleep2.id, relation: 'support' }] });
    cog.put({ subjectId: 'owner', content: '用户感到烦', contentType: 'state', formedBy: 'stated', confidence: 250, credStatus: 'low', evidence: [{ evidenceId: eAnnoyed.id, relation: 'support' }, { evidenceId: eAnnoyed2.id, relation: 'support' }] });
    const stub = { callCount: 0, async chat() { this.callCount++; return '{"hypotheses":[]}'; } };
    const r = await attribute('owner', { evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r.hypotheses.length, 0, '无行为/观察原因 → 不产假设');
    assert.equal(stub.callCount, 0, '没有候选原因，根本不调模型（不硬编）');
  } finally {
    ev.close(); cog.close();
  }
});

test('M4 归因：一次只归因最近一个现象（maxPhenomenaPerRun=1，防爆炸）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const eGame = ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: '游戏开到凌晨3:30', occurredAt: '2026-06-23T03:30:00.000Z' });
    // 两现象各【反复≥2 条】满足④门槛；maxPhenomenaPerRun=1 → 仍只归因最近一个。
    const eSleep = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '昨晚没睡好', occurredAt: '2026-06-23T08:00:00.000Z' });
    const eSleep2 = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '还是没睡好', occurredAt: '2026-06-23T08:05:00.000Z' });
    const eAnnoyed = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '有点烦', occurredAt: '2026-06-23T08:10:00.000Z' });
    const eAnnoyed2 = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '还是很烦', occurredAt: '2026-06-23T08:15:00.000Z' });
    cog.put({ subjectId: 'owner', content: '用户昨晚没睡好', contentType: 'state', formedBy: 'stated', confidence: 250, credStatus: 'low', evidence: [{ evidenceId: eSleep.id, relation: 'support' }, { evidenceId: eSleep2.id, relation: 'support' }] });
    cog.put({ subjectId: 'owner', content: '用户感到烦', contentType: 'state', formedBy: 'stated', confidence: 250, credStatus: 'low', evidence: [{ evidenceId: eAnnoyed.id, relation: 'support' }, { evidenceId: eAnnoyed2.id, relation: 'support' }] });
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"hypotheses":[{"content":"可能因为玩游戏太晚导致没睡好","based_on_evidence_ids":["${eGame.id}"]}]}`; } };
    const r = await attribute('owner', { evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r.consideredPhenomena, 1, '一次只考察 1 个现象');
    assert.ok(r.hypotheses.length <= 1, '最多产 1 条（不爆炸）');
    assert.equal(stub.callCount, 1, '只对 1 个现象调模型');
  } finally {
    ev.close(); cog.close();
  }
});

test('M4 归因④治脑补：现象只 1 条支撑（偶发一次）→ 不归因、不调模型（不脑补）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    // 只说了一次"好累" + 有个游戏观察；现象仅 1 条支撑 < minPhenomenonSupport → 不该急着推因果。
    const eTired = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '好累', occurredAt: '2026-06-23T08:00:00.000Z' });
    ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: '游戏开到凌晨3:30', occurredAt: '2026-06-23T03:30:00.000Z' });
    cog.put({ subjectId: 'owner', content: '用户觉得累', contentType: 'state', formedBy: 'stated', confidence: 250, credStatus: 'low', evidence: [{ evidenceId: eTired.id, relation: 'support' }] });
    const stub = { callCount: 0, async chat() { this.callCount++; return '{"hypotheses":[{"content":"可能熬夜导致","based_on_evidence_ids":[]}]}'; } };
    const r = await attribute('owner', { evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r.consideredPhenomena, 0, '现象没攒够，根本不考察');
    assert.equal(r.hypotheses.length, 0, '偶发一次不推因果');
    assert.equal(stub.callCount, 0, '不调模型（省钱 + 不脑补）');
    assert.ok(config.attribution.minPhenomenonSupport >= 2, '④门槛 N≥2');
  } finally {
    ev.close(); cog.close();
  }
});

test('M5 询问：低置信假设 → 带证据提问 + 把握度透明 + 标已问去重', async () => {
  const { ev, cog, eGame } = setupScenario();
  try {
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"hypotheses":[{"content":"可能因为玩游戏太晚导致没睡好","based_on_evidence_ids":["${eGame.id}"]}]}`; } };
    await attribute('owner', { evidenceStore: ev, cognitionStore: cog, llm: stub });

    // 不带 llm → 模板问法
    const r = await proposeAsk('owner', { cognitionStore: cog, evidenceStore: ev });
    assert.equal(r.proposals.length, 1, '挑出 1 个该问的假设');
    const p = r.proposals[0]!;
    assert.ok(p.question.includes('没睡好'), '问法带上假设');
    assert.ok(p.evidence.some((e) => e.summary.includes('3:30')), '带证据问（可证伪）');
    assert.ok(p.confidence > 0 && p.credStatus, '把握度透明给宿主（规则 7）');
    // 标了 askedAt → 再问不重复（不烦用户）
    const r2 = await proposeAsk('owner', { cognitionStore: cog, evidenceStore: ev });
    assert.equal(r2.proposals.length, 0, '问过的不再问');
  } finally {
    ev.close(); cog.close();
  }
});

test('接回闭环：用户回答否定假设 → consolidate correct → 旧假设失效、新事实采纳', async () => {
  const { ev, cog, eGame } = setupScenario();
  const evt = new SqliteEventStore(':memory:');
  try {
    // 先有一条归因假设
    const stub1 = { callCount: 0, async chat() { this.callCount++; return `{"hypotheses":[{"content":"可能因为玩游戏太晚导致没睡好","based_on_evidence_ids":["${eGame.id}"]}]}`; } };
    const ar = await attribute('owner', { evidenceStore: ev, cognitionStore: cog, llm: stub1 });
    const hypoId = ar.hypotheses[0]!.cognition.id;

    // 用户回答："没有，只是挂机，和女友打电话" → 证据 → 事件
    const eAns = ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '没有，那会儿只是挂机，我在和女友打电话' });
    const answerEvent = evt.put({ subjectId: 'owner', summary: '用户澄清：昨晚游戏只是挂机，本人在和女友打电话，没真玩', occurredAt: eAns.occurredAt, evidenceIds: [eAns.id] });

    // consolidate 看到画像里的假设 + 用户澄清事件 → 判 correct（否定假设、采纳事实）
    const stub2 = {
      callCount: 0,
      async chat() {
        this.callCount++;
        return `{"correct":[{"cognition_id":"${hypoId}","content":"用户昨晚是挂机并和女友打电话，并非玩游戏","content_type":"fact","formed_by":"stated","support_evidence_ids":["${eAns.id}"]}]}`;
      },
    };
    const cr = await consolidate('owner', { eventStore: evt, evidenceStore: ev, cognitionStore: cog, llm: stub2 });
    assert.equal(cr.corrected, 1, '走纠正闭环');
    assert.ok(cog.get(hypoId)?.invalidAt, '旧假设标失效保留（可溯源）');
    const active = cog.active('owner');
    assert.ok(!active.some((c) => c.id === hypoId), '失效假设不再活跃');
    assert.ok(active.some((c) => c.content.includes('挂机')), '采纳用户澄清的新事实');
  } finally {
    ev.close(); cog.close(); evt.close();
  }
});

/** 按队列依次吐回复的假模型；callCount 由外部读取（同 attribute/trends 里 deps.llm.callCount 的用法）。 */
function queueStub(replies: string[]): { callCount: number; chat(): Promise<string> } {
  let i = 0;
  return {
    callCount: 0,
    async chat() {
      this.callCount++;
      return replies[i++] ?? '';
    },
  };
}

test('T3 归因容错解析：脏 JSON（带围栏 + 前后废话）一次解出、llm 只调 1 次', async () => {
  const { ev, cog, eGame } = setupScenario();
  try {
    // 脏 JSON 会被 parseJsonObject 的容错一次解掉（走不到重试）——这条测的是容错，不是重试。
    const dirty =
      '好的，结果如下：\n```json\n' +
      `{"hypotheses":[{"content":"可能因为玩游戏太晚导致没睡好","based_on_evidence_ids":["${eGame.id}"]}]}` +
      '\n```\n（以上）';
    const stub = queueStub([dirty]);
    const r = await attribute('owner', { evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r.hypotheses.length, 1, '脏 JSON 也能直接解出假设');
    assert.equal(stub.callCount, 1, '容错一次解掉，未触发重试');
    assert.equal(r.llmCalls, 1, 'llmCalls 计入 1 次');
  } finally {
    ev.close(); cog.close();
  }
});

test('T3 归因真重试：首次无花括号纯文字、二次合法 JSON → 产出假设、llm 调 2 次', async () => {
  const { ev, cog, eGame } = setupScenario();
  try {
    const stub = queueStub([
      '我不知道',
      `{"hypotheses":[{"content":"可能因为玩游戏太晚导致没睡好","based_on_evidence_ids":["${eGame.id}"]}]}`,
    ]);
    const r = await attribute('owner', { evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r.hypotheses.length, 1, '重试拿到合法 JSON 后产出假设');
    assert.equal(stub.callCount, 2, '触发了一次重试');
    assert.equal(r.llmCalls, 2, 'llmCalls 计入重试的 2 次');
  } finally {
    ev.close(); cog.close();
  }
});

test('T3 归因降级：连坏两次 → 空产出、不抛错', async () => {
  const { ev, cog } = setupScenario();
  try {
    const stub = queueStub(['我不知道', '还是不知道']);
    const r = await attribute('owner', { evidenceStore: ev, cognitionStore: cog, llm: stub });
    assert.equal(r.hypotheses.length, 0, '两次都坏 → 降级为空');
    assert.equal(stub.callCount, 2, '最多重试一次（共 2 次）');
    assert.equal(r.llmCalls, 2, 'llmCalls 计入 2 次');
  } finally {
    ev.close(); cog.close();
  }
});
