/**
 * 阶段 4-A 档1 离线护栏：通用观察摄入口 + 活动窗口映射。
 * 验收：observed 默认授权（本地可读 / 默认不上云 / 可推画像）、originId 幂等、显式授权被尊重。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { ingestObservations, type Observation } from '../src/perception/ingest.ts';
import { activeWindowToObservation } from '../src/perception/collectors/activeWindow.ts';

function obs(over: Partial<Observation> = {}): Observation {
  return {
    kind: 'active_window',
    occurredAt: '2026-06-23T03:30:00.000Z',
    content: '在 某游戏 停留约 50 分钟',
    ...over,
  };
}

test('ingest：批量落 observed + observed 默认授权（本地可读 / 默认不上云 / 可推画像）', () => {
  const ev = new SqliteEvidenceStore(':memory:');
  try {
    const r = ingestObservations('owner', [
      obs(),
      obs({ occurredAt: '2026-06-23T04:00:00.000Z', content: '在 浏览器 停留约 10 分钟' }),
    ], { evidenceStore: ev });
    assert.equal(r.stored.length, 2, '两条都落库');
    assert.equal(r.skipped, 0);
    for (const e of r.stored) {
      assert.equal(e.sourceKind, 'observed', '来源标 observed');
      assert.equal(e.allowLocalRead, true, '本地可读');
      assert.equal(e.allowCloudRead, false, '默认不上云（隐私默认）');
      assert.equal(e.allowInference, true, '可推画像');
    }
  } finally {
    ev.close();
  }
});

test('ingest：originId 幂等——重复摄入不重复落库、计入 skipped', () => {
  const ev = new SqliteEvidenceStore(':memory:');
  try {
    const o = obs({ originId: 'win-1' });
    const r1 = ingestObservations('owner', [o], { evidenceStore: ev });
    assert.equal(r1.stored.length, 1);
    assert.equal(r1.skipped, 0);
    const r2 = ingestObservations('owner', [o, obs({ originId: 'win-1' })], { evidenceStore: ev });
    assert.equal(r2.stored.length, 0, '同 originId 不重复落');
    assert.equal(r2.skipped, 2, '两条都被幂等跳过');
    assert.equal(ev.all().length, 1, '证据层只 1 条');
  } finally {
    ev.close();
  }
});

test('ingest：显式授权位被尊重（测试台"允许上云"勾选 = 显式 cloud=true）', () => {
  const ev = new SqliteEvidenceStore(':memory:');
  try {
    const r = ingestObservations('owner', [obs({ allowCloudRead: true })], { evidenceStore: ev });
    assert.equal(r.stored[0]!.allowCloudRead, true, '显式上云被尊重（走路线 A 验证用）');
    assert.equal(r.stored[0]!.allowInference, true, '其余仍走 observed 默认');
  } finally {
    ev.close();
  }
});

test('activeWindow：样本 → 标准化 Observation（content 串 + 幂等键 + meta + kind）', () => {
  const o = activeWindowToObservation({ app: '某游戏', title: '战斗', durationSec: 3000, occurredAt: '2026-06-23T03:30:00.000Z' });
  assert.equal(o.kind, 'active_window');
  assert.equal(o.occurredAt, '2026-06-23T03:30:00.000Z');
  assert.ok(o.content.includes('某游戏') && o.content.includes('50 分钟'), 'content 标准化（约 50 分钟）');
  assert.ok(o.originId && o.originId.includes('某游戏'), '带幂等键');
  assert.equal(o.meta!.durationSec, 3000, 'meta 保留原始结构');
});

test('端到端：手动授权上云的 observed 证据落库 + 可被归因时间窗捞到（路线 A 的库级前提）', () => {
  const ev = new SqliteEvidenceStore(':memory:');
  try {
    const o = activeWindowToObservation({ app: '某游戏', title: '', durationSec: 3600, occurredAt: '2026-06-23T03:00:00.000Z' });
    const r = ingestObservations('owner', [{ ...o, allowCloudRead: true }], { evidenceStore: ev });
    assert.equal(r.stored.length, 1);
    const hit = ev.byTimeRange('2026-06-23T02:00:00.000Z', '2026-06-23T09:00:00.000Z');
    assert.ok(
      hit.some((e) => e.id === r.stored[0]!.id && e.allowCloudRead && e.allowInference),
      '观察证据可被归因时间窗 + allowInference/cloud 过滤捞到',
    );
  } finally {
    ev.close();
  }
});
