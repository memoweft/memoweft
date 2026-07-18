/**
 * 可注入时钟护栏：三个 store 的落库和更新时间源均可注入。
 * 注入固定/前进的 Clock → 时间戳跟随注入值(确定性 + 时间旅行的地基);
 * 不注入 → 用真实系统时间(缺省行为不变)。纯离线、内存库。
 * 测试只覆盖 store 时间源，不改变公共 API 或置信度计算。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { createMemoWeftCore } from '../src/core/createCore.ts';
import { proposeAsk } from '../src/asking/proposeAsk.ts';
import { revisitConflicts } from '../src/asking/revisitConflicts.ts';
import { createRunLogger } from '../src/obs/runLog.ts';
import type { Clock } from '../src/clock.ts';
import type { ChatMessage } from '../src/llm/client.ts';
import type { Retriever } from '../src/retrieval/retriever.ts';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join as pathJoin } from 'node:path';

/** 简易词匹配召回器（同 no-key-demo）：按共享词打分，供衰减门控测试有东西可召回。 */
function wordRetriever(): Retriever {
  let items: Array<{ id: string; text: string }> = [];
  const words = (s: string) => new Set(s.toLowerCase().split(/\W+/).filter(Boolean));
  return {
    async indexAll(next) {
      items = [...next];
    },
    async search(query, topK) {
      const q = words(query);
      return items
        .map((it) => ({ id: it.id, score: [...words(it.text)].filter((w) => q.has(w)).length }))
        .filter((h) => h.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, topK);
    },
  };
}

const AT = '2026-03-01T00:00:00.000Z';
const fixedClock =
  (iso = AT): Clock =>
  () =>
    new Date(iso);

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
    const c = s.put({
      subjectId: 'u',
      content: 'c',
      contentType: 'fact',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
    });
    assert.equal(c.createdAt, '2026-03-01T00:00:00.000Z');
    assert.equal(c.updatedAt, '2026-03-01T00:00:00.000Z');
    // 置信度由入参和规则决定；注入 clock 只控制时间戳，不改变置信度。
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
    const ev = s.put({
      subjectId: 'u',
      summary: 's',
      occurredAt: '2026-02-01T00:00:00.000Z',
      evidenceIds: [],
    });
    assert.equal(ev.createdAt, AT, 'createdAt = 注入时间');
    assert.equal(
      ev.occurredAt,
      '2026-02-01T00:00:00.000Z',
      'occurredAt 是业务时间、不被 clock 覆盖',
    );
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

test('门面：createMemoWeftCore({ clock }) → 落库时间跟随注入的 clock', async () => {
  const core = createMemoWeftCore({ dbPath: ':memory:', clock: fixedClock() });
  try {
    const e = await core.ingestUserMessage({ content: 'hi', subjectId: 'u' });
    assert.equal(e.recordedAt, AT, 'core 门面注入的 clock 透传到 evidence store 的 recordedAt');
  } finally {
    core.close();
  }
});

test('写路径：固定 clock 下 core.updateProfile 产出的认知时间戳 = 注入 clock', async () => {
  // 极简离线 stub：distill 出一句事件；consolidate 出一条 new preference，引用真实 evidence id。
  const stub = {
    callCount: 0,
    async chat(msgs: ChatMessage[]): Promise<string> {
      this.callCount++;
      const sys = msgs[0]?.content ?? '';
      const body = msgs[1]?.content ?? '';
      if (!/JSON/.test(sys)) return 'The user likes tea.';
      // consolidate prompt 里 utterance 行格式：`  - [evidence-id] 原话`（缩进 + 方括号 id），同 no-key-demo。
      const um = body
        .split('\n')
        .map((l) => l.match(/^\s+- \[([^\]]+)\] /))
        .find(Boolean);
      const eid = um ? um[1]! : 'x';
      return JSON.stringify({
        new: [
          {
            content: 'User likes tea',
            content_type: 'preference',
            formed_by: 'stated',
            support_evidence_ids: [eid],
          },
        ],
        reinforce: [],
        correct: [],
        conflict: [],
      });
    },
  };
  const nullRet = {
    async indexAll() {},
    async search() {
      return [];
    },
  };
  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: stub,
    retriever: nullRet,
    clock: fixedClock(),
  });
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

test('读路径：前进 clock → state 情绪衰减出局、fact 类留存', async () => {
  // stub：consolidate 出一条 state（情绪，半衰期 1.5 天、封顶 300）+ 一条 preference（不衰减）。
  const stub = {
    callCount: 0,
    async chat(msgs: ChatMessage[]): Promise<string> {
      this.callCount++;
      const sys = msgs[0]?.content ?? '';
      const body = msgs[1]?.content ?? '';
      if (!/JSON/.test(sys)) return 'The user is tired and likes tea.';
      const um = body
        .split('\n')
        .map((l) => l.match(/^\s+- \[([^\]]+)\] /))
        .find(Boolean);
      const eid = um ? um[1]! : 'x';
      return JSON.stringify({
        new: [
          {
            content: 'User feels tired lately',
            content_type: 'state',
            formed_by: 'stated',
            support_evidence_ids: [eid],
          },
          {
            content: 'User likes tea',
            content_type: 'preference',
            formed_by: 'stated',
            support_evidence_ids: [eid],
          },
        ],
        reinforce: [],
        correct: [],
        conflict: [],
      });
    },
  };
  let t = new Date('2026-03-01T00:00:00.000Z');
  const clock: Clock = () => t;
  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: stub,
    retriever: wordRetriever(),
    clock,
  });
  try {
    await core.ingestUserMessage({
      content: 'I am tired and I like tea',
      subjectId: 'u',
      occurredAt: '2026-03-01T00:00:00.000Z',
    });
    await core.updateProfile({ subjectId: 'u' });
    const q = 'tired tea feel drink';

    const base = await core.recall({ query: q, subjectId: 'u' });
    assert.ok(
      base.some((h) => h.content.includes('tired')),
      'base：情绪 state 被召回（有效置信高）',
    );
    assert.ok(
      base.some((h) => h.content.includes('tea')),
      'base：偏好被召回',
    );

    t = new Date('2026-03-12T00:00:00.000Z'); // 前进 11 天
    const future = await core.recall({ query: q, subjectId: 'u' });
    assert.ok(
      !future.some((h) => h.content.includes('tired')),
      '前进 11 天后：情绪 state 衰减出局（淡出，不再注入）',
    );
    assert.ok(
      future.some((h) => h.content.includes('tea')),
      '前进后：偏好（不衰减）留存',
    );
  } finally {
    core.close();
  }
});

test('core.memory 注入 clock → checkIntegrity.checkedAt 使用注入值', () => {
  const core = createMemoWeftCore({ dbPath: ':memory:', clock: fixedClock() });
  try {
    const report = core.memory.checkIntegrity();
    assert.equal(report.checkedAt, AT, 'managementApi 的 checkedAt 使用注入 clock');
  } finally {
    core.close();
  }
});

// ── 非门面路径的时钟契约：asking.askedAt 与 runLog.ts ──

test(' proposeAsk：注入 clock → askedAt = 注入值；confidence 不受影响', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    // 落在"可问带"（confidence 100–400 / credStatus low / 没问过）的低置信假设 → proposeAsk 会问它并标 askedAt。
    const h = cog.put({
      subjectId: 'u',
      content: '可能咖啡喝多了睡不着',
      contentType: 'hypothesis',
      formedBy: 'inferred',
      confidence: 200,
      credStatus: 'low',
    });
    const r = await proposeAsk('u', {
      cognitionStore: cog,
      evidenceStore: ev,
      clock: fixedClock(),
    });
    assert.equal(r.proposals.length, 1, '挑出该问的假设');
    assert.equal(cog.get(h.id)!.askedAt, AT, 'askedAt = 注入 clock');
    assert.equal(cog.get(h.id)!.confidence, 200, '注入 clock 不改变 confidence');
  } finally {
    ev.close();
    cog.close();
  }
});

test(' revisitConflicts：注入 clock → conflicted 认知的 askedAt = 注入值（confidence 不变）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const eTea = ev.put({
      subjectId: 'u',
      sourceKind: 'spoken',
      hostId: 'h',
      rawContent: '我爱喝茶',
    });
    const eCoffee = ev.put({
      subjectId: 'u',
      sourceKind: 'observed',
      hostId: 'h',
      rawContent: '这周一直在点咖啡',
    });
    const c = cog.put({
      subjectId: 'u',
      content: '用户喜欢喝茶',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'conflicted',
      evidence: [
        { evidenceId: eTea.id, relation: 'support' },
        { evidenceId: eCoffee.id, relation: 'contradict' },
      ],
    });
    const r = await revisitConflicts('u', {
      cognitionStore: cog,
      evidenceStore: ev,
      clock: fixedClock(),
    });
    assert.equal(r.proposals.length, 1, '复看那条冲突');
    assert.equal(cog.get(c.id)!.askedAt, AT, 'askedAt = 注入 clock');
    assert.equal(cog.get(c.id)!.confidence, 600, '注入 clock 不改变 confidence');
  } finally {
    ev.close();
    cog.close();
  }
});

test(' runLog：注入 clock → appendTurn/appendProfileUpdate 的 ts = 注入值', () => {
  const dir = mkdtempSync(pathJoin(tmpdir(), 'mw-runlog-'));
  try {
    const lg = createRunLogger({ dir, sessionId: 's1', clock: fixedClock() });
    assert.equal(lg.appendTurn({ userInput: 'hi' }).ts, AT, 'appendTurn ts = 注入 clock');
    assert.equal(
      lg.appendProfileUpdate({ trigger: 'manual' }).ts,
      AT,
      'appendProfileUpdate ts = 注入 clock',
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test(' 回归：不注入 clock → askedAt/ts 用真实系统时间（缺省行为不变）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  const dir = mkdtempSync(pathJoin(tmpdir(), 'mw-runlog-'));
  try {
    const h = cog.put({
      subjectId: 'u',
      content: '可能没吃早饭导致饿',
      contentType: 'hypothesis',
      formedBy: 'inferred',
      confidence: 200,
      credStatus: 'low',
    });
    const before = Date.now();
    await proposeAsk('u', { cognitionStore: cog, evidenceStore: ev }); // 不传 clock
    const at = Date.parse(cog.get(h.id)!.askedAt!);
    assert.ok(
      at >= before - 2000 && at <= Date.now() + 2000,
      'proposeAsk 缺省 askedAt = 系统时间（±2s）',
    );
    const ts = Date.parse(createRunLogger({ dir, sessionId: 's2' }).appendTurn({}).ts); // 不传 clock
    assert.ok(ts >= before - 2000 && ts <= Date.now() + 2000, 'runLog 缺省 ts = 系统时间（±2s）');
  } finally {
    ev.close();
    cog.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
