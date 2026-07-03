/**
 * TASK-05 验收测试 —— 权重计算（D-007）+ 召回权重排序。
 * 用 Node 内置 node:test（D-021）。运行：`npm test`。
 * 覆盖验收 1-6（验收7 回归＝全套 npm test + tsc）。
 *
 * 注：经产品所有者确认（D-018），最终权重为【派生排序分，不设上限】，
 * 故验收1 只断言"恒 >0 且整数"，不再要求 ≤1000。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { rmSync } from 'node:fs';
import { EventStore } from '../src/dla/event/store.ts';
import { computeWeight } from '../src/dla/event/weight.ts';
import { association } from '../src/dla/pipeline/association.ts';
import type { EventInput, Event } from '../src/dla/event/model.ts';
import type { LLMClient, ChatMessage } from '../src/dla/llm/client.ts';

function ev(over: Partial<EventInput> = {}): EventInput {
  return {
    raw_content: 'x',
    event_form: 'explicit',
    is_directional_change: false,
    topic: 'T',
    tags: [],
    summary: 's',
    sentiment: 'neutral',
    source_type: 'user',
    temporal_orientation: 'long_term',
    related_event_ids: [],
    correction_target_id: null,
    ...over,
  };
}

/** 取回刚写入的 Event 对象（computeWeight 吃 Event）。 */
function writeAndRead(store: EventStore, input: EventInput): Event {
  const id = store.write(input);
  const e = store.read(id);
  if (!e) throw new Error('read failed');
  return e;
}

/** Mock：topic 粗筛时挑"意图里包含的现有 topic"。 */
class TopicPickMock implements LLMClient {
  private _c = 0;
  get callCount(): number { return this._c; }
  async chat(messages: ChatMessage[]): Promise<string> {
    this._c++;
    const user = messages.find((m) => m.role === 'user')?.content ?? '';
    const intent = (user.split('【用户当前意图】')[1] ?? '').split('【')[0] ?? '';
    const list = (user.split('【库里已有的话题清单】')[1] ?? '')
      .split('\n').map((l) => l.replace(/^-\s*/, '').trim()).filter(Boolean);
    return JSON.stringify(list.filter((t) => intent.includes(t)));
  }
}

test('验收1：权重恒 >0 且为整数（派生排序分，不设上限）', () => {
  const store = new EventStore(':memory:');
  const cases = [
    ev({ source_type: 'user', temporal_orientation: 'long_term', event_form: 'correction', topic: 'A' }),
    ev({ source_type: 'observed', temporal_orientation: 'present', event_form: 'explicit', topic: 'B' }), // 最低档
    ev({ source_type: 'user', temporal_orientation: 'present', event_form: 'explicit', topic: 'C' }),
  ];
  for (const c of cases) {
    const e = writeAndRead(store, c);
    const w = computeWeight(e, store);
    assert.ok(Number.isInteger(w), '权重应为整数（无浮点）');
    assert.ok(w > 0, `权重应恒 >0，实际 ${w}`);
  }
  store.close();
});

test('验收2：主动性主导——user 权重明显高于 observed（同其他条件）', () => {
  const store = new EventStore(':memory:');
  const u = writeAndRead(store, ev({ source_type: 'user', topic: 'U' }));
  const o = writeAndRead(store, ev({ source_type: 'observed', topic: 'O' }));
  const wu = computeWeight(u, store);
  const wo = computeWeight(o, store);
  assert.ok(wu > wo, `user(${wu}) 应高于 observed(${wo})`);
  // "明显"：差距应可观（w1 最大）
  assert.ok(wu - wo >= 200, `差距应明显，实际 ${wu - wo}`);
  store.close();
});

test('验收3：放大系数生效——有重复/关联的权重高于无的同类', () => {
  const store = new EventStore(':memory:');
  // 同类（user/long/explicit），唯一 topic Y → amp=1000
  const solo = writeAndRead(store, ev({ topic: 'Y' }));
  const wSolo = computeWeight(solo, store);

  // 重复：topic X 写 3 条 → 重复度=2 → 放大
  writeAndRead(store, ev({ topic: 'X' }));
  writeAndRead(store, ev({ topic: 'X' }));
  const repeated = writeAndRead(store, ev({ topic: 'X' }));
  const wRepeated = computeWeight(repeated, store);
  assert.ok(wRepeated > wSolo, `重复(${wRepeated}) 应高于无重复(${wSolo})`);

  // 关联：related_event_ids 非空 → 放大
  const linked = writeAndRead(store, ev({ topic: 'Z', related_event_ids: ['a', 'b'] }));
  const wLinked = computeWeight(linked, store);
  const zSolo = computeWeight(writeAndRead(store, ev({ topic: 'Z2' })), store);
  assert.ok(wLinked > zSolo, `有关联(${wLinked}) 应高于无关联(${zSolo})`);
  store.close();
});

test('验收4：整数全程 + 可复现（同输入同输出）', () => {
  const store = new EventStore(':memory:');
  const e = writeAndRead(store, ev({ topic: 'R' }));
  const a = computeWeight(e, store);
  const b = computeWeight(e, store);
  assert.equal(a, b, '同一 Event 算两次应完全一致');
  assert.ok(Number.isInteger(a));
  store.close();
});

test('验收5：召回按权重降序（高权重排前）', async () => {
  const store = new EventStore(':memory:');
  // 同 topic 两条：一条 user（高权重）、一条 observed（低权重）
  writeAndRead(store, ev({ topic: '志向', source_type: 'observed', summary: '低权重观测' }));
  writeAndRead(store, ev({ topic: '志向', source_type: 'user', summary: '高权重主动' }));

  const r = await association('关于志向的事', store, new TopicPickMock());
  assert.equal(r.recalled.length, 2);
  assert.equal(r.recalled[0]!.summary, '高权重主动', '高权重的应排第一');
  assert.equal(r.recalled[1]!.summary, '低权重观测');
  store.close();
});

test('验收6：权重未进库（表里无 weight 字段，实时算）', () => {
  const tmp = `./.task05_pragma_${process.pid}.db`;
  const store = new EventStore(tmp);
  writeAndRead(store, ev());
  store.close();
  const insp = new DatabaseSync(tmp);
  const cols = (insp.prepare('PRAGMA table_info(event)').all() as unknown as Array<{ name: string }>).map((c) => c.name);
  insp.close();
  for (const p of [tmp, `${tmp}-wal`, `${tmp}-shm`, `${tmp}-journal`]) {
    try { rmSync(p); } catch { /* ignore */ }
  }
  assert.ok(!cols.includes('weight'), 'event 表不应有 weight 字段（D-003/D-009）');
});
