/**
 * adapter-openai-agents · v0.6 会话上下文（recordAssistantReply）单元测试。
 *
 * 覆盖 run 包装器新增的 ④ 上下文线（薄补 0.6 面，不启真实 SDK）：
 *   - finalAssistantText：从 RunResult 提【本轮 AI 最终回复】文本（finalOutput string / message_output_item / 结构化输出）。
 *   - recordFinalReply：门控（能力探测 + conversationId + 非空文本）+ 「AI 回复永不落证据、只进上下文窗口」（tool-result-only ingestion boundary）。
 *
 * 与 adapterContract.test.ts 的 assistant-output exclusion 互补：assistant-output exclusion 证 persistToolOutputs 不摄助手侧 item；本文件证 recordAssistantReply
 *   这条 0.6 上下文线也【绝不】把助手回复写成证据（它只经 recordAssistantReply 进上下文窗口）。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { RunItem } from '@openai/agents';
import { finalAssistantText, recordFinalReply } from '../src/index.ts';

// ── RunResult 构造夹具（只填 finalAssistantText 实际读的字段；cast 成 RunItem 供公开签名）──
function msgOutputItem(text: string): RunItem {
  return {
    type: 'message_output_item',
    rawItem: { role: 'assistant', content: [{ type: 'output_text', text }] },
  } as unknown as RunItem;
}
function toolOutputItem(): RunItem {
  return {
    type: 'tool_call_output_item',
    output: '{"ok":true}',
    rawItem: { callId: 'c1' },
  } as unknown as RunItem;
}

test('finalAssistantText：finalOutput 为非空 string → 直接用', () => {
  assert.equal(finalAssistantText({ finalOutput: 'Hello there' }), 'Hello there');
});

test('finalAssistantText：finalOutput 非串 → 从最后一条 message_output_item 拼 output_text', () => {
  const result = {
    finalOutput: { structured: true },
    newItems: [toolOutputItem(), msgOutputItem('the reply')],
  };
  assert.equal(finalAssistantText(result), 'the reply');
});

test('finalAssistantText：无 finalOutput 文本、无 message_output_item → null', () => {
  assert.equal(finalAssistantText({ finalOutput: undefined, newItems: [toolOutputItem()] }), null);
  assert.equal(finalAssistantText({ finalOutput: '   ', newItems: [msgOutputItem('   ')] }), null);
});

test('recordFinalReply：0.6 Core + conversationId + 有回复 → 调 recordAssistantReply，内容=最终回复', () => {
  const calls: Array<{ conversationId: string; content: string }> = [];
  const core = {
    recordAssistantReply: (i: { conversationId: string; content: string }) => {
      calls.push(i);
    },
  };
  const ok = recordFinalReply(core, { finalOutput: 'yes I can' }, 'conv-1');
  assert.equal(ok, true);
  assert.deepEqual(calls, [{ conversationId: 'conv-1', content: 'yes I can' }]);
});

test('recordFinalReply：0.5 Core（无 recordAssistantReply）→ 不调、返回 false（能力探测降级）', () => {
  const core = {}; // 无 recordAssistantReply
  assert.equal(recordFinalReply(core, { finalOutput: 'hi' }, 'conv-1'), false);
});

test('recordFinalReply：未传 conversationId → 不调（无会话上下文，行为同旧）', () => {
  let called = false;
  const core = {
    recordAssistantReply: () => {
      called = true;
    },
  };
  assert.equal(recordFinalReply(core, { finalOutput: 'hi' }, undefined), false);
  assert.equal(called, false);
});

test('recordFinalReply：无可提取的非空回复 → 不调（结构化输出/空回复）', () => {
  let called = false;
  const core = {
    recordAssistantReply: () => {
      called = true;
    },
  };
  assert.equal(recordFinalReply(core, { finalOutput: { obj: 1 }, newItems: [] }, 'conv-1'), false);
  assert.equal(called, false);
});

test('recordFinalReply：recordAssistantReply 抛错 → 静默吞、返回 false（记忆层出错不崩对话）', () => {
  const core = {
    recordAssistantReply: () => {
      throw new Error('boom');
    },
  };
  assert.equal(recordFinalReply(core, { finalOutput: 'hi' }, 'conv-1'), false); // 不向外抛
});
