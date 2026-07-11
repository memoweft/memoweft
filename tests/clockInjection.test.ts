/**
 * S1a 护栏（Phase 4 · 可注入时钟）：三个 store 的落库/更新时间源可注入。
 * 注入固定/前进的 Clock → 时间戳跟随注入值(确定性 + 时间旅行的地基);
 * 不注入 → 用真实系统时间(缺省行为不变)。纯离线、内存库。
 * 本步只碰 store 时间源(internal),不碰公共 API、不碰置信度自算。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { createMemoWeftCore } from '../src/core/createCore.ts';
import type { Clock } from '../src/clock.ts';
import type { ChatMessage } from '../src/llm/client.ts';

const AT = '2026-03-01T00:00:00.000Z';
const fixedClock = (iso = AT): Clock => () => new Date(iso);

test('evidence store：注入 clock → recordedAt 跟随注入值（确定性）', () => {
  const s = new SqliteEvidenceStore(':memory:', undefined, fixedClock());
  try {
    const e = s.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: 'x' });
    assert.equal(e.recordedAt, AT, 'recordedAt = 注入的固定时间');
    assert.equal(e.occurredAt, AT, 'occurredAt 缺省 = recordedAt（同一注入时间）');
  } finally {
    s.close();
  }
});

test('cognition store：put/update 时间跟随 clock，update 用前进后的时间', () => {
  let t = new Date('2026-03-01T00:00:00.000Z');
  const clock: Clock = () => t;
  const s = new SqliteCognitionStore(':memory:', clock);
  try {
    const c = s.put({ subjectId: 'u', content: 'c', contentType: 'fact', formedBy: 'stated', confidence: 600, credStatus: 'limited' });
    assert.equal(c.createdAt, '2026-03-01T00:00:00.000Z');
    assert.equal(c.updatedAt, '2026-03-01T00:00:00.000Z');
    // 纪律：置信度由入参定、store 不算 —— 注入 clock 不改置信度（铁律 3b）。
    assert.equal(c.confidence, 600, '置信度不受 clock 影响');

    t = new Date('2026-03-05T00:00:00.000Z'); // 前进时钟 4 天
    const u = s.update(c.id, { confidence: 700 });
    assert.equal(u!.updatedAt, '2026-03-05T00:00:00.000Z', 'update 用前进后的时间');
    assert.equal(u!.createdAt, '2026-03-01T00:00:00.000Z', 'createdAt 不随 update 变');
  } finally {
    s.close();
  }
});

test('event store：createdAt 跟随 clock；occurredAt 由入参定、不受 clock 影响', () => {
  const s = new SqliteEventStore(':memory:', fixedClock());
  try {
    const ev = s.put({ subjectId: 'u', summary: 's', occurredAt: '2026-02-01T00:00:00.000Z', evidenceIds: [] });
    assert.equal(ev.createdAt, AT, 'createdAt = 注入时间');
    assert.equal(ev.occurredAt, '2026-02-01T00:00:00.000Z', 'occurredAt 是业务时间、不被 clock 覆盖');
  } finally {
    s.close();
  }
});

test('回归：缺省不注入 clock → 用真实系统时间（行为不变）', () => {
  const s = new SqliteEvidenceStore(':memory:');
  try {
    const before = Date.now();
    const e = s.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: 'x' });
    const t = Date.parse(e.recordedAt);
    assert.ok(t >= before - 2000 && t <= Date.now() + 2000, '缺省用真实系统时间（±2s 容差）');
  } finally {
    s.close();
  }
});

test('门面：createMemoWeftCore({ clock }) → 落库时间跟随注入的 clock（S1b 公共入口）', async () => {
  const core = createMemoWeftCore({ dbPath: ':memory:', clock: fixedClock() });
  try {
    const e = await core.ingestUserMessage({ content: 'hi', subjectId: 'u' });
    assert.equal(e.recordedAt, AT, 'core 门面注入的 clock 透传到 evidence store 的 recordedAt');
  } finally {
    core.close();
  }
});

test('S2 写路径：固定 clock 下 core.updateProfile 产的认知时间戳 = 注入 clock', async () => {
  // 极简离线 stub：distill 出一句事件；consolidate 出一条 new preference，引用真实 evidence id。
  const stub = {
    callCount: 0,
    async chat(msgs: ChatMessage[]): Promise<string> {
      this.callCount++;
      const sys = msgs[0]?.content ?? '';
      const body = msgs[1]?.content ?? '';
      if (!/JSON/.test(sys)) return 'The user likes tea.';
      // consolidate prompt 里 utterance 行格式：`  - [evidence-id] 原话`（缩进 + 方括号 id），同 no-key-demo。
      const um = body.split('\n').map((l) => l.match(/^\s+- \[([^\]]+)\] /)).find(Boolean);
      const eid = um ? um[1]! : 'x';
      return JSON.stringify({
        new: [{ content: 'User likes tea', content_type: 'preference', formed_by: 'stated', support_evidence_ids: [eid] }],
        reinforce: [], correct: [], conflict: [],
      });
    },
  };
  const nullRet = { async indexAll() {}, async search() { return []; } };
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stub, retriever: nullRet, clock: fixedClock() });
  try {
    await core.ingestUserMessage({ content: 'I like tea', subjectId: 'u', occurredAt: AT });
    await core.updateProfile({ subjectId: 'u' });
    const cogs = core.memory.listCognitions({ subjectId: 'u' });
    assert.ok(cogs.length >= 1, '产出至少一条认知（写路径接线通）');
    for (const c of cogs) {
      assert.equal(c.createdAt, AT, '认知 createdAt = 注入 clock（consolidate → store clock）');
      assert.equal(c.updatedAt, AT, '认知 updatedAt = 注入 clock');
    }
  } finally {
    core.close();
  }
});
