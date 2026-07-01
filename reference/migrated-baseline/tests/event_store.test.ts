/**
 * TASK-01 验收测试 —— Event 存储层。
 * 用 Node 内置 node:test（D-021 零依赖）。运行：`npm test`（= node --test）。
 *
 * 覆盖 TASK-01 验收四条 + D-009 防回潮 + D-006 自检。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, rmSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { EventStore } from '../src/dla/event/store.ts';
import type { EventInput } from '../src/dla/event/model.ts';

/** 一条完整的样例 Event 输入（覆盖全部字段，含列表与可空字段）。 */
function sample(): EventInput {
  return {
    raw_content: '我决定以后早睡，晚上11点前睡觉',
    event_form: 'explicit',
    is_directional_change: true,
    topic: '作息',
    tags: ['睡眠', '健康', '习惯'],
    summary: '用户决定调整作息，11点前入睡',
    sentiment: 'positive',
    source_type: 'user',
    temporal_orientation: 'long_term',
    related_event_ids: ['evt-old-1', 'evt-old-2'],
    correction_target_id: null,
  };
}

/** 删除测试临时库文件（含 sqlite 可能产生的旁文件）。 */
function rmTmp(path: string): void {
  for (const p of [path, `${path}-wal`, `${path}-shm`, `${path}-journal`]) {
    try {
      rmSync(p);
    } catch {
      /* 文件不存在则忽略 */
    }
  }
}

test('验收1：写入一条完整 Event，再读出来，字段完全一致', () => {
  const store = new EventStore(':memory:');
  const input = sample();
  const id = store.write(input);

  const got = store.read(id);
  assert.ok(got, '应能读到刚写入的 Event');

  // id / timestamp 由存储层生成
  assert.equal(got.id, id);
  assert.equal(typeof got.timestamp, 'number');
  assert.ok(got.timestamp > 0);

  // 其余字段逐一与输入相等（含列表深比较）
  assert.equal(got.raw_content, input.raw_content);
  assert.equal(got.event_form, input.event_form);
  assert.equal(got.is_directional_change, input.is_directional_change);
  assert.equal(got.topic, input.topic);
  assert.deepEqual(got.tags, input.tags);
  assert.equal(got.summary, input.summary);
  assert.equal(got.sentiment, input.sentiment);
  assert.equal(got.source_type, input.source_type);
  assert.equal(got.temporal_orientation, input.temporal_orientation);
  assert.deepEqual(got.related_event_ids, input.related_event_ids);
  assert.equal(got.correction_target_id, input.correction_target_id);

  store.close();
});

test('验收2：防回潮 —— 表里不存在 5 个禁止字段，且 D-009 字段齐全', () => {
  const tmpPath = `./.task01_pragma_${process.pid}.db`;
  const store = new EventStore(tmpPath);
  store.write(sample()); // 确保表已建
  store.close();

  const inspect = new DatabaseSync(tmpPath);
  const cols = inspect
    .prepare(`PRAGMA table_info(event)`)
    .all() as unknown as Array<{ name: string }>;
  inspect.close();
  rmTmp(tmpPath);

  const colNames = cols.map((c) => c.name);

  const forbidden = ['event_type', 'pattern', 'weight', 'repetition_count', 'is_correction'];
  for (const f of forbidden) {
    assert.ok(
      !colNames.includes(f),
      `禁止字段 "${f}" 不应出现（D-009 防回潮）。实际列：${colNames.join(', ')}`,
    );
  }

  const expected = [
    'id', 'timestamp', 'raw_content', 'event_form', 'is_directional_change',
    'topic', 'tags', 'summary', 'sentiment', 'source_type',
    'temporal_orientation', 'related_event_ids', 'correction_target_id',
  ];
  for (const e of expected) {
    assert.ok(colNames.includes(e), `字段 "${e}" 应存在`);
  }
  // 不多不少：列数应严格等于字段表
  assert.equal(colNames.length, expected.length, `列数应为 ${expected.length}，实际 ${colNames.length}`);
});

test('验收3：D-006 自检 —— 存储层不存在任何物理删除接口', () => {
  const store = new EventStore(':memory:') as unknown as Record<string, unknown>;
  for (const banned of ['delete', 'remove', 'drop', 'clear', 'truncate', 'purge']) {
    assert.equal(
      typeof store[banned],
      'undefined',
      `EventStore 不应暴露 "${banned}" 这类删除接口（D-006 只生不灭）`,
    );
  }

  // 源码层面：store.ts 不应包含物理删除 SQL
  const src = readFileSync(new URL('../src/dla/event/store.ts', import.meta.url), 'utf8');
  const upper = src.toUpperCase();
  assert.ok(!upper.includes('DROP TABLE'), 'store.ts 不应包含 DROP TABLE');
  assert.ok(!/\bDELETE\s+FROM\b/.test(upper), 'store.ts 不应包含 DELETE FROM');
});

test('验收4：可复现性 —— 同一 id 连读两次结果完全一致（D-008 基础）', () => {
  const store = new EventStore(':memory:');
  const id = store.write(sample());
  const first = store.read(id);
  const second = store.read(id);
  assert.deepEqual(first, second, '连续两次读取应完全一致');
  store.close();
});
