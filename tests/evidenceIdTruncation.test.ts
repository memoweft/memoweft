/**
 * evidence id 截断 · 回归测试（2026-07-17 dogfood 根因）。
 *
 * 靶心：真实 dogfood 里 mimo-v2.5-pro **间歇性**把 36 字符的 UUID 证据 id 截断成前 8 位再写回
 * （实测 6 次重放里 5 次；另有一次写成 `ev-` + 前 8 位 —— 照抄 `prompts.ts:87` 输出示例里的
 * `"support_evidence_ids":["ev-1"]` 占位形态）。模型产出的 JSON 完全合法（finish_reason=stop、
 * jsonRepair 零告警、content 4000~5200 字、new 与 resolutions 都填得好好的），
 * 但 `pickSupport`（只查 id 白名单）与 resolutions 白名单都做**精确匹配** → 短 id 一个也匹配不上
 * → `:302 support.length === 0 → continue` 丢掉每条认知、resolutions 整批被 `continue` 丢掉
 * → **整批 0 解析 0 认知，event 仍被 `:426` 无条件标 consolidated → 证据永久蒸发**。
 * 两个通道共用同一套白名单（`spokenEvidence ⊆ validEvidence`），故「0 解析」与「0 认知」完全共变。
 *
 * 真实代价（活库实测）：5 个批次 47 条原话静默丢失，含用户亲口的「我今年26岁有一辆25款的小鹏G6」。
 *
 * **护栏不可放宽**（本文件后半段钉的就是这条）：容错只允许把短 id 解回**白名单内**的真 id，
 * 且必须【唯一命中】。捏造 id、歧义前缀、过短前缀一律仍旧丢弃 —— 3a/3d 结构墙一寸不让。
 * 与 confirmedLaundering.test.ts 的 3a 用例互补：那里钉「捏造 id 进不来」，这里钉「真 id 别被误杀」。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { SqliteSemanticResolutionStore } from '../src/interaction/semanticResolutionStore.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';

interface Stores {
  ev: SqliteEvidenceStore;
  evt: SqliteEventStore;
  cog: SqliteCognitionStore;
  sr: SqliteSemanticResolutionStore;
}
function fresh(): Stores {
  return {
    ev: new SqliteEvidenceStore(':memory:'),
    evt: new SqliteEventStore(':memory:'),
    cog: new SqliteCognitionStore(':memory:'),
    sr: new SqliteSemanticResolutionStore(':memory:'),
  };
}
function closeAll(s: Stores) { s.ev.close(); s.evt.close(); s.cog.close(); s.sr.close(); }
function deps(s: Stores, stub: { callCount: number; chat(): Promise<string> }) {
  return { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, semanticResolutionStore: s.sr, llm: stub };
}

/** 造一条【用户主动说的】证据 + 覆盖它的事件；返回真实证据 id（36 字符 UUID）。 */
function said(s: Stores, at: string, userWord: string): string {
  const e = s.ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: userWord, occurredAt: at });
  s.evt.put({ subjectId: 'u', summary: `用户说"${userWord}"`, occurredAt: at, evidenceIds: [e.id] });
  return e.id;
}

/** 按真实 dogfood 报文的形状造回复：new + resolutions 都引 `citeId`。 */
function reply(citeId: string, content = '用户年龄26岁'): string {
  return JSON.stringify({
    new: [{ content, content_type: 'fact', formed_by: 'stated', support_evidence_ids: [citeId] }],
    resolutions: [{
      evidence_id: citeId, resolved_content: content, response_act: 'none',
      prompt_act: 'none', proposition_origin: 'user_stated', assertion_strength: 'explicit', required_context: '',
    }],
  });
}
const stubOf = (body: string) => ({ callCount: 0, async chat() { this.callCount++; return body; } });

// ── ① 红：模型截断 id → 认知与解析都不该整批蒸发 ──

test('模型把 evidence id 截断成 UUID 前 8 位 → 认知与解析仍应落库（不再整批蒸发）', async () => {
  const s = fresh();
  try {
    const eId = said(s, '2026-06-01T08:00:00.000Z', '我今年26岁有一辆25款的小鹏G6');
    const truncated = eId.slice(0, 8); // 实测形态：'25601f4c'
    assert.equal(truncated.length, 8);
    assert.notEqual(truncated, eId, '前提：真 id 是 36 字符 UUID');

    const r = await consolidate('u', deps(s, stubOf(reply(truncated))));

    assert.equal(r.created.length, 1, '认知应落库（此前：pickSupport 精确匹配落空 → :302 continue → 0）');
    assert.equal(s.cog.sourcesOf(r.created[0]!.id)[0]!.evidenceId, eId, '溯源必须挂到【真 id】，不是短 id');
    assert.ok(s.sr.ofEvidence(eId), '解析应落库，且 evidence_id 是【真 id】（不能把短 id 写成脏数据）');
  } finally { closeAll(s); }
});

test('模型照提示词示例写成 `ev-` + 前 8 位（实测形态 ev-25601f4c）→ 同样应解回真 id', async () => {
  const s = fresh();
  try {
    const eId = said(s, '2026-06-01T08:00:00.000Z', '我喜欢喝东方树叶');
    const r = await consolidate('u', deps(s, stubOf(reply(`ev-${eId.slice(0, 8)}`, '用户喜欢喝东方树叶'))));
    assert.equal(r.created.length, 1, '剥掉示例的 ev- 前缀后应能解回真 id');
    assert.equal(s.cog.sourcesOf(r.created[0]!.id)[0]!.evidenceId, eId);
    assert.ok(s.sr.ofEvidence(eId), '解析落到真 id 上');
  } finally { closeAll(s); }
});

// ── ② 绿（护栏）：容错一寸也不许放宽白名单 ──

test('护栏：捏造的非前缀 id 仍被白名单挡掉（3a 不因容错而松动）', async () => {
  const s = fresh();
  try {
    said(s, '2026-06-01T08:00:00.000Z', '嗯');
    const r = await consolidate('u', deps(s, stubOf(reply('ai-context-only-fake'))));
    assert.equal(r.created.length, 0, '捏造 id 不是任何真 id 的前缀 → 仍不产无溯源认知');
    assert.equal(s.sr.ofEvidence('ai-context-only-fake'), null, '更不许把捏造 id 写进解析表');
  } finally { closeAll(s); }
});

test('护栏：前缀歧义（两条证据同前缀）→ 丢弃，绝不猜', async () => {
  const s = fresh();
  try {
    // 用 insert（按原 id 原样插入，store.ts:116）确定性地造出共享前缀的两条证据——
    // 真实 UUID 前 8 位碰撞概率约 1e-8，靠 put() 的随机 id 钉不住这个分支。
    const base = {
      subjectId: 'u', sourceKind: 'spoken' as const, hostId: 'h',
      recordedAt: '2026-06-01T08:00:00.000Z', summary: '',
      allowLocalRead: true, allowCloudRead: true, allowInference: true,
      originId: null, correctsEvidenceId: null, precedingAiContext: null,
    };
    const a = { ...base, id: 'dup12345-aaaa-4aaa-8aaa-aaaaaaaaaaaa', rawContent: '前者', occurredAt: '2026-06-01T08:00:00.000Z' };
    const b = { ...base, id: 'dup12345-bbbb-4bbb-8bbb-bbbbbbbbbbbb', rawContent: '后者', occurredAt: '2026-06-01T08:01:00.000Z' };
    s.ev.insert(a as never);
    s.ev.insert(b as never);
    s.evt.put({ subjectId: 'u', summary: '二选一', occurredAt: '2026-06-01T08:00:00.000Z', evidenceIds: [a.id, b.id] });
    assert.equal(a.id.slice(0, 8), b.id.slice(0, 8), '前提：两条证据共享前 8 位前缀');

    const r = await consolidate('u', deps(s, stubOf(reply('dup12345'))));
    assert.equal(r.created.length, 0, '歧义前缀 → 解不出唯一真 id → 丢弃（宁可不记，不可记错）');
    assert.equal(s.sr.ofEvidence(a.id), null, '歧义时也不许给任一候选落解析');
    assert.equal(s.sr.ofEvidence(b.id), null);
  } finally { closeAll(s); }
});

test('护栏：过短的 id（< 8 字符）不做前缀猜测', async () => {
  const s = fresh();
  try {
    said(s, '2026-06-01T08:00:00.000Z', '是');
    // 'ev-1' 正是提示词示例里的字面占位；剥掉 ev- 只剩 '1' → 太短，绝不能拿它去前缀匹配
    const r = await consolidate('u', deps(s, stubOf(reply('ev-1'))));
    assert.equal(r.created.length, 0, '示例占位 id 不该匹配到任何真证据');
  } finally { closeAll(s); }
});

test('模型写对完整 id 时行为零变化（精确匹配优先）', async () => {
  const s = fresh();
  try {
    const eId = said(s, '2026-06-01T08:00:00.000Z', '我今年26岁');
    const r = await consolidate('u', deps(s, stubOf(reply(eId))));
    assert.equal(r.created.length, 1);
    assert.equal(s.cog.sourcesOf(r.created[0]!.id)[0]!.evidenceId, eId);
    assert.ok(s.sr.ofEvidence(eId));
  } finally { closeAll(s); }
});
