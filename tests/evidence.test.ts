/**
 * 证据存储层测试：离线护栏，不调模型。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { cloudReadDefault } from '../src/config.ts';

function fresh() {
  return new SqliteEvidenceStore(':memory:');
}

const base = {
  subjectId: 'owner',
  sourceKind: 'spoken',
  hostId: 'testbench',
  rawContent: '昨晚没睡好',
} as const;

test('put 写入→读出一致，默认值正确', () => {
  const s = fresh();
  try {
    const e = s.put({ ...base });
    assert.ok(e.id, '应有 id');
    assert.equal(e.summary, '昨晚没睡好', 'summary 默认=原文');
    assert.equal(e.occurredAt, e.recordedAt, 'occurredAt 默认=recordedAt');
    assert.equal(e.allowLocalRead, true);
    assert.equal(e.allowInference, true);
    assert.equal(e.allowCloudRead, cloudReadDefault(), 'cloud_read 跟随配置');
    assert.equal(e.correctsEvidenceId, null);

    const got = s.get(e.id);
    assert.deepEqual(got, e, '读出与写入一致');
  } finally {
    s.close();
  }
});

test('幂等：同 originId 只落一次', () => {
  const s = fresh();
  try {
    const a = s.put({ ...base, originId: 'msg-1' });
    const b = s.put({ ...base, originId: 'msg-1', rawContent: '重试时不同内容' });
    assert.equal(a.id, b.id, '同 originId 返回原条');
    assert.equal(s.all().length, 1, '只落一条');
    assert.equal(s.get(a.id)?.rawContent, '昨晚没睡好', '保留首次内容');
  } finally {
    s.close();
  }
});

test('byTimeRange 按 occurredAt 区间过滤', () => {
  const s = fresh();
  try {
    s.put({ ...base, occurredAt: '2026-06-20T10:00:00.000Z', rawContent: '前天' });
    s.put({ ...base, occurredAt: '2026-06-22T22:00:00.000Z', rawContent: '昨晚' });
    s.put({ ...base, occurredAt: '2026-06-23T09:00:00.000Z', rawContent: '今早' });
    const hit = s.byTimeRange('2026-06-22T00:00:00.000Z', '2026-06-22T23:59:59.999Z');
    assert.equal(hit.length, 1);
    assert.equal(hit[0]!.rawContent, '昨晚');
  } finally {
    s.close();
  }
});

test('形成方式：sourceKind 原样存取', () => {
  const s = fresh();
  try {
    const spoken = s.put({ ...base, sourceKind: 'spoken' });
    const observed = s.put({ ...base, sourceKind: 'observed', rawContent: '开了一晚上游戏' });
    assert.equal(s.get(spoken.id)?.sourceKind, 'spoken');
    assert.equal(s.get(observed.id)?.sourceKind, 'observed');
  } finally {
    s.close();
  }
});

test('all 按 recordedAt 升序', () => {
  const s = fresh();
  try {
    s.put({ ...base, rawContent: '一' });
    s.put({ ...base, rawContent: '二' });
    const all = s.all();
    assert.equal(all.length, 2);
    assert.equal(all[0]!.rawContent, '一');
    assert.equal(all[1]!.rawContent, '二');
  } finally {
    s.close();
  }
});

test('update 改授权位：allowCloudRead / allowInference 持久化，未提供的位不动', () => {
  const s = fresh();
  try {
    const e = s.put({ ...base, allowCloudRead: true, allowInference: true });

    // 关 cloud：改后 get 回来是 false；inference / 内容不受牵连
    const u1 = s.update(e.id, { allowCloudRead: false });
    assert.equal(u1?.allowCloudRead, false, 'cloud 已关');
    assert.equal(u1?.allowInference, true, '未提供的 inference 不动');
    assert.equal(u1?.rawContent, '昨晚没睡好', '内容不受牵连');
    assert.equal(s.get(e.id)?.allowCloudRead, false, '已持久化（get 回读仍是 false）');

    // 关 inference + 同时改 summary：两者都生效，之前关掉的 cloud 保持关
    const u2 = s.update(e.id, { allowInference: false, summary: '顺手改摘要' });
    assert.equal(u2?.allowInference, false, 'inference 已关');
    assert.equal(u2?.summary, '顺手改摘要', 'summary 同步改');
    assert.equal(u2?.allowCloudRead, false, '之前关的 cloud 保持');

    // 再开回 cloud：布尔来回切都落库
    s.update(e.id, { allowCloudRead: true });
    assert.equal(s.get(e.id)?.allowCloudRead, true, 'cloud 可再开回');
    assert.equal(s.get(e.id)?.allowInference, false, 'inference 仍是关');

    assert.equal(s.update('nope', { allowCloudRead: false }), null, '不存在返回 null');
  } finally {
    s.close();
  }
});

test('update 改 summary（原文不变） / remove 真删（用户主动）', () => {
  const s = fresh();
  try {
    const e = s.put({ ...base });
    const u = s.update(e.id, { summary: '改后的摘要' });
    assert.equal(u?.summary, '改后的摘要');
    assert.equal(u?.rawContent, '昨晚没睡好', '原文不变');
    assert.equal(s.get(e.id)?.summary, '改后的摘要', '已持久化');
    assert.equal(s.update('nope', { summary: 'x' }), null, '不存在返回 null');

    assert.equal(s.remove(e.id), true);
    assert.equal(s.get(e.id), null, '已删');
    assert.equal(s.remove(e.id), false, '再删返回 false');
  } finally {
    s.close();
  }
});
