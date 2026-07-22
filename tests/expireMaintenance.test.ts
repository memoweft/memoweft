/**
 * expire 门面维护入口（方案①·独立入口）：core.expire() 把临时类（state/hypothesis/trend）久未印证的
 * 认知标 invalidAt（失效不删、保留可溯源、不再召回）；稳定类（preference/fact 等）永不自动失效。
 *
 * 纯离线：stub LLM + null 召回 + 内存库 + 可前进的注入时钟（时间旅行）。
 * 只验证【门面接线】：subjectId 归一 / 注入时钟透传 / 返回条数 / 幂等 / subject 隔离。
 * 算子本体（阈值、active() 语义、归档不动）见 background.test.ts；此处不重复。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createMemoWeftCore } from '../src/core/createCore.ts';
import type { Clock } from '../src/clock.ts';
import type { ChatMessage } from '../src/llm/client.ts';

const T0 = '2026-03-01T00:00:00.000Z';
/** state 阈值 7 天（config.background.expireAfterDays）；前进到第 10 天 → state 过期、preference 不动。 */
const T_PLUS_10 = '2026-03-11T00:00:00.000Z';

/** 极简离线 stub：distill 出一句；consolidate 出一条 state（情绪·7 天过期）+ 一条 preference（不过期）。 */
function stubLLM() {
  return {
    callCount: 0,
    async chat(msgs: ChatMessage[]): Promise<string> {
      this.callCount++;
      const sys = msgs[0]?.content ?? '';
      const body = msgs[1]?.content ?? '';
      if (!/JSON/.test(sys)) return 'The user feels tired and likes tea.';
      // consolidate prompt 里 utterance 行格式：`  - [evidence-id] 原话`（缩进 + 方括号 id）。
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
}

const nullRet = {
  async indexAll() {},
  async search() {
    return [];
  },
};

test('core.expire()：前进过 state 阈值 → 临时类标失效、稳定类不动、返回条数、invalidAt 用注入时钟', async () => {
  let t = new Date(T0);
  const clock: Clock = () => t;
  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: stubLLM(),
    retriever: nullRet,
    clock,
  });
  try {
    await core.ingestUserMessage({
      content: 'I am tired and I like tea',
      subjectId: 'u',
      occurredAt: T0,
    });
    await core.updateProfile({ subjectId: 'u' });

    const before = core.memory.listCognitions({ subjectId: 'u' });
    assert.equal(before.length, 2, '产出 state + preference 两条');
    assert.ok(
      before.every((c) => c.invalidAt == null),
      '过期前两条都有效',
    );

    t = new Date(T_PLUS_10); // 时间旅行：第 10 天
    const r = core.expire({ subjectId: 'u' });
    assert.equal(r.expired, 1, '只过期 1 条（state 情绪；preference 不在过期名单）');

    const after = core.memory.listCognitions({ subjectId: 'u' });
    const state = after.find((c) => c.contentType === 'state');
    const pref = after.find((c) => c.contentType === 'preference');
    assert.ok(
      state?.invalidAt,
      'state 被标 invalidAt（失效不删、仍在 listCognitions 可见、可溯源）',
    );
    assert.equal(
      state!.invalidAt,
      T_PLUS_10,
      'invalidAt = 注入时钟当前值（证明 core 把 clock 透传给算子）',
    );
    assert.equal(pref?.invalidAt, null, 'preference 永不自动失效');
  } finally {
    core.close();
  }
});

test('core.expire()：幂等（已失效不重复标）+ subject 隔离（subjectId 真的接进算子、不串号）', async () => {
  let t = new Date(T0);
  const clock: Clock = () => t;
  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: stubLLM(),
    retriever: nullRet,
    clock,
  });
  try {
    await core.ingestUserMessage({
      content: 'I am tired and I like tea',
      subjectId: 'u',
      occurredAt: T0,
    });
    await core.updateProfile({ subjectId: 'u' });

    t = new Date(T_PLUS_10);
    // 别的 subject 没有数据 → 0：证明 subjectId 真被接进算子，不是默认串号（memoweft-subjectid-default-trap）。
    assert.equal(core.expire({ subjectId: 'other' }).expired, 0, '空 subject 过期 0 条');
    assert.equal(core.expire({ subjectId: 'u' }).expired, 1, '首次过期 1 条');
    assert.equal(core.expire({ subjectId: 'u' }).expired, 0, '再跑幂等：已失效不再计入');
  } finally {
    core.close();
  }
});
