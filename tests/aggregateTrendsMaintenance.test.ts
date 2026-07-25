/**
 * aggregateTrends 门面维护入口（后台维护·独立入口）：core.aggregateTrends() 规则筛「窗口内同类 state
 * 反复出现」→ 写模型命名成 trend 认知（formed_by=ruled）。是 trend 类的唯一生产产出者。
 *
 * 纯离线：stub LLM（区分 distill / consolidate / trends 三种 prompt）+ null 召回 + 内存库 + 注入时钟。
 * 只验证【门面接线】：subjectId 归一 / 写模型透传 / 注入时钟 / 真调算子 + subject 隔离。
 * 算子本体（频率阈值、窗口、dedup、ruled）见 trends.test.ts；此处不重复。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createMemoWeftCore } from '../src/core/createCore.ts';
import type { Clock } from '../src/clock.ts';
import type { ChatMessage } from '../src/llm/client.ts';

const T0 = '2026-06-20T00:00:00.000Z';

/** 区分三种 prompt：trends（按 body 标记，须先判，因它系统词也含 JSON）→ consolidate（含 JSON）→ distill。 */
function stubLLM() {
  return {
    callCount: 0,
    async chat(msgs: ChatMessage[]): Promise<string> {
      this.callCount++;
      const sys = msgs[0]?.content ?? '';
      const body = msgs[1]?.content ?? '';
      // ① trends：body 有「近期反复出现的状态 / Recent recurring states」标记；引用短标号 e1/e2…
      if (/近期反复出现的状态|Recent recurring states/.test(body)) {
        const tags = [...body.matchAll(/\[(e\d+)\]/g)].map((m) => m[1]);
        return `{"trends":[{"content":"用户最近这段时间持续疲惫","based_on_evidence_ids":${JSON.stringify(tags)}}]}`;
      }
      // ② consolidate：系统词含 JSON；给每条原话产一条 state 认知（供趋势聚合）。
      if (/JSON/.test(sys)) {
        const eids = [...body.matchAll(/^\s+- \[([^\]]+)\] /gm)].map((m) => m[1]);
        return JSON.stringify({
          new: eids.map((eid, i) => ({
            content: `用户状态${i}`,
            content_type: 'state',
            formed_by: 'stated',
            support_evidence_ids: [eid],
          })),
          reinforce: [],
          correct: [],
          conflict: [],
        });
      }
      // ③ distill
      return 'The user feels tired.';
    },
  };
}

const nullRet = {
  async indexAll() {},
  async search() {
    return [];
  },
};

test('core.aggregateTrends()：空画像 → 不崩、返回 TrendResult 形状（consideredCount 0、无产出）', async () => {
  const clock: Clock = () => new Date(T0);
  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: stubLLM(),
    retriever: nullRet,
    clock,
  });
  try {
    const r = await core.aggregateTrends({ subjectId: 'u' });
    assert.equal(r.consideredCount, 0);
    assert.equal(r.trends.length, 0);
    assert.equal(r.llmCalls, 0, '不够频不调模型');
  } finally {
    core.close();
  }
});

test('core.aggregateTrends()：窗口内够频 state → 聚出 trend；subjectId/写模型/时钟真接进算子', async () => {
  const clock: Clock = () => new Date(T0);
  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: stubLLM(),
    retriever: nullRet,
    clock,
  });
  try {
    // 三条原话（都在 trendWindowDays=14 窗口内，同一 now）→ 一次 updateProfile 产 3 条 state 认知。
    for (const c of ['我很烦', '又没睡好', '还是提不起劲']) {
      await core.ingestUserMessage({ content: c, subjectId: 'u', occurredAt: T0 });
    }
    await core.updateProfile({ subjectId: 'u' });
    const states = core.memory
      .listCognitions({ subjectId: 'u' })
      .filter((c) => c.contentType === 'state');
    assert.equal(states.length, 3, '产出 3 条 state（趋势聚合的输入）');

    const r = await core.aggregateTrends({ subjectId: 'u' });
    assert.equal(
      r.consideredCount,
      3,
      '窗口内 3 条 state 证据被考察（证明 subjectId + 算子真接进来）',
    );
    assert.equal(r.trends.length, 1, '够频 → 聚出 1 条 trend');
    assert.equal(r.trends[0]!.contentType, 'trend');
    assert.equal(r.trends[0]!.formedBy, 'ruled', '规则聚出（基于客观频率）');

    // subject 隔离：别的 subject 无数据 → 0（证明 subjectId 真接进算子、非默认串号）。
    const other = await core.aggregateTrends({ subjectId: 'nobody' });
    assert.equal(other.consideredCount, 0);
    assert.equal(other.trends.length, 0);
  } finally {
    core.close();
  }
});
