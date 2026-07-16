/**
 * 附和 / AI 上下文机制 —— 结构层 plumbing 与「结构无泄漏」（D-0033 Phase 1b）。
 *
 * 覆盖:证据列 preceding_ai_context 只写不读 + 缺列补迁移 + conversation 捕获 + distill/consolidate 注入
 * (经隐私门) + exportBundle/listEvidence 结构性拿不到 AI 上文。对抗/洗白见 confirmedLaundering.test.ts。
 * 全离线(伪 llm/retriever)。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { DatabaseSync } from '../src/store/nodeSqliteDriver.ts';
import { Conversation } from '../src/pipeline/conversation.ts';
import { distill } from '../src/distillation/distill.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import { openStores } from '../src/store/openStores.ts';
import { createMemoryManagementAPI } from '../src/memory/managementApi.ts';
import { exportBundle } from '../src/portable/exportBundle.ts';

// ── 存储层:只写不读 + 结构无泄漏 ──

test('证据存储:precedingAiContext 落库 + 专用只读取回;Evidence 读结构永不带它（结构墙）', () => {
  const ev = new SqliteEvidenceStore(':memory:');
  try {
    const e = ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: '是的', precedingAiContext: '你喜欢爬山吧?' });
    // 专用只读方法取得回
    assert.equal(ev.precedingAiContextOf(e.id), '你喜欢爬山吧?', 'precedingAiContextOf 取回 AI 上文');
    // 读结构(put 返回值 / get / all)都不带该字段 —— fromRow 结构性不映射
    assert.equal('precedingAiContext' in e, false, 'put 返回的 Evidence 无 precedingAiContext 字段');
    const got = ev.get(e.id)!;
    assert.equal('precedingAiContext' in got, false, 'get() 的 Evidence 无 precedingAiContext 字段');
    assert.equal('precedingAiContext' in ev.all()[0]!, false, 'all() 的 Evidence 无 precedingAiContext 字段');
    // 落库文本里没混进 rawContent / summary
    assert.equal(got.rawContent, '是的');
    assert.equal(got.summary, '是的');
  } finally {
    ev.close();
  }
});

test('证据存储:无 AI 上文 → precedingAiContextOf 返回 null;裸 ingest 路(无字段)恒 null', () => {
  const ev = new SqliteEvidenceStore(':memory:');
  try {
    const e = ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: '我喜欢喝茶' });
    assert.equal(ev.precedingAiContextOf(e.id), null, '不传 = null');
    assert.equal(ev.precedingAiContextOf('no-such-id'), null, '不存在 = null');
    // observed 路（ingestObservations 走 put、不带 precedingAiContext）→ null
    const o = ev.put({ subjectId: 'u', sourceKind: 'observed', hostId: 'h', rawContent: '观察' });
    assert.equal(ev.precedingAiContextOf(o.id), null, 'observed 恒 null');
  } finally {
    ev.close();
  }
});

test('迁移(缺列补):直接构造老库(无 preceding_ai_context 列)→ 构造 store 自动补列、旧行无损、可round-trip', () => {
  const db = new DatabaseSync(':memory:');
  // 手搭 Phase 1b 之前的 13 列 evidence 表（不经 openStores/runMigrations）
  db.exec(`CREATE TABLE evidence (
    id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, source_kind TEXT NOT NULL, host_id TEXT NOT NULL,
    origin_id TEXT, occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL, raw_content TEXT NOT NULL,
    summary TEXT NOT NULL, allow_local_read INTEGER NOT NULL, allow_cloud_read INTEGER NOT NULL,
    allow_inference INTEGER NOT NULL, corrects_evidence_id TEXT
  )`);
  db.prepare(`INSERT INTO evidence (id, subject_id, source_kind, host_id, origin_id, occurred_at, recorded_at, raw_content, summary, allow_local_read, allow_cloud_read, allow_inference, corrects_evidence_id)
    VALUES ('old-1','u','spoken','h',NULL,'2026-01-01T00:00:00.000Z','2026-01-01T00:00:00.000Z','旧证据','旧证据',1,1,1,NULL)`).run();
  try {
    const before = db.prepare("SELECT name FROM pragma_table_info('evidence')").all() as Array<{ name: string }>;
    assert.equal(before.some((c) => c.name === 'preceding_ai_context'), false, '老库无该列');
    // 构造 store(共享连接)→ SCHEMA 的 CREATE IF NOT EXISTS 是 no-op、migrate() 缺列补
    const ev = new SqliteEvidenceStore(db);
    const after = db.prepare("SELECT name FROM pragma_table_info('evidence')").all() as Array<{ name: string }>;
    assert.equal(after.some((c) => c.name === 'preceding_ai_context'), true, 'migrate() 已补列');
    // 旧行无损（列补成 nullable、旧行为 null）
    assert.equal(ev.all().length, 1, '旧行还在');
    assert.equal(ev.precedingAiContextOf('old-1'), null, '旧行 AI 上文 = null');
    // 新写可 round-trip
    const e = ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: '嗯', precedingAiContext: '你今天开心吗?' });
    assert.equal(ev.precedingAiContextOf(e.id), '你今天开心吗?');
  } finally {
    db.close();
  }
});

// ── 捕获:conversation 先存后答时抓上一轮 AI 那句 ──

test('捕获:seedTurns 种入上一轮 AI → handle 存的证据挂上那句 AI 上文', async () => {
  const store = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const retriever = { async indexAll() {}, async search() { return []; } };
    const llm = { callCount: 0, async chat() { this.callCount++; return '好的呀。'; } };
    // 种入一轮:AI 主动问 → 用户下面只回"是的"（孤儿回应）
    const convo = new Conversation({ store, retriever, cognitionStore: cog, llm, seedTurns: [{ role: 'assistant', content: '你喜欢爬山吧?' }] });
    const out = await convo.handle('是的');
    assert.equal(store.precedingAiContextOf(out.storedEvidence.id), '你喜欢爬山吧?', '证据挂上了上一轮 AI 那句');
    assert.equal('precedingAiContext' in out.storedEvidence, false, 'TurnOutcome.storedEvidence 结构上不带 AI 上文（不外泄）');
  } finally {
    store.close(); cog.close();
  }
});

test('捕获:多轮 —— 第一轮无 AI 上文(null)、第二轮挂上第一轮 AI 回复', async () => {
  const store = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const retriever = { async indexAll() {}, async search() { return []; } };
    const llm = { callCount: 0, async chat() { this.callCount++; return `AI回复${this.callCount}`; } };
    const convo = new Conversation({ store, retriever, cognitionStore: cog, llm });
    const t1 = await convo.handle('你好');
    assert.equal(store.precedingAiContextOf(t1.storedEvidence.id), null, '第一轮 window 空 → 无 AI 上文');
    const t2 = await convo.handle('是的');
    assert.equal(store.precedingAiContextOf(t2.storedEvidence.id), 'AI回复1', '第二轮挂上第一轮的 AI 回复');
  } finally {
    store.close(); cog.close();
  }
});

// ── 注入:AI 上文进 distill / consolidate 的 LLM 输入（经隐私门），且不铸独立可溯源 id ──

test('注入:distill 的 LLM 输入带上该证据的 AI 上文（供看懂孤儿回应）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  try {
    ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: '是的', precedingAiContext: '你喜欢爬山吧?' });
    let captured = '';
    const llm = { callCount: 0, tier: 'cloud' as const, async chat(msgs: Array<{ role: string; content: string }>) { this.callCount++; captured = msgs.map((m) => m.content).join('\n'); return '用户确认了喜欢爬山'; } };
    await distill('u', { evidenceStore: ev, eventStore: evt, llm });
    assert.match(captured, /你喜欢爬山吧\?/, 'distill 输入含 AI 上文');
    assert.match(captured, /是的/, 'distill 输入含用户原话');
  } finally {
    ev.close(); evt.close();
  }
});

test('注入:consolidate 的 LLM 输入带 AI 上文;AI 上文只作原话后缀、共用真证据 id、不铸独立条目', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const e = ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: '是的', precedingAiContext: '你喜欢爬山吧?' });
    evt.put({ subjectId: 'u', summary: '用户确认喜欢爬山', occurredAt: e.occurredAt, evidenceIds: [e.id] });
    let captured = '';
    const llm = { callCount: 0, tier: 'cloud' as const, async chat(msgs: Array<{ role: string; content: string }>) { this.callCount++; captured = msgs.map((m) => m.content).join('\n'); return '{"new":[]}'; } };
    await consolidate('u', { eventStore: evt, evidenceStore: ev, cognitionStore: cog, llm });
    assert.match(captured, /你喜欢爬山吧\?/, 'consolidate 输入含 AI 上文');
    // 真证据 id 出现一次（作为 [id] 前缀）；AI 上文没有自己的 [id] 条目
    const idOccurrences = captured.split(`[${e.id}]`).length - 1;
    assert.equal(idOccurrences, 1, '真证据 id 只作一条 [id] 原话出现，AI 上文不另起带 id 的条目');
  } finally {
    ev.close(); evt.close(); cog.close();
  }
});

test('注入隐私门:AI 上文挂在 tier 不可读的证据上 → 不喂给当前模型（随宿主行被隐私门一起挡）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  try {
    // 一条 cloud 不可读的证据（observed 默认 allowCloudRead=false）带 AI 上文
    ev.put({ subjectId: 'u', sourceKind: 'observed', hostId: 'h', rawContent: '观察到的东西', precedingAiContext: 'AI 敏感提问' });
    let captured = '';
    const llm = { callCount: 0, tier: 'cloud' as const, async chat(msgs: Array<{ role: string; content: string }>) { this.callCount++; captured = msgs.map((m) => m.content).join('\n'); return 'x'; } };
    await distill('u', { evidenceStore: ev, eventStore: evt, llm });
    // 该证据整行被 tier 门挡（cloud 读不到 observed）→ 没建 event、也没调模型 → AI 上文自然没喂出去
    assert.equal(llm.callCount, 0, 'cloud tier 读不到该 observed 证据 → 不调模型');
    assert.equal(captured.includes('AI 敏感提问'), false, 'AI 上文没漏给云模型');
  } finally {
    ev.close(); evt.close();
  }
});

// ── 端到端结构无泄漏:listEvidence / exportBundle 拿不到 AI 上文 ──

test('端到端无泄漏:会话产生带 AI 上文的证据 → listEvidence 与 exportBundle 都拿不到那句 AI 话', async () => {
  const bundle = openStores(':memory:');
  try {
    const retriever = { async indexAll() {}, async search() { return []; } };
    const llm = { callCount: 0, async chat() { this.callCount++; return '你喜欢爬山吧?'; } };
    const convo = new Conversation({ store: bundle.evidenceStore, retriever, cognitionStore: bundle.cognitionStore, llm });
    // 第一轮:AI 回"你喜欢爬山吧?"（进 window）;第二轮:用户"是的" → 证据挂上 AI 上文
    await convo.handle('随便聊聊');
    const t2 = await convo.handle('是的');
    // 确认底层确实存了 AI 上文（否则本测试是空转）
    assert.equal(bundle.evidenceStore.precedingAiContextOf(t2.storedEvidence.id), '你喜欢爬山吧?', '底层已存 AI 上文（否则测试空转）');

    // listEvidence:一条都不带 precedingAiContext 字段、序列化里不含那句 AI 话
    const mgmt = createMemoryManagementAPI(bundle);
    const listed = mgmt.listEvidence({ subjectId: 'owner' });
    assert.ok(listed.length >= 1);
    for (const e of listed) assert.equal('precedingAiContext' in e, false, 'listEvidence 的证据不带 AI 上文字段');
    assert.equal(JSON.stringify(listed).includes('你喜欢爬山吧?'), false, 'listEvidence 序列化里没有那句 AI 话');

    // exportBundle:同样拿不到
    const exported = exportBundle('owner', bundle);
    for (const e of exported.data.evidence) assert.equal('precedingAiContext' in e, false, '导出证据不带 AI 上文字段');
    assert.equal(JSON.stringify(exported).includes('你喜欢爬山吧?'), false, '整个导出包里没有那句 AI 话');
  } finally {
    bundle.close();
  }
});
