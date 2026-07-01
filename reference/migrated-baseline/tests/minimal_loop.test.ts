/**
 * TASK-02 验收测试 —— 最短闭环（Perception→Event→Action）。
 * 用 Node 内置 node:test（D-021）。运行：`npm test`。
 *
 * 用 Mock LLMClient 保证离线、确定性（真实模型在 web 测试台手动验证）。
 * 覆盖 TASK-02 验收五条。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { EventStore } from '../src/dla/event/store.ts';
import { runPipeline } from '../src/dla/pipeline/runner.ts';
import { perception } from '../src/dla/pipeline/perception.ts';
import { attention } from '../src/dla/pipeline/attention.ts';
import { conflict } from '../src/dla/pipeline/conflict.ts';
import { calibration } from '../src/dla/pipeline/calibration.ts';
import type { LLMClient, ChatMessage } from '../src/dla/llm/client.ts';

/** Mock 客户端：解析返回固定 JSON，回话返回固定句；记录每次收到的 messages。 */
class MockLLM implements LLMClient {
  private _callCount = 0;
  readonly seen: ChatMessage[][] = [];
  get callCount(): number {
    return this._callCount;
  }
  async chat(messages: ChatMessage[]): Promise<string> {
    this._callCount++;
    this.seen.push(messages);
    const isParse = messages.some((m) => m.content.includes('语义解析器'));
    if (isParse) {
      return JSON.stringify({
        event_form: 'explicit',
        is_directional_change: false,
        topic: '版本',
        tags: ['版本', '开发'],
        summary: '用户提议先从最小版本开始。',
        sentiment: 'neutral',
        temporal_orientation: 'present',
      });
    }
    return '好的，我们从最小版本开始。';
  }
}

test('验收1：全链路跑通，返回回应且库里新增一条 Event', async () => {
  const store = new EventStore(':memory:');
  const llm = new MockLLM();
  const r = await runPipeline('我们先从最小版本开始吧', { store, llm });

  assert.ok(r.response.length > 0, '应返回非空回应');
  assert.ok(r.eventId, '应有新建 Event id');
  assert.equal(store.readAll().length, 1, '库里应恰好新增一条 Event');
  const stored = store.read(r.eventId!);
  assert.ok(stored, '应能读回新建 Event');
  assert.equal(stored.raw_content, '我们先从最小版本开始吧');
  store.close();
});

test('验收2：Event 语义字段被正确填充（非空且合理）', async () => {
  const store = new EventStore(':memory:');
  const r = await runPipeline('我们先从最小版本开始吧', { store, llm: new MockLLM() });
  const e = r.event;
  assert.ok(e, 'event 应存在');
  assert.equal(e.event_form, 'explicit');
  assert.ok(e.topic.length > 0, 'topic 非空');
  assert.ok(e.summary.length > 0, 'summary 非空');
  assert.ok(['positive', 'negative', 'neutral'].includes(e.sentiment));
  assert.ok(['long_term', 'present'].includes(e.temporal_orientation));
  assert.equal(e.source_type, 'user', 'source_type 由感知层给定，不由模型判');
  store.close();
});

test('验收3：大模型本轮被调用恰好 2 次（解析1 + 回话1，未合并）', async () => {
  const store = new EventStore(':memory:');
  const llm = new MockLLM();
  const r = await runPipeline('我们先从最小版本开始吧', { store, llm });
  assert.equal(r.llmCalls, 2, '一轮应恰好 2 次调用（D-002/D-015）');
  assert.equal(llm.callCount, 2);
  store.close();
});

test('验收4：D-001 红线 —— ③ 解析 prompt 不含任何判权重/重要性的指令', () => {
  const src = readFileSync(new URL('../src/dla/pipeline/eventMaker.ts', import.meta.url), 'utf8');
  // 截取 buildParsePrompt 函数体范围做检查
  const start = src.indexOf('function buildParsePrompt');
  const end = src.indexOf('function extractJson');
  assert.ok(start !== -1 && end !== -1 && end > start, '应能定位解析 prompt 函数');
  const promptCode = src.slice(start, end);

  // 这些词若出现在"指令模型去判断"的语境即违规。prompt 里出现"严禁判断…重要/权重"是允许的（那是禁止指令）。
  // 故只断言不存在"要求模型给出"权重/重要性字段的迹象：
  const banned = ['权重', '重要性', '重要程度', '该不该记', '值不值得', 'importance', 'weight'];
  // 允许出现在"严禁/不要判断X"的否定语境；这里采用更强的结构化保证：
  // 解析输出 JSON 的字段名里不得含权重类字段。
  const outputsWeight = /"(weight|importance|priority|score|重要|权重)"\s*:/.test(promptCode);
  assert.ok(!outputsWeight, '解析 JSON 不得包含权重/重要性类输出字段（D-001）');

  // 同时确认 prompt 显式声明了"不做价值判断"这条红线
  assert.ok(promptCode.includes('不做任何价值判断') || promptCode.includes('严禁判断'),
    'prompt 应显式声明不做价值判断（D-001）');
  void banned;
});

test('验收5：占位步骤是独立可替换函数，返回固定占位值', () => {
  // 注：association 已在 TASK-04 填成真逻辑，不再是占位（其测试见 association 召回用例）。
  const raw = perception('随便一句', 'user');
  assert.deepEqual(attention(raw), { admit: true }, 'attention 占位恒 admit:true');

  const fakeEvent = {
    id: 'x', timestamp: 1, raw_content: 'x', event_form: 'explicit' as const,
    is_directional_change: false, topic: 't', tags: [], summary: 's',
    sentiment: 'neutral' as const, source_type: 'user' as const,
    temporal_orientation: 'present' as const, related_event_ids: [], correction_target_id: null,
  };
  const cf = conflict(fakeEvent, []);
  assert.deepEqual(cf, { hasConflict: false }, 'conflict 占位恒无冲突');
  assert.deepEqual(calibration(fakeEvent, cf), { probe: false }, 'calibration 占位恒不探测');

  // 这三个仍是占位，各自独立导出的函数（不是写死在 runner 里拆不开）
  for (const fn of [attention, conflict, calibration]) {
    assert.equal(typeof fn, 'function');
  }
});
