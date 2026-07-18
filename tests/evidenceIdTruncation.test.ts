/**
 * Evidence-id truncation regression tests.
 *
 * Some models truncate 36-character UUIDs to an eight-character prefix, or prepend the
 * `ev-` shape used in examples. The resulting JSON is valid, but exact whitelist matching
 * would reject every citation and silently discard otherwise valid output.
 *
 * Recovery remains conservative: a prefix must resolve uniquely to a real id in the
 * supplied whitelist. Fabricated, ambiguous, and too-short ids are still rejected.
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
function closeAll(s: Stores) {
  s.ev.close();
  s.evt.close();
  s.cog.close();
  s.sr.close();
}
function deps(s: Stores, stub: { callCount: number; chat(): Promise<string> }) {
  return {
    eventStore: s.evt,
    evidenceStore: s.ev,
    cognitionStore: s.cog,
    semanticResolutionStore: s.sr,
    llm: stub,
  };
}

/** 造一条【用户主动说的】证据 + 覆盖它的事件；返回真实证据 id（36 字符 UUID）。 */
function said(s: Stores, at: string, userWord: string): string {
  const e = s.ev.put({
    subjectId: 'u',
    sourceKind: 'spoken',
    hostId: 'h',
    rawContent: userWord,
    occurredAt: at,
  });
  s.evt.put({
    subjectId: 'u',
    summary: `用户说"${userWord}"`,
    occurredAt: at,
    evidenceIds: [e.id],
  });
  return e.id;
}

/** Construct a response where both `new` and `resolutions` cite the supplied id. */
function reply(citeId: string, content = '测试用户喜欢蓝色自行车'): string {
  return JSON.stringify({
    new: [{ content, content_type: 'fact', formed_by: 'stated', support_evidence_ids: [citeId] }],
    resolutions: [
      {
        evidence_id: citeId,
        resolved_content: content,
        response_act: 'none',
        prompt_act: 'none',
        proposition_origin: 'user_stated',
        assertion_strength: 'explicit',
        required_context: '',
      },
    ],
  });
}
const stubOf = (body: string) => ({
  callCount: 0,
  async chat() {
    this.callCount++;
    return body;
  },
});
/** 同 stubOf，但把收到的 prompt 存下来供断言（测「喂进去的是什么形态的 id」）。 */
function spyStub(body: string) {
  return {
    callCount: 0,
    seen: '',
    async chat(...args: unknown[]) {
      this.callCount++;
      const msgs = args[0] as Array<{ content: string }>;
      this.seen = msgs.map((m) => m.content).join('\n');
      return body;
    },
  };
}

// ── 截断 id 的兼容解析 ──

test('模型把 evidence id 截断成 UUID 前 8 位 → 认知与解析仍应落库（不再整批蒸发）', async () => {
  const s = fresh();
  try {
    const eId = said(s, '2026-06-01T08:00:00.000Z', '我喜欢蓝色自行车，也养了一株薄荷');
    const truncated = eId.slice(0, 8);
    assert.equal(truncated.length, 8);
    assert.notEqual(truncated, eId, '前提：真 id 是 36 字符 UUID');

    const r = await consolidate('u', deps(s, stubOf(reply(truncated))));

    assert.equal(r.created.length, 1, '唯一前缀可解析时，认知应正常落库');
    assert.equal(
      s.cog.sourcesOf(r.created[0]!.id)[0]!.evidenceId,
      eId,
      '溯源必须挂到【真 id】，不是短 id',
    );
    assert.ok(
      s.sr.ofEvidence(eId),
      '解析应落库，且 evidence_id 是【真 id】（不能把短 id 写成脏数据）',
    );
  } finally {
    closeAll(s);
  }
});

test('模型照提示词示例写成 `ev-` + 前 8 位 → 同样应解回真 id', async () => {
  const s = fresh();
  try {
    const eId = said(s, '2026-06-01T08:00:00.000Z', '我喜欢喝无糖乌龙茶');
    const r = await consolidate(
      'u',
      deps(s, stubOf(reply(`ev-${eId.slice(0, 8)}`, '用户喜欢喝无糖乌龙茶'))),
    );
    assert.equal(r.created.length, 1, '剥掉示例的 ev- 前缀后应能解回真 id');
    assert.equal(s.cog.sourcesOf(r.created[0]!.id)[0]!.evidenceId, eId);
    assert.ok(s.sr.ofEvidence(eId), '解析落到真 id 上');
  } finally {
    closeAll(s);
  }
});

// ── 容错解析仍严格受白名单约束 ──

test('护栏：捏造的非前缀 id 仍被证据白名单挡掉', async () => {
  const s = fresh();
  try {
    said(s, '2026-06-01T08:00:00.000Z', '嗯');
    const r = await consolidate('u', deps(s, stubOf(reply('ai-context-only-fake'))));
    assert.equal(r.created.length, 0, '捏造 id 不是任何真 id 的前缀 → 仍不产无溯源认知');
    assert.equal(s.sr.ofEvidence('ai-context-only-fake'), null, '更不许把捏造 id 写进解析表');
  } finally {
    closeAll(s);
  }
});

test('护栏：前缀歧义（两条证据同前缀）→ 丢弃，绝不猜', async () => {
  const s = fresh();
  try {
    // 用 insert 确定性构造共享前缀的两条证据，避免依赖随机 UUID 碰撞。
    const base = {
      subjectId: 'u',
      sourceKind: 'spoken' as const,
      hostId: 'h',
      recordedAt: '2026-06-01T08:00:00.000Z',
      summary: '',
      allowLocalRead: true,
      allowCloudRead: true,
      allowInference: true,
      originId: null,
      correctsEvidenceId: null,
      precedingAiContext: null,
    };
    const a = {
      ...base,
      id: 'dup12345-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      rawContent: '前者',
      occurredAt: '2026-06-01T08:00:00.000Z',
    };
    const b = {
      ...base,
      id: 'dup12345-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      rawContent: '后者',
      occurredAt: '2026-06-01T08:01:00.000Z',
    };
    s.ev.insert(a as never);
    s.ev.insert(b as never);
    s.evt.put({
      subjectId: 'u',
      summary: '二选一',
      occurredAt: '2026-06-01T08:00:00.000Z',
      evidenceIds: [a.id, b.id],
    });
    assert.equal(a.id.slice(0, 8), b.id.slice(0, 8), '前提：两条证据共享前 8 位前缀');

    const r = await consolidate('u', deps(s, stubOf(reply('dup12345'))));
    assert.equal(r.created.length, 0, '歧义前缀 → 解不出唯一真 id → 丢弃（宁可不记，不可记错）');
    assert.equal(s.sr.ofEvidence(a.id), null, '歧义时也不许给任一候选落解析');
    assert.equal(s.sr.ofEvidence(b.id), null);
  } finally {
    closeAll(s);
  }
});

test('护栏：过短的 id（< 8 字符）不做前缀猜测', async () => {
  const s = fresh();
  try {
    said(s, '2026-06-01T08:00:00.000Z', '是');
    // 'ev-1' 正是提示词示例里的字面占位；剥掉 ev- 只剩 '1' → 太短，绝不能拿它去前缀匹配
    const r = await consolidate('u', deps(s, stubOf(reply('ev-1'))));
    assert.equal(r.created.length, 0, '示例占位 id 不该匹配到任何真证据');
  } finally {
    closeAll(s);
  }
});

test('模型写对完整 id 时行为零变化（精确匹配优先）', async () => {
  const s = fresh();
  try {
    const eId = said(s, '2026-06-01T08:00:00.000Z', '我喜欢蓝色自行车');
    const r = await consolidate('u', deps(s, stubOf(reply(eId))));
    assert.equal(r.created.length, 1);
    assert.equal(s.cog.sourcesOf(r.created[0]!.id)[0]!.evidenceId, eId);
    assert.ok(s.sr.ofEvidence(eId));
  } finally {
    closeAll(s);
  }
});

// ── Prompt 使用短证据标号，并保留 UUID 兼容解析 ──
// 短标号避免模型改写 UUID；唯一前缀与精确 UUID 解析继续承担向后兼容。

test('prompt 使用短标号 [e1]，不暴露 evidence UUID', async () => {
  const s = fresh();
  try {
    const eId = said(s, '2026-06-01T08:00:00.000Z', '我喜欢蓝色自行车');
    const stub = spyStub(reply('e1'));
    await consolidate('u', deps(s, stub));
    assert.ok(
      !stub.seen.includes(eId),
      `prompt 里不该再出现 evidence 的真 UUID，实际 prompt：\n${stub.seen}`,
    );
    assert.match(stub.seen, /\[e1\]/, 'prompt 里应发短序号 [e1]');
  } finally {
    closeAll(s);
  }
});

test('模型返回短标号 e1 → 认知与解析均关联真实 evidence id', async () => {
  const s = fresh();
  try {
    const eId = said(s, '2026-06-01T08:00:00.000Z', '我喜欢蓝色自行车');
    const r = await consolidate('u', deps(s, stubOf(reply('e1'))));
    assert.equal(r.created.length, 1, '短序号应能解回真 id');
    assert.equal(s.cog.sourcesOf(r.created[0]!.id)[0]!.evidenceId, eId, '溯源挂真 id，不是标号');
    assert.ok(s.sr.ofEvidence(eId), '解析也落到真 id 上（标号进表就是脏数据）');
  } finally {
    closeAll(s);
  }
});

test('多条证据的短标号分别映射到正确 evidence id', async () => {
  const s = fresh();
  try {
    const a = s.ev.put({
      subjectId: 'u',
      sourceKind: 'spoken',
      hostId: 'h',
      rawContent: '我喜欢蓝色自行车',
      occurredAt: '2026-06-01T08:00:00.000Z',
    });
    const b = s.ev.put({
      subjectId: 'u',
      sourceKind: 'spoken',
      hostId: 'h',
      rawContent: '我养了一株薄荷',
      occurredAt: '2026-06-01T08:01:00.000Z',
    });
    s.evt.put({
      subjectId: 'u',
      summary: '自述',
      occurredAt: '2026-06-01T08:00:00.000Z',
      evidenceIds: [a.id, b.id],
    });
    // 模型只引第二条（e2）→ 必须精确挂到 b、绝不能串到 a
    const r = await consolidate('u', deps(s, stubOf(reply('e2', '用户养了一株薄荷'))));
    assert.equal(r.created.length, 1);
    assert.equal(s.cog.sourcesOf(r.created[0]!.id)[0]!.evidenceId, b.id, 'e2 必须解到第二条证据');
    assert.ok(s.sr.ofEvidence(b.id), 'e2 的解析落在 b 上');
    assert.equal(s.sr.ofEvidence(a.id), null, 'a 没被引用 → 不该有解析');
  } finally {
    closeAll(s);
  }
});

test('越界短标号 e99 无法解析时丢弃候选', async () => {
  const s = fresh();
  try {
    said(s, '2026-06-01T08:00:00.000Z', '我喜欢蓝色自行车');
    const r = await consolidate('u', deps(s, stubOf(reply('e99'))));
    assert.equal(r.created.length, 0, '越界标号不该匹配到任何证据');
  } finally {
    closeAll(s);
  }
});

test('向后兼容：模型返回完整 UUID 时继续精确匹配', async () => {
  const s = fresh();
  try {
    const eId = said(s, '2026-06-01T08:00:00.000Z', '我喜欢蓝色自行车');
    const r = await consolidate('u', deps(s, stubOf(reply(eId))));
    assert.equal(r.created.length, 1, '完整 UUID 应继续按精确匹配解析');
    assert.equal(s.cog.sourcesOf(r.created[0]!.id)[0]!.evidenceId, eId);
  } finally {
    closeAll(s);
  }
});

// ── 解析覆盖率诊断 ──
//
// A database's final state cannot distinguish “the model returned nothing” from “the model
// returned ids that were all rejected”. These tests preserve the diagnostic signal.

/** 捕获 console.warn，返回 [告警数组, 还原函数]。 */
function captureWarn(): [string[], () => void] {
  const warns: string[] = [];
  const real = console.warn;
  console.warn = (...a: unknown[]) => {
    warns.push(a.map(String).join(' '));
  };
  return [
    warns,
    () => {
      console.warn = real;
    },
  ];
}

test('仪表：模型产了解析、却一条都没落地 → 落告警（含模型写的 id 形态，一眼看出问题）', async () => {
  const s = fresh();
  const [warns, restore] = captureWarn();
  try {
    said(s, '2026-06-01T08:00:00.000Z', '我喜欢蓝色自行车');
    // 模型产了解析，但 id 全是认不出的（非任何真 id 前缀）→ 全被白名单挡掉
    await consolidate('u', deps(s, stubOf(reply('totally-bogus-id'))));
    const hit = warns.find((w) => w.includes('[memoweft/consolidate]'));
    assert.ok(hit, `应落一条 consolidate 告警，实得：${JSON.stringify(warns)}`);
    assert.match(
      hit!,
      /totally-bogus-id/,
      '告警要带上模型写的 id 形态——这正是判别 id 契约破裂的钥匙',
    );
  } finally {
    restore();
    closeAll(s);
  }
});

test('模型产出可解析 id 时不记录覆盖率告警', async () => {
  const s = fresh();
  const [warns, restore] = captureWarn();
  try {
    const eId = said(s, '2026-06-01T08:00:00.000Z', '我喜欢蓝色自行车');
    await consolidate('u', deps(s, stubOf(reply(eId))));
    assert.equal(warns.filter((w) => w.includes('[memoweft/consolidate]')).length, 0);
  } finally {
    restore();
    closeAll(s);
  }
});

test('模型未产出 resolutions 时不记录覆盖率告警', async () => {
  const s = fresh();
  const [warns, restore] = captureWarn();
  try {
    said(s, '2026-06-01T08:00:00.000Z', '我喜欢蓝色自行车');
    // 四类全空、无 resolutions 字段 —— 同 writePathMetrics.test.ts 的 emptyOutputStub
    await consolidate('u', deps(s, stubOf('{"new":[],"reinforce":[],"correct":[],"conflict":[]}')));
    assert.equal(
      warns.filter((w) => w.includes('[memoweft/consolidate]')).length,
      0,
      '「模型没产」与「产了但落不了地」是两回事，只告警后者',
    );
  } finally {
    restore();
    closeAll(s);
  }
});
