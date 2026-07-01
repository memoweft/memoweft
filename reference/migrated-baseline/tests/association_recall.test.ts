/**
 * TASK-04 验收测试 —— Association 真实召回（A1 topic 粗筛）。
 * 用 Node 内置 node:test + Mock LLM（离线确定性）。运行：`npm test`。
 * 覆盖 TASK-04 验收 1/2/3/5（验收4 全链路、验收6 回归另见真跑与既有用例）。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventStore } from '../src/dla/event/store.ts';
import { association } from '../src/dla/pipeline/association.ts';
import type { EventInput } from '../src/dla/event/model.ts';
import type { LLMClient, ChatMessage } from '../src/dla/llm/client.ts';

/** Mock：topic 粗筛时，返回"现有清单里与意图字面相关"的 topic（模拟模型语义挑选）。 */
class TopicPickMock implements LLMClient {
  private _calls = 0;
  lastUserContent = '';
  get callCount(): number {
    return this._calls;
  }
  async chat(messages: ChatMessage[]): Promise<string> {
    this._calls++;
    const user = messages.find((m) => m.role === 'user')?.content ?? '';
    this.lastUserContent = user;
    // 解析出意图与清单
    const intent = (user.split('【用户当前意图】')[1] ?? '').split('【')[0] ?? '';
    const listBlock = user.split('【库里已有的话题清单】')[1] ?? '';
    const topics = listBlock.split('\n').map((l) => l.replace(/^-\s*/, '').trim()).filter(Boolean);
    // 简单语义模拟：意图里包含该 topic 文字，或 topic 是意图关键词，即算相关
    const picked = topics.filter((t) => intent.includes(t));
    return JSON.stringify(picked);
  }
}

function ev(topic: string, summary: string): EventInput {
  return {
    raw_content: summary,
    event_form: 'explicit',
    is_directional_change: false,
    topic,
    tags: [topic],
    summary,
    sentiment: 'neutral',
    source_type: 'user',
    temporal_orientation: 'present',
    related_event_ids: [],
    correction_target_id: null,
  };
}

function seed(store: EventStore) {
  store.write(ev('志向', '用户大学时立志做AI'));
  store.write(ev('作息', '用户决定早睡'));
  store.write(ev('项目', '用户在做星瑶项目'));
}

test('验收1：召回真的发生——只返回 topic 相关的那几条', async () => {
  const store = new EventStore(':memory:');
  seed(store);
  const llm = new TopicPickMock();

  const r = await association('用户大学时立的志向是什么', store, llm);
  assert.deepEqual(r.matchedTopics, ['志向'], '应只命中"志向"这个 topic');
  assert.equal(r.recalled.length, 1, '应只召回 1 条相关 Event');
  assert.equal(r.recalled[0]!.topic, '志向');
  store.close();
});

test('验收2：召回用的是 recallQuery 文字（非 event）', async () => {
  const store = new EventStore(':memory:');
  seed(store);
  const llm = new TopicPickMock();

  const query = '用户大学时立的志向';
  await association(query, store, llm);
  assert.ok(llm.lastUserContent.includes(query), 'topic 粗筛的输入应包含 recallQuery 原文');
  store.close();
});

test('验收3：不相关的不召回——返回空', async () => {
  const store = new EventStore(':memory:');
  seed(store);
  const llm = new TopicPickMock();

  const r = await association('关于烹饪和菜谱的事', store, llm);
  assert.deepEqual(r.matchedTopics, [], '无相关 topic 应命中空');
  assert.equal(r.recalled.length, 0, '不相关时不应捞出任何 Event');
  store.close();
});

test('验收5：召回过程只读不写（D-003）', async () => {
  const store = new EventStore(':memory:');
  seed(store);
  const before = store.readAll().length;

  await association('用户大学时立的志向', store, new TopicPickMock());

  assert.equal(store.readAll().length, before, '召回不应改变库内 Event 数量');
  store.close();
});

test('空库：无 topic 时直接返回空，且不调用模型', async () => {
  const store = new EventStore(':memory:');
  const llm = new TopicPickMock();
  const r = await association('任何意图', store, llm);
  assert.deepEqual(r.recalled, []);
  assert.equal(llm.callCount, 0, '空库应短路，不浪费模型调用');
  store.close();
});

test('store 只读查询：distinctTopics / findByTopics 行为正确', () => {
  const store = new EventStore(':memory:');
  seed(store);
  store.write(ev('项目', '又一条项目相关')); // 重复 topic

  const topics = store.distinctTopics().sort();
  assert.deepEqual(topics, ['作息', '志向', '项目'], 'distinct 应去重');

  const byProj = store.findByTopics(['项目']);
  assert.equal(byProj.length, 2, '项目 topic 有两条');
  assert.deepEqual(store.findByTopics([]), [], '空 topic 列表返回空');

  // 两条项目相关 Event 都应被召回（同毫秒写入时同 timestamp，组内顺序不保证；
  // 真正的排序待 TASK-05 换成权重排序）
  const summaries = byProj.map((e) => e.summary).sort();
  assert.deepEqual(summaries, ['用户在做星瑶项目', '又一条项目相关'].sort());
  store.close();
});
