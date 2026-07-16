/**
 * 交互上下文捕获（v0.6 · D-0034）：裸 ingestUserMessage 路的会话上下文 + episode + 结构墙。
 *
 * 重点验证【头号问题修复】：weftmate 全走裸 ingestUserMessage、从不经 Conversation 路 —— 让 core 承担上下文管理后，
 *   上一轮 AI（经 recordAssistantReply 报告）能被下一轮 ingest 捕获进 preceding_ai_context → distill 注入生效。
 * 并守结构墙（3a）：AI 文本永不经 listEvidence / exportBundle 的证据泄漏。全离线（distill 用 stub LLM）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createMemoWeftCore } from '../src/core/index.ts';
import { InteractionSession } from '../src/pipeline/interactionSession.ts';

// ── InteractionSession 单元 ──

test('InteractionSession：preceding = 最近 assistant；首轮无 AI → null', () => {
  const s = new InteractionSession();
  assert.equal(s.beginUserTurn(0).precedingAiContext, null, '首轮无上一轮 AI');
  s.pushUser('你好');
  s.pushAssistant('你喜欢爬山吧?');
  const t = s.beginUserTurn(1000);
  assert.equal(t.precedingAiContext, '你喜欢爬山吧?', '抓最近一轮 assistant');
});

test('InteractionSession：episode 切分 —— 短间隔同 episode / idle 超阈新 episode / explicit 覆盖', () => {
  const s = new InteractionSession({ idleMs: 60_000 });
  const t1 = s.beginUserTurn(0);
  s.pushUser('a');
  const t2 = s.beginUserTurn(30_000); // 30s < 60s
  assert.equal(t2.episodeId, t1.episodeId, '短间隔 → 同 episode');
  s.pushUser('b');
  const t3 = s.beginUserTurn(300_000); // 距上轮 270s > 60s
  assert.notEqual(t3.episodeId, t2.episodeId, 'idle 超阈 → 新 episode');
  const t4 = s.beginUserTurn(300_001, 'ep-fixed');
  assert.equal(t4.episodeId, 'ep-fixed', 'explicit episodeId 优先');
});

// ── core 端到端：捕获 + 头号问题修复 ──

test('裸 ingest 路端到端：上一轮 AI（recordAssistantReply）→ 下一轮 ingest 捕获进 distill（头号问题修复）', async () => {
  const captured: string[] = [];
  const stub = {
    callCount: 0,
    async chat(messages: Array<{ role: string; content: string }>) {
      this.callCount++;
      captured.push(JSON.stringify(messages));
      return '用户确认喜欢爬山。';
    },
  };
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stub });
  try {
    await core.ingestUserMessage({ content: '你好', conversationId: 'c1', occurredAt: '2026-07-16T08:00:00.000Z' });
    core.recordAssistantReply({ conversationId: 'c1', content: '你喜欢爬山吧?' });
    await core.ingestUserMessage({ content: '是的', conversationId: 'c1', occurredAt: '2026-07-16T08:01:00.000Z' });
    try {
      await core.updateProfile(); // distill 时已捕获输入；consolidate/attribute 的 stub 非 JSON 无产出（不影响本断言）
    } catch {
      /* 忽略下游步骤错误，distill 输入已在 captured */
    }
    const all = captured.join('\n');
    assert.ok(all.includes('你喜欢爬山吧?'), 'distill 输入带上一轮 AI 上文（裸 ingest 路捕获生效 = 头号问题修复）');
    assert.ok(all.includes('是的'), 'distill 输入带用户原话');
  } finally {
    core.close();
  }
});

test('结构墙（3a）：上一轮 AI 文本不经 exportBundle 证据泄漏；却作为 interaction_context 合法保存', async () => {
  const core = createMemoWeftCore({ dbPath: ':memory:' });
  try {
    await core.ingestUserMessage({ content: '你好', conversationId: 'c1' });
    core.recordAssistantReply({ conversationId: 'c1', content: '你喜欢爬山吧?' });
    await core.ingestUserMessage({ content: '是的', conversationId: 'c1' });

    const bundle = core.portable.exportBundle();
    // 只有两条用户证据；AI 回复没变成证据
    assert.equal(bundle.data.evidence.length, 2, 'recordAssistantReply 的 AI 回复不落证据');
    assert.ok(!JSON.stringify(bundle.data.evidence).includes('你喜欢爬山吧?'), 'AI 文本不在任何证据（结构墙）');
    // interaction_context 落了库，且含 AI 文本（合法——它是上下文、不是证据）
    assert.ok(bundle.data.interactionContexts && bundle.data.interactionContexts.length >= 1, 'interaction_context 落库');
    const ctxText = JSON.stringify(bundle.data.interactionContexts);
    assert.ok(ctxText.includes('你喜欢爬山吧?'), 'AI 文本在 interaction_context（合法上下文）');
    assert.ok(ctxText.includes('是的'), '本轮用户话也在上下文快照');
  } finally {
    core.close();
  }
});

test('不带 conversationId：行为同旧（不落 interaction_context、preceding 恒 null）', async () => {
  const core = createMemoWeftCore({ dbPath: ':memory:' });
  try {
    await core.ingestUserMessage({ content: '我喜欢喝茶' });
    const bundle = core.portable.exportBundle();
    assert.equal(bundle.data.evidence.length, 1);
    assert.equal((bundle.data.interactionContexts ?? []).length, 0, '无 conversationId → 不落交互上下文');
  } finally {
    core.close();
  }
});

test('recordAssistantReply：未 ingest 过的会话 → 静默略过（不崩）', () => {
  const core = createMemoWeftCore({ dbPath: ':memory:' });
  try {
    assert.doesNotThrow(() => core.recordAssistantReply({ conversationId: 'never', content: 'hi' }));
  } finally {
    core.close();
  }
});

test('便携包往返：interaction_context 导出→导入→再导出 一致', async () => {
  const src = createMemoWeftCore({ dbPath: ':memory:' });
  let bundle;
  try {
    await src.ingestUserMessage({ content: '你好', conversationId: 'c1' });
    src.recordAssistantReply({ conversationId: 'c1', content: '你喜欢爬山吧?' });
    await src.ingestUserMessage({ content: '是的', conversationId: 'c1' });
    bundle = src.portable.exportBundle();
  } finally {
    src.close();
  }
  assert.ok(bundle.data.interactionContexts && bundle.data.interactionContexts.length >= 1);

  const dst = createMemoWeftCore({ dbPath: ':memory:' });
  try {
    const plan = dst.portable.importBundle(bundle, { mode: 'merge' });
    assert.equal(plan.valid, true);
    assert.equal(plan.counts.interactionContexts, bundle.data.interactionContexts.length, '导入交互上下文条数一致');
    const round = dst.portable.exportBundle();
    assert.deepEqual(
      (round.data.interactionContexts ?? []).map((c) => c.id).sort(),
      bundle.data.interactionContexts.map((c) => c.id).sort(),
      '往返后 interaction_context id 集一致',
    );
  } finally {
    dst.close();
  }
});
