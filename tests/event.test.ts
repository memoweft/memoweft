/**
 * 事件层 + distill 测试：离线护栏；distill 用 stub LLM。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEventStore } from '../src/event/store.ts';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { distill } from '../src/distillation/distill.ts';

test('event store：put / evidenceOf / coveredEvidenceIds / remove', () => {
  const evt = new SqliteEventStore(':memory:');
  try {
    const e = evt.put({
      subjectId: 'owner',
      summary: 's',
      occurredAt: '2026-06-23T01:00:00.000Z',
      evidenceIds: ['x', 'y'],
    });
    assert.deepEqual(evt.evidenceOf(e.id).sort(), ['x', 'y']);
    assert.deepEqual(evt.coveredEvidenceIds('owner').sort(), ['x', 'y']);
    assert.equal(evt.all('owner').length, 1);
    assert.equal(evt.remove(e.id), true);
    assert.deepEqual(evt.evidenceOf(e.id), [], '删事件连带删覆盖关系');
  } finally {
    evt.close();
  }
});

test('distill：未整理证据 → 一个事件（覆盖它们、锚到最早）；再 distill 无 pending 不调模型', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  try {
    const a = ev.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 'h',
      rawContent: '今天带了雨伞',
      occurredAt: '2026-06-23T01:00:00.000Z',
    });
    const b = ev.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 'h',
      rawContent: '最近有点烦',
      occurredAt: '2026-06-23T01:05:00.000Z',
    });
    const stub = {
      callCount: 0,
      async chat() {
        this.callCount++;
        return '用户今天带了雨伞，最近表达烦躁。';
      },
    };

    const r = await distill('owner', { evidenceStore: ev, eventStore: evt, llm: stub });
    assert.ok(r.event);
    assert.equal(r.pendingCount, 2);
    assert.equal(r.event.summary, '用户今天带了雨伞，最近表达烦躁。');
    assert.equal(r.event.occurredAt, a.occurredAt, '锚到最早发生时间');
    assert.deepEqual(evt.evidenceOf(r.event.id).sort(), [a.id, b.id].sort());

    const r2 = await distill('owner', { evidenceStore: ev, eventStore: evt, llm: stub });
    assert.equal(r2.event, null, '没有未整理证据 → 不生成事件');
    assert.equal(r2.pendingCount, 0);
    assert.equal(r2.llmCalls, 0, '无 pending 不调模型');
  } finally {
    ev.close();
    evt.close();
  }
});
