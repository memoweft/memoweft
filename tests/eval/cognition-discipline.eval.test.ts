/**
 * eval 套件 · 认知纪律三条的成套断言证据（离线护栏层）。
 *
 * 回答评估者「凭什么信你的认知纪律真生效」：把三条纪律从单点断言系统化成
 * 一组带编号的「脚本化对话输入 → 断言认知产出」用例，供 Q3 对比表回链背书。
 *
 * 三条纪律 + 源码锚点（编号见下方各用例 // EVAL-C / M / T ##）：
 *   - 冲突暴露不合并（C 系列）：deriveCredStatus 在 contradictCount>0 时给 'conflicted'；
 *     consolidate 的 conflict 分支标冲突、两条都留，不悄悄合成一条；correct 才允许收敛。
 *   - 情绪封顶（M 系列）：confidence.ts 的 isTransient → transientCap；临时类（state）
 *     credStatus 最多 'low'/'candidate'，不随支持次数升为 'stable'。
 *   - 记≠信（T 系列）：consolidate 不采信 LLM 自报置信（如 confidence:999）；
 *     computeConfidence 里 stated 起点高于 inferred，推测类落低置信候选。
 *
 * 本文件走 tests/**\/*.test.ts glob（离线、进 npm test 护栏）。装配抄 cognition.test.ts
 * 的 computeConfidence / deriveCredStatus 直调 + core.test.ts 的 stubLLM + 各 store 直建。
 *
 * 断言口径：断【语义】不断魔数——情绪封顶断的是「临时类 credStatus 命中 'low'/'candidate'
 * 且不升 'stable'」，不把 transientCap 的当前配置数值当验收锚点（它 config 可调）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../../src/evidence/store.ts';
import { SqliteEventStore } from '../../src/event/store.ts';
import { SqliteCognitionStore } from '../../src/cognition/store.ts';
import { computeConfidence, deriveCredStatus } from '../../src/consolidation/confidence.ts';
import { consolidate } from '../../src/consolidation/consolidate.ts';
import type { ChatMessage } from '../../src/llm/client.ts';

/** stub LLM：回固定 JSON（core.test.ts 同款手法）；带 callCount 供 consolidate 计次。 */
function stubLLM(replyText: string) {
  return {
    callCount: 0,
    async chat(_messages: ChatMessage[]) {
      this.callCount++;
      return replyText;
    },
  };
}

/** 建三库 + 收口的小工具：每个 consolidate 用例各开各连接（走 noopTransaction，行为同旧）。 */
function makeStores() {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  return {
    ev,
    evt,
    cog,
    close() {
      ev.close();
      evt.close();
      cog.close();
    },
  };
}

// ============================================================================
// 纪律一 · 冲突暴露不合并（C 系列）
// 前后矛盾【非纠正】→ 两条都留、标 conflicted，不悄悄合成一条、不新覆盖旧；
// 含「显式纠正」对照组验证系统能区分纠正（允许收敛）与矛盾（暴露保留）。
// ============================================================================

test('EVAL-C01 有反对证据 → credStatus 命中 conflicted（暴露不消解）', () => {
  // 任何内容类型，一旦 contradictCount>0，deriveCredStatus 优先给 conflicted。
  assert.equal(deriveCredStatus(800, 1, 'fact'), 'conflicted', '高置信 fact 遇反证也标冲突');
  assert.equal(deriveCredStatus(200, 1, 'preference'), 'conflicted', '低置信 preference 遇反证也标冲突');
});

test('EVAL-C02 反对证据先于置信判定 → 冲突不被高分洗白', () => {
  // conflicted 分支在最前：即便置信高到本该 stable，有反证就先暴露冲突、不进稳定。
  const wouldBeStable = deriveCredStatus(900, 0, 'fact');
  const withContradict = deriveCredStatus(900, 1, 'fact');
  assert.equal(wouldBeStable, 'stable', '无反证时本会稳定');
  assert.equal(withContradict, 'conflicted', '有反证 → 冲突优先，不因高分洗成 stable');
});

test('EVAL-C03 conflict 分支：矛盾非纠正 → 旧认知标 conflicted 且仍活跃（不删不覆盖）', async () => {
  const s = makeStores();
  try {
    const old = s.cog.put({ subjectId: 'owner', content: '用户喜欢早睡', contentType: 'preference', formedBy: 'stated', confidence: 600, credStatus: 'limited' });
    const e = s.ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: '凌晨3点还在打游戏', allowCloudRead: true });
    s.evt.put({ subjectId: 'owner', summary: '观察到凌晨3点打游戏', occurredAt: e.occurredAt, evidenceIds: [e.id] });
    // LLM 判成 conflict（矛盾但非明确纠正）。
    const llm = stubLLM(`{"conflict":[{"cognition_id":"${old.id}","support_evidence_ids":["${e.id}"]}]}`);
    const r = await consolidate('owner', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm });
    assert.equal(r.conflicted, 1, '判成一条冲突');
    assert.equal(s.cog.get(old.id)?.credStatus, 'conflicted', '旧认知被标 conflicted');
    assert.equal(s.cog.get(old.id)?.invalidAt ?? null, null, '冲突不使旧认知失效');
    assert.ok(s.cog.active('owner').some((c) => c.id === old.id), '旧认知仍活跃（不删）');
  } finally {
    s.close();
  }
});

test('EVAL-C04 conflict：两条都留、不合成一条（矛盾双方并存）', async () => {
  const s = makeStores();
  try {
    const old = s.cog.put({ subjectId: 'owner', content: '用户偏好远程办公', contentType: 'preference', formedBy: 'stated', confidence: 600, credStatus: 'limited' });
    const e = s.ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: '每天主动去公司坐班', allowCloudRead: true });
    s.evt.put({ subjectId: 'owner', summary: '观察到每天去公司坐班', occurredAt: e.occurredAt, evidenceIds: [e.id] });
    const llm = stubLLM(`{"conflict":[{"cognition_id":"${old.id}","support_evidence_ids":["${e.id}"]}]}`);
    await consolidate('owner', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm });
    // 冲突证据以 contradict 关系挂到旧认知上，原认知内容不被改写。
    const sources = s.cog.sourcesOf(old.id);
    assert.ok(sources.some((l) => l.relation === 'contradict' && l.evidenceId === e.id), '反证以 contradict 关系挂上，不并入原文');
    assert.equal(s.cog.get(old.id)?.content, '用户偏好远程办公', '原认知内容不被冲突证据改写');
  } finally {
    s.close();
  }
});

test('EVAL-C05 对照组 · 显式纠正 → 允许收敛（旧失效保留、新采纳）', async () => {
  const s = makeStores();
  try {
    const old = s.cog.put({ subjectId: 'owner', content: '用户喜欢喝茶', contentType: 'preference', formedBy: 'stated', confidence: 600, credStatus: 'limited' });
    const e = s.ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '更正一下，我现在不喝茶改喝咖啡了' });
    s.evt.put({ subjectId: 'owner', summary: '用户更正：改喝咖啡', occurredAt: e.occurredAt, evidenceIds: [e.id] });
    const llm = stubLLM(`{"correct":[{"cognition_id":"${old.id}","content":"用户现在喝咖啡，不喝茶了","content_type":"preference","formed_by":"stated","support_evidence_ids":["${e.id}"]}]}`);
    const r = await consolidate('owner', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm });
    assert.equal(r.corrected, 1, '判成纠正');
    assert.ok(s.cog.get(old.id)?.invalidAt, '纠正 → 旧的标失效（收敛，与 conflict 不同）');
    assert.equal(s.cog.active('owner').length, 1, '只剩纠正后的新认知活跃');
    assert.equal(s.cog.active('owner')[0]!.credStatus === 'conflicted', false, '纠正后新认知不带冲突');
  } finally {
    s.close();
  }
});

test('EVAL-C06 区分纠正与矛盾：correct 旧失效、conflict 旧仍活跃（同起点两种走向）', async () => {
  // correct 组
  const a = makeStores();
  // conflict 组
  const b = makeStores();
  try {
    const seed = (s: ReturnType<typeof makeStores>) =>
      s.cog.put({ subjectId: 'owner', content: '用户喜欢辣', contentType: 'preference', formedBy: 'stated', confidence: 600, credStatus: 'limited' });
    const oldA = seed(a);
    const eA = a.ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '纠正一下，我其实不吃辣' });
    a.evt.put({ subjectId: 'owner', summary: '用户纠正：不吃辣', occurredAt: eA.occurredAt, evidenceIds: [eA.id] });
    await consolidate('owner', { eventStore: a.evt, evidenceStore: a.ev, cognitionStore: a.cog, llm: stubLLM(`{"correct":[{"cognition_id":"${oldA.id}","content":"用户不吃辣","content_type":"preference","formed_by":"stated","support_evidence_ids":["${eA.id}"]}]}`) });

    const oldB = seed(b);
    const eB = b.ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: '点了不辣的菜', allowCloudRead: true });
    b.evt.put({ subjectId: 'owner', summary: '观察到点不辣的菜', occurredAt: eB.occurredAt, evidenceIds: [eB.id] });
    await consolidate('owner', { eventStore: b.evt, evidenceStore: b.ev, cognitionStore: b.cog, llm: stubLLM(`{"conflict":[{"cognition_id":"${oldB.id}","support_evidence_ids":["${eB.id}"]}]}`) });

    assert.ok(a.cog.get(oldA.id)?.invalidAt, 'correct：旧失效');
    assert.equal(b.cog.get(oldB.id)?.invalidAt ?? null, null, 'conflict：旧不失效');
    assert.equal(b.cog.get(oldB.id)?.credStatus, 'conflicted', 'conflict：旧标冲突仍在');
  } finally {
    a.close();
    b.close();
  }
});

test('EVAL-C07 conflict 没引到有效原话 → 不凭空标冲突（宁缺毋滥）', async () => {
  const s = makeStores();
  try {
    const old = s.cog.put({ subjectId: 'owner', content: '用户喜欢安静', contentType: 'preference', formedBy: 'stated', confidence: 600, credStatus: 'limited' });
    const e = s.ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: '开了很吵的音乐', allowCloudRead: true });
    s.evt.put({ subjectId: 'owner', summary: '观察到放很吵的音乐', occurredAt: e.occurredAt, evidenceIds: [e.id] });
    // LLM 引了一个不存在的证据 id → 无合法支撑，不该标冲突。
    const llm = stubLLM(`{"conflict":[{"cognition_id":"${old.id}","support_evidence_ids":["ev-不存在"]}]}`);
    const r = await consolidate('owner', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm });
    assert.equal(r.conflicted, 0, '无可溯源反证 → 不标冲突');
    assert.notEqual(s.cog.get(old.id)?.credStatus, 'conflicted', '旧认知不被凭空标冲突');
  } finally {
    s.close();
  }
});

// ============================================================================
// 纪律二 · 情绪封顶（M 系列）
// 情绪反复（今天讨厌 X、明天爱 X）→ 临时类（state）credStatus 命中 'low'/'candidate'，
// 且【不随支持次数增加升为 stable】。断语义、不把 transientCap 的当前配置值当锚点。
// ============================================================================

test('EVAL-M01 临时类（state）credStatus 最多 low，不进 stable', () => {
  // 用远高于任何合理阈值的分数喂进去，验证临时类仍被压在 low（永不 stable/limited）。
  const highScore = 1000;
  const status = deriveCredStatus(highScore, 0, 'state');
  assert.equal(status, 'low', 'state 即便满分也只到 low');
  assert.notEqual(status, 'stable', 'state 永不进 stable');
  assert.notEqual(status, 'limited', 'state 永不进 limited');
});

test('EVAL-M02 情绪反复被多次支持 → confidence 不随支持数升为 stable 档', () => {
  // 同一条情绪被支持 1 次 vs 9 次，临时类都不该跨进 stable。断的是「状态不升 stable」这个语义，
  // 不锚定 transientCap 的具体数字（config 可调）。
  const few = computeConfidence({ contentType: 'state', formedBy: 'stated', supportCount: 1, contradictCount: 0 });
  const many = computeConfidence({ contentType: 'state', formedBy: 'stated', supportCount: 9, contradictCount: 0 });
  assert.notEqual(deriveCredStatus(few, 0, 'state'), 'stable', '支持 1 次不 stable');
  assert.notEqual(deriveCredStatus(many, 0, 'state'), 'stable', '支持 9 次仍不 stable（重复≠稳定特质）');
  assert.ok(deriveCredStatus(many, 0, 'state') === 'low' || deriveCredStatus(many, 0, 'state') === 'candidate', '多次支持后仍落在 low/candidate 档');
});

test('EVAL-M03 稳定类 vs 临时类同等支持：偏好可升 stable、情绪封顶', () => {
  // 同样 stated + 大量支持：preference（稳定类）能爬到高档，state（临时类）被封。
  const prefHi = computeConfidence({ contentType: 'preference', formedBy: 'stated', supportCount: 9, contradictCount: 0 });
  const stateHi = computeConfidence({ contentType: 'state', formedBy: 'stated', supportCount: 9, contradictCount: 0 });
  assert.ok(prefHi > stateHi, '稳定类置信高于临时类（排序不倒挂）');
  assert.notEqual(deriveCredStatus(stateHi, 0, 'state'), 'stable', '情绪封顶不 stable');
});

test('EVAL-M04 情绪反复（讨厌→爱）经 consolidate 落库 → credStatus 落 low/candidate', async () => {
  const s = makeStores();
  try {
    const e1 = s.ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '今天好烦这个项目' });
    s.evt.put({ subjectId: 'owner', summary: '用户说烦这个项目', occurredAt: e1.occurredAt, evidenceIds: [e1.id] });
    const llm = stubLLM(`{"new":[{"content":"用户当前对项目感到烦躁","content_type":"state","formed_by":"stated","support_evidence_ids":["${e1.id}"]}]}`);
    const r = await consolidate('owner', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm });
    assert.equal(r.created.length, 1, '情绪落一条 state');
    const c = r.created[0]!;
    assert.equal(c.contentType, 'state', '判为临时类 state');
    assert.ok(c.credStatus === 'low' || c.credStatus === 'candidate', `情绪落低置信档（实际 ${c.credStatus}）`);
    assert.notEqual(c.credStatus, 'stable', '情绪不冒充稳定特质');
  } finally {
    s.close();
  }
});

test('EVAL-M05 同一情绪被 reinforce 多次 → 仍不升 stable（重复不越攒越像定论）', async () => {
  const s = makeStores();
  try {
    const e0 = s.ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '今天有点累' });
    const c0 = s.cog.put({ subjectId: 'owner', content: '用户当前感到疲惫', contentType: 'state', formedBy: 'stated', confidence: 250, credStatus: 'low', evidence: [{ evidenceId: e0.id, relation: 'support' }] });
    const e1 = s.ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '还是累' });
    s.evt.put({ subjectId: 'owner', summary: '用户又说累', occurredAt: e1.occurredAt, evidenceIds: [e1.id] });
    const llm = stubLLM(`{"reinforce":[{"cognition_id":"${c0.id}","support_evidence_ids":["${e1.id}"]}]}`);
    const r = await consolidate('owner', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm });
    assert.equal(r.reinforced, 1, '强化成功（补挂证据）');
    const after = s.cog.get(c0.id)!;
    assert.notEqual(after.credStatus, 'stable', '情绪被反复印证也不升 stable');
    assert.ok(after.credStatus === 'low' || after.credStatus === 'candidate', '强化后仍落低置信档');
  } finally {
    s.close();
  }
});

test('EVAL-M06 稳定类不受封顶影响（封顶只针对临时类，非一刀切压所有）', () => {
  // 验证「情绪封顶」是分型策略、不误伤稳定类：goal/fact 高分照进 stable。
  assert.equal(deriveCredStatus(800, 0, 'fact'), 'stable', 'fact 高分进 stable（不被封顶误伤）');
  assert.equal(deriveCredStatus(800, 0, 'goal'), 'stable', 'goal 高分进 stable');
  // 对照：同分数的 state 被封在 low。
  assert.equal(deriveCredStatus(800, 0, 'state'), 'low', '同分 state 被封 low');
});

test('EVAL-M07 情绪即便反复被提也顶在临时档：低支持与高支持同落非 stable', () => {
  // 扫一遍支持次数 1..9，临时类 state 的 credStatus 全程不 stable（封顶是硬约束）。
  for (let n = 1; n <= 9; n++) {
    const conf = computeConfidence({ contentType: 'state', formedBy: 'stated', supportCount: n, contradictCount: 0 });
    const status = deriveCredStatus(conf, 0, 'state');
    assert.notEqual(status, 'stable', `支持 ${n} 次仍不 stable`);
    assert.notEqual(status, 'limited', `支持 ${n} 次仍不 limited`);
  }
});

// ============================================================================
// 纪律三 · 记≠信（T 系列）
// LLM 瞎报高把握（confidence:999 / 「我非常确定」）→ 系统自算、不采信自报值；
// 亲口说（stated）起点高于推测（inferred）→ 推测落低置信候选、不冒充亲述事实。
// ============================================================================

test('EVAL-T01 不采信 LLM 自报置信：塞 confidence:999 → 产出不等于 999', async () => {
  const s = makeStores();
  try {
    const e1 = s.ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '我喜欢喝茶' });
    s.evt.put({ subjectId: 'owner', summary: '用户喜欢喝茶', occurredAt: e1.occurredAt, evidenceIds: [e1.id] });
    const llm = stubLLM(`{"new":[{"content":"用户喜欢喝茶","content_type":"preference","formed_by":"stated","support_evidence_ids":["${e1.id}"],"confidence":999}]}`);
    const r = await consolidate('owner', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm });
    assert.equal(r.created.length, 1);
    assert.notEqual(r.created[0]!.confidence, 999, '系统自算，不采信 LLM 自报的 999');
    assert.ok(r.created[0]!.confidence > 0 && r.created[0]!.confidence <= 1000, '落在系统合法区间');
  } finally {
    s.close();
  }
});

test('EVAL-T02 LLM 嘴上「非常确定」不改自算：置信由规则定、与措辞无关', async () => {
  // 同一亲述事实，一次带「非常确定 / confidence:999」措辞、一次不带；系统自算值应一致（措辞不影响把握度）。
  const seed = (store: ReturnType<typeof makeStores>, reply: string) => {
    const e = store.ev.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '我住在上海' });
    store.evt.put({ subjectId: 'owner', summary: '用户住上海', occurredAt: e.occurredAt, evidenceIds: [e.id] });
    return reply.replace('__EV__', e.id);
  };
  const plain = makeStores();
  const boastful = makeStores();
  try {
    const rp = seed(plain, `{"new":[{"content":"用户住在上海","content_type":"fact","formed_by":"stated","support_evidence_ids":["__EV__"]}]}`);
    const rb = seed(boastful, `{"new":[{"content":"用户住在上海（我非常确定，confidence:999）","content_type":"fact","formed_by":"stated","support_evidence_ids":["__EV__"]}]}`);
    const a = await consolidate('owner', { eventStore: plain.evt, evidenceStore: plain.ev, cognitionStore: plain.cog, llm: stubLLM(rp) });
    const b = await consolidate('owner', { eventStore: boastful.evt, evidenceStore: boastful.ev, cognitionStore: boastful.cog, llm: stubLLM(rb) });
    assert.equal(a.created[0]!.confidence, b.created[0]!.confidence, '带不带「非常确定」措辞，系统自算置信一致');
  } finally {
    plain.close();
    boastful.close();
  }
});

test('EVAL-T03 亲口说 > 推测：computeConfidence 里 stated 起点高于 inferred', () => {
  const stated = computeConfidence({ contentType: 'fact', formedBy: 'stated', supportCount: 1, contradictCount: 0 });
  const inferred = computeConfidence({ contentType: 'fact', formedBy: 'inferred', supportCount: 1, contradictCount: 0 });
  assert.ok(stated > inferred, '亲述起点高于推测');
});

test('EVAL-T04 推测类落低置信候选、不冒充亲述事实（同支持数下 credStatus 更保守）', () => {
  // 同为 1 条支持：亲述可能已够 limited/low，推测更低——验证推测不被抬到亲述档。
  const statedConf = computeConfidence({ contentType: 'fact', formedBy: 'stated', supportCount: 1, contradictCount: 0 });
  const inferredConf = computeConfidence({ contentType: 'fact', formedBy: 'inferred', supportCount: 1, contradictCount: 0 });
  const statedStatus = deriveCredStatus(statedConf, 0, 'fact');
  const inferredStatus = deriveCredStatus(inferredConf, 0, 'fact');
  const rank = { candidate: 0, low: 1, limited: 2, stable: 3, conflicted: -1 } as const;
  assert.ok(rank[inferredStatus] <= rank[statedStatus], '推测的可信档不高于亲述');
  assert.notEqual(inferredStatus, 'stable', '单条支持的推测绝不冒充 stable');
} );

test('EVAL-T05 consolidate 缺 formed_by → 保守当 inferred（推测），不默认高置信亲述', async () => {
  const s = makeStores();
  try {
    const e1 = s.ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: '搜索「怎么找女朋友」', allowCloudRead: true });
    s.evt.put({ subjectId: 'owner', summary: '观察到搜索找对象', occurredAt: e1.occurredAt, evidenceIds: [e1.id] });
    // LLM 不给 formed_by（缺字段）→ 系统兜底当 inferred。
    const llm = stubLLM(`{"new":[{"content":"用户可能单身","content_type":"fact","support_evidence_ids":["${e1.id}"]}]}`);
    const r = await consolidate('owner', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm });
    assert.equal(r.created.length, 1);
    assert.equal(r.created[0]!.formedBy, 'inferred', '缺 formed_by → 保守当推测');
    // 且推测起点低于同内容亲述基线（拿基线对照，不锚定绝对分）。
    const statedBaseline = computeConfidence({ contentType: 'fact', formedBy: 'stated', supportCount: 1, contradictCount: 0 });
    assert.ok(r.created[0]!.confidence < statedBaseline, '推测置信低于亲述基线');
  } finally {
    s.close();
  }
});

test('EVAL-T06 支持证据加分是渐进的、非一步登天（记≠一次就信满）', () => {
  // 记 ≠ 信：一条证据不给满分；多一条支持渐进加分，仍受 supportCap 封顶（不无限涨）。
  const one = computeConfidence({ contentType: 'fact', formedBy: 'stated', supportCount: 1, contradictCount: 0 });
  const two = computeConfidence({ contentType: 'fact', formedBy: 'stated', supportCount: 2, contradictCount: 0 });
  const many = computeConfidence({ contentType: 'fact', formedBy: 'stated', supportCount: 50, contradictCount: 0 });
  assert.ok(one < 1000, '单条支持不给满分（记≠满信）');
  assert.ok(two > one, '多一条支持渐进加分');
  assert.ok(many <= 1000, '支持再多也封顶在合法上限（不无限膨胀）');
} );

test('EVAL-T07 反对证据扣分：记下反证 → 置信降且暴露冲突（不粉饰）', () => {
  const clean = computeConfidence({ contentType: 'fact', formedBy: 'stated', supportCount: 3, contradictCount: 0 });
  const contested = computeConfidence({ contentType: 'fact', formedBy: 'stated', supportCount: 3, contradictCount: 1 });
  assert.ok(contested < clean, '有反证 → 自算置信下降');
  assert.equal(deriveCredStatus(contested, 1, 'fact'), 'conflicted', '有反证 → 状态暴露为冲突');
} );
