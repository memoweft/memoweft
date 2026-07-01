/**
 * TASK-03 验收测试 —— 短期对话窗口（D-024）。
 * 用 Node 内置 node:test + Mock LLM（离线确定性）。运行：`npm test`。
 * 覆盖 TASK-03 验收五条。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventStore } from '../src/dla/event/store.ts';
import { createConversation } from '../src/dla/pipeline/runner.ts';
import { WorkingMemory } from '../src/dla/pipeline/workingMemory.ts';
import { estimateTokens } from '../src/dla/config.ts';
import type { LLMClient, ChatMessage } from '../src/dla/llm/client.ts';

/** 触发"需召回"的暗号：用户最新一句里含此词，Mock 就判 needRecall。 */
const RECALL_TRIGGER = '很久以前';

/**
 * Mock LLM，区分三种 prompt：
 * - 语义解析（eventMaker 沉淀）：system 含「语义解析器」
 * - 窗口回话·二次（已召回）：system 含「刚为你检索到」
 * - 窗口回话·首次：其余
 */
class MockLLM implements LLMClient {
  private _calls = 0;
  get callCount(): number {
    return this._calls;
  }
  async chat(messages: ChatMessage[]): Promise<string> {
    this._calls++;
    const system = messages.find((m) => m.role === 'system')?.content ?? '';
    const user = messages.find((m) => m.role === 'user')?.content ?? '';

    if (system.includes('语义解析器')) {
      return JSON.stringify({
        event_form: 'explicit',
        is_directional_change: false,
        topic: '测试',
        tags: ['测试'],
        summary: '一条被沉淀的对话',
        sentiment: 'neutral',
        temporal_orientation: 'present',
      });
    }

    const lastLine = (user.split('【用户最新一句】')[1] ?? '').trim();

    if (system.includes('刚为你检索到')) {
      return JSON.stringify({ need_recall: false, recall_query: '', reply: `（已查阅记忆后回答）关于：${lastLine}` });
    }

    // 首次窗口回话：含暗号才判需召回
    if (lastLine.includes(RECALL_TRIGGER)) {
      return JSON.stringify({ need_recall: true, recall_query: `用户想回忆：${lastLine}`, reply: '' });
    }
    return JSON.stringify({ need_recall: false, recall_query: '', reply: `好的，关于「${lastLine}」我明白了` });
  }
}

test('验收1：连续对话不召回、不重演"问B引出A"', async () => {
  const store = new EventStore(':memory:');
  const convo = createConversation({ store, llm: new MockLLM() });

  await convo.handle('我们聊聊 A 项目吧');
  await convo.handle('现在说说 B 项目');
  const r = await convo.handle('B 项目进展怎么样');

  assert.equal(r.needRecall, false, 'B 在窗口内，不应触发召回');
  assert.ok(r.reply.includes('B'), '回应应指向 B，而非被 A 污染');
  assert.equal(store.readAll().length, 0, '窗口内对话期间不应写入 Event 库');
  store.close();
});

test('验收2：纯对话（窗口内）每轮仅 1 次模型调用', async () => {
  const store = new EventStore(':memory:');
  const convo = createConversation({ store, llm: new MockLLM() });

  const r1 = await convo.handle('你好');
  const r2 = await convo.handle('今天天气不错');
  assert.equal(r1.replyLlmCalls, 1, '窗口够用应仅 1 次调用');
  assert.equal(r2.replyLlmCalls, 1, '窗口够用应仅 1 次调用');
  store.close();
});

test('验收3：需要窗外旧记忆时才下沉，触发第 2 次调用', async () => {
  const store = new EventStore(':memory:');
  const convo = createConversation({ store, llm: new MockLLM() });

  const r = await convo.handle(`帮我回忆${RECALL_TRIGGER}我们定下的那件事`);
  assert.equal(r.needRecall, true, '指向窗外旧事应判需召回');
  assert.equal(r.replyLlmCalls, 2, '需召回应为 2 次调用（判断+回话 / 召回后再回话）');
  assert.ok(r.recallQuery && r.recallQuery.length > 0, '应回报补全后的检索意图');
  store.close();
});

test('验收4：滑出窗口才沉淀，窗口内不写库', async () => {
  const store = new EventStore(':memory:');
  // 小窗口（首轮 user+assistant 装得下，再来一轮就滑出）
  const convo = createConversation({ store, llm: new MockLLM() }, 60);

  // 第一轮：窗口还装得下，不应有沉淀、库为空
  const first = await convo.handle('第一句话内容');
  assert.equal(store.readAll().length, 0, '第一轮窗口内，库应为空');

  // 持续灌入，直到发生滑出
  let sawEviction = first.evicted.length > 0;
  let sawSediment = first.sedimented.length > 0;
  for (let i = 0; i < 6 && !sawSediment; i++) {
    const r = await convo.handle(`第${i + 2}句话也来占据窗口空间消耗额度`);
    if (r.evicted.length) sawEviction = true;
    if (r.sedimented.length) sawSediment = true;
  }

  assert.ok(sawEviction, '持续对话应触发窗口滑出');
  assert.ok(sawSediment, '滑出的用户轮应在【滑出时】被沉淀');
  assert.ok(store.readAll().length > 0, '沉淀后库里应有 Event');
  store.close();
});

test('验收5：窗口按 token 长度滑动（非固定轮数）', () => {
  const wm = new WorkingMemory(20); // 上限 20 token
  wm.push({ role: 'user', content: '甲' });        // 1+4=5
  wm.push({ role: 'assistant', content: '乙' });   // 1+4=5
  wm.push({ role: 'user', content: '丙' });        // 1+4=5 → 共15，未超
  assert.equal(wm.size, 3, '未超长不应滑出');

  // 再加一条中等长度内容，使总和超过 20 → 最老的逐条被挤出，直到回到上限内
  const evicted = wm.push({ role: 'assistant', content: '撑爆窗口的较长话语' }); // 9+4=13
  assert.ok(evicted.length > 0, '超过 token 上限应滑出最老轮');
  assert.ok(wm.estimatedTokens() <= 20, '滑动后应回到上限以内');

  // 证明是按长度而非轮数：同样的轮数，内容短就不滑
  const wm2 = new WorkingMemory(20);
  wm2.push({ role: 'user', content: 'a' });
  wm2.push({ role: 'assistant', content: 'b' });
  wm2.push({ role: 'user', content: 'c' });
  assert.equal(wm2.size, 3, '内容短则同样轮数不触发滑出——证明按长度不按轮数');
  void estimateTokens;
});
