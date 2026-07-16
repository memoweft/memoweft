/**
 * 附和洗白 · 对抗测试（D-0033 Phase 1b —— 结构墙的核心靶）。
 *
 * 靶心:AI 诱导性提问风暴 + 用户连答"是的",不能被洗成可信画像。断言【哪怕提示词判漏,结构墙也拦得住】:
 *   ① 3a/3d:AI 上文永无证据 id → 附和认知只溯源到【用户那句真话】,助手输出永不成为可溯源证据;
 *   ② 封顶:纯附和(formedBy 恒 confirmed)攒再多支持也 ≤480 < limited,永不"有一定把握/稳定";
 *   ③ 聚合排除:confirmed 认知不进 trends 规则聚合 → 洗不成更可信的 ruled 趋势;
 *   ④ 并存(v0.6 Phase 3·D-0035 拍板③,原「升级门」):confirmed 认知**永不就地升级**——用户后来主动说
 *      才另起一条并存的 stated,旧的原样留档。附和/观察/纯连答都触发不了并存。
 *   ⑤ 取最弱(v0.6 Phase 3·D-0035 拍板②):一条认知引多条证据时按【最弱】的算 → 「附和 + 顺带引一条
 *      无关的主动陈述」洗不出 stated。**§15.3 全量 eval 对这条零鉴别力**(语料异质支持集 0/49,且真模型
 *      引哪些证据语料强制不了)——只能在这里钉。deriveFormedBy.test 从纯函数层钉,这里从 consolidate
 *      端到端钉(要穿过 pickSupport:它只查 id 白名单、**不查相关性**,正是攻击面所在)。
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

/** 造一条【用户主动说的】证据(无 AI 上文 → 载体维派生成 stated)+ 覆盖它的事件;返回证据 id。 */
function spontaneous(s: Stores, at: string, userWord: string): string {
  const e = s.ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: userWord, occurredAt: at });
  s.evt.put({ subjectId: 'u', summary: `用户主动说"${userWord}"`, occurredAt: at, evidenceIds: [e.id] });
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

// ── ④ 并存:confirmed 永不就地升级;用户主动说 → 另起一条 stated,旧的留档 ──

test('并存(D-0035 拍板③):confirmed 认知被【用户主动亲口】印证 → 不就地升级,而形成并存的新 stated 认知', async () => {
  // v0.6 Phase 3 前这里测的是【就地升级】(confirmed→stated 改写同一条)。人类拍板③ 取消了升级路：
  //   ① Phase 3 删掉 formed_by 的载体维指令后,原 gate①(LLM 自报 stated)的输入源消失、升级会默认失效
  //      = 悄悄推翻 D-0033 决定③；② 「就地改写一条认知的来源标签」本就与宪章「冲突只暴露不裁决」相悖。
  //   新语义：旧的 confirmed 原样留档,另起一条 stated —— 两条各自溯源清楚,读的人自己判断。
  const s = fresh();
  try {
    // 先有一条附和产的 confirmed 认知
    const e0 = assent(s, '2026-06-01T00:00:00.000Z', '是的', '你喜欢爬山吧?');
    const stub0 = { callCount: 0, async chat() { this.callCount++; return `{"new":[{"content":"用户喜欢爬山","content_type":"preference","formed_by":"confirmed","support_evidence_ids":["${e0}"]}]}`; } };
    const cogId = (await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub0 })).created[0]!.id;
    assert.equal(s.cog.get(cogId)!.formedBy, 'confirmed');
    assert.ok(s.cog.get(cogId)!.confidence <= 480);

    // 用户【主动亲口】自我披露（spoken、无 AI 上文 → 载体维派生成 stated）。
    //   注意 stub 不再喂 formed_by —— 那个字段已随升级路一并删除。
    const e1 = s.ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: '我其实特别喜欢爬山,每个周末都去', occurredAt: '2026-06-05T00:00:00.000Z' });
    s.evt.put({ subjectId: 'u', summary: '用户主动说很喜欢爬山', occurredAt: e1.occurredAt, evidenceIds: [e1.id] });
    const stub1 = { callCount: 0, async chat() { this.callCount++; return `{"reinforce":[{"cognition_id":"${cogId}","support_evidence_ids":["${e1.id}"]}]}`; } };
    const out = await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub1 });

    // 旧的 confirmed：原样留档，标签不被改写，仍在 480 封顶下
    const old = s.cog.get(cogId)!;
    assert.equal(old.formedBy, 'confirmed', '旧认知的来源标签不被就地改写');
    assert.ok(old.confidence <= 480, `旧认知仍在 480 封顶下（实际 ${old.confidence}）`);
    assert.ok(old.credStatus !== 'limited' && old.credStatus !== 'stable', '旧认知不进 limited/stable');

    // 并存：新起一条 stated，溯源到用户主动说的那条原话
    assert.equal(out.created.length, 1, '形成一条并存的新认知');
    const born = out.created[0]!;
    assert.notEqual(born.id, cogId, '是新的一条，不是改写旧的');
    assert.equal(born.formedBy, 'stated', '新认知 = stated（用户主动说的）');
    assert.ok(born.confidence > 480, `新认知破 480（实际 ${born.confidence}）`);
    assert.ok(s.cog.sourcesOf(born.id).some((l) => l.evidenceId === e1.id), '新认知溯源到用户主动说的那条原话');
  } finally { closeAll(s); }
});

test('并存 · 护栏:印证的原话是【行为观察】→ 不形成并存 stated(observed 永不算用户亲口)', async () => {
  // v0.6 Phase 3·D-0035：机制由「升级门」换成「并存」，但护栏语义原样——observed 不是用户亲口，
  //   载体维派生成 observed ≠ stated → 并存不触发。stub 里的 formed_by 已是**无效字段**（随升级路一并
  //   删除），这里刻意留着：正好证明模型**就算谎标 stated 也没用**，载体维由代码从证据算、不听它的。
  const s = fresh();
  try {
    const e0 = assent(s, '2026-06-01T00:00:00.000Z', '是的', '你喜欢爬山吧?');
    const stub0 = { callCount: 0, async chat() { this.callCount++; return `{"new":[{"content":"用户喜欢爬山","content_type":"preference","formed_by":"confirmed","support_evidence_ids":["${e0}"]}]}`; } };
    const cogId = (await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub0 })).created[0]!.id;

    // observed 证据（显式开 cloud 读,好过 tier 门进 validEvidence);LLM 谎标 stated
    const e1 = s.ev.put({ subjectId: 'u', sourceKind: 'observed', hostId: 'h', rawContent: '观察到用户周末在山里', occurredAt: '2026-06-05T00:00:00.000Z', allowCloudRead: true, allowInference: true });
    s.evt.put({ subjectId: 'u', summary: '观察到爬山行为', occurredAt: e1.occurredAt, evidenceIds: [e1.id] });
    const stub1 = { callCount: 0, async chat() { this.callCount++; return `{"reinforce":[{"cognition_id":"${cogId}","support_evidence_ids":["${e1.id}"],"formed_by":"stated"}]}`; } };
    const out = await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub1 });

    assert.equal(out.created.length, 0, 'observed 印证 → 不形成并存的 stated 认知');
    const c = s.cog.get(cogId)!;
    assert.equal(c.formedBy, 'confirmed', '旧认知保持 confirmed（结构护栏：observed 派生不出 stated）');
    assert.ok(c.confidence <= 480, `仍封顶 ≤480（实际 ${c.confidence}）`);
  } finally { closeAll(s); }
});

test('并存 · 护栏:又一句附和"是的"(带 AI 上文)→ 不形成并存,连答洗不上去', async () => {
  // v0.6 Phase 3·D-0035：这条在新机制下**更硬了**。旧世界靠 gate①「LLM 没把这次 reinforce 标成
  //   stated」挡住——那是**模型自觉**；现在靠**结构**：这句"是的"带 ⟨AI 前一句⟩ → 载体维派生成
  //   confirmed ≠ stated → 并存结构性不触发。模型标什么都无所谓，它已经没有这个字段了。
  const s = fresh();
  try {
    const e0 = assent(s, '2026-06-01T00:00:00.000Z', '是的', '你喜欢爬山吧?');
    const stub0 = { callCount: 0, async chat() { this.callCount++; return `{"new":[{"content":"用户喜欢爬山","content_type":"preference","formed_by":"confirmed","support_evidence_ids":["${e0}"]}]}`; } };
    const cogId = (await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub0 })).created[0]!.id;

    // 又一句 spoken"是的"——它同样带 AI 上文 → 载体维仍是 confirmed → 并存不触发
    const e1 = assent(s, '2026-06-05T00:00:00.000Z', '是的', '你真的喜欢爬山吧?');
    const stub1 = { callCount: 0, async chat() { this.callCount++; return `{"reinforce":[{"cognition_id":"${cogId}","support_evidence_ids":["${e1}"]}]}`; } };
    const out = await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub1 });

    assert.equal(out.created.length, 0, '又一句附和 → 不形成并存的 stated 认知');
    const c = s.cog.get(cogId)!;
    assert.equal(c.formedBy, 'confirmed', '保持 confirmed（连答"是的"洗不上去）');
    assert.ok(c.confidence <= 480, `仍封顶 ≤480（实际 ${c.confidence}）`);
  } finally { closeAll(s); }
});

// ── ⑤ 取最弱:多证据洗白防线（v0.6 Phase 3·D-0035 拍板②）──

test('反洗白 · 多证据:附和 + 顺带引一条无关的主动陈述 → 取最弱,仍是 confirmed', async () => {
  // 这条攻击【不需要恶意模型】：pickSupport(consolidate.ts) 只查 id 白名单、**不查相关性**，而
  //   validEvidence 覆盖整批（生产 batchSize=12 轮对话）——一个「过度引用」的模型（LLM 常见失败模式）
  //   就足以把同批里一条无关的用户主动陈述算进支撑。
  //   若取最强：得 stated → 600 + 40 = 640 ≥ limited(500)。取最弱把这条堵死。
  const s = fresh();
  try {
    const e0 = assent(s, '2026-06-01T00:00:00.000Z', '是的', '你是不是特别不爱打电话?');
    const e1 = spontaneous(s, '2026-06-01T00:01:00.000Z', '我最近在学法语');
    // 模型过度引用 + 谎标 stated（formed_by 的载体维部分已不被采信，这里刻意留着证明这点）
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"new":[{"content":"用户不爱打电话","content_type":"preference","formed_by":"stated","support_evidence_ids":["${e0}","${e1}"]}]}`; } };
    const out = await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub });

    assert.equal(out.created.length, 1);
    const c = out.created[0]!;
    assert.equal(c.formedBy, 'confirmed', '取最弱：支持集里有一条是附和 → 整条按附和算（取最强会给 stated）');
    assert.ok(c.confidence <= 480, `不破 480 封顶（取最强会给 640；实际 ${c.confidence}）`);
    assert.ok(c.credStatus !== 'limited' && c.credStatus !== 'stable', `不进 limited/stable（实际 ${c.credStatus}）`);
  } finally { closeAll(s); }
});

test('反洗白 · 多证据:附和 + 五条无关主动陈述(取最强会到 800/stable)→ 仍是 confirmed', async () => {
  // supportCap=5、supportStep=40 → 取最强时 600+200=800 ≥ stable(750)：一条附和能被洗成「稳定事实」。
  const s = fresh();
  try {
    const e0 = assent(s, '2026-06-01T00:00:00.000Z', '对', '你不太爱去人多的酒吧吧?');
    const many = [1, 2, 3, 4, 5].map((i) => spontaneous(s, `2026-06-01T00:0${i}:00.000Z`, `无关的主动陈述 ${i}`));
    const ids = [e0, ...many].map((id) => `"${id}"`).join(',');
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"new":[{"content":"用户不爱去人多的酒吧","content_type":"preference","formed_by":"stated","support_evidence_ids":[${ids}]}]}`; } };
    const out = await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub });

    const c = out.created[0]!;
    assert.equal(c.formedBy, 'confirmed', '证据再多也不改变「有一环是附和」这个事实');
    assert.ok(c.confidence <= 480, `不破 480（取最强会给 800/stable；实际 ${c.confidence}）`);
    assert.ok(c.credStatus !== 'limited' && c.credStatus !== 'stable', `不进 limited/stable（实际 ${c.credStatus}）`);
  } finally { closeAll(s); }
});

test('对照:全是【用户主动说的】证据 → 正常判 stated（取最弱不是无脑降级）', async () => {
  // 防「取最弱」被误实现成「一律压到最低」——它只在支持集里【真有】更弱的一环时才降。
  const s = fresh();
  try {
    const e1 = spontaneous(s, '2026-06-01T00:00:00.000Z', '我周末基本都去爬山');
    const e2 = spontaneous(s, '2026-06-01T00:01:00.000Z', '爬了七八年了');
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"new":[{"content":"用户周末常去爬山","content_type":"preference","formed_by":"stated","support_evidence_ids":["${e1}","${e2}"]}]}`; } };
    const out = await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: stub });

    const c = out.created[0]!;
    assert.equal(c.formedBy, 'stated', '两条都是用户主动说的 → 取最弱仍是 stated');
    assert.ok(c.confidence > 480, `该破 480（实际 ${c.confidence}）`);
  } finally { closeAll(s); }
});
