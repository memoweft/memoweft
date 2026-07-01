/**
 * 阶段 4-B 离线护栏：分型衰减（读时算有效置信）+ 自然过期（临时类失效、稳定类不失效）。
 * 全用注入的时间戳，不依赖真实时钟。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { decayFactor, effectiveConfidence } from '../src/background/decay.ts';
import { expire } from '../src/background/expire.ts';

const DAY = 86_400_000;

test('衰减因子：半衰期处减半、0/不配=不衰减、越久越低', () => {
  assert.equal(decayFactor(0, 10 * DAY), 1, '半衰期 0 → 不衰减');
  assert.equal(decayFactor(2, 0), 1, '没过时间 → 1');
  assert.ok(Math.abs(decayFactor(2, 2 * DAY) - 0.5) < 1e-9, '过 1 个半衰期 → 0.5');
  assert.ok(decayFactor(2, 4 * DAY) < decayFactor(2, 2 * DAY), '越久越低');
});

test('有效置信：情绪久不提明显降，明确偏好几乎不衰', () => {
  const now = new Date('2026-07-01T00:00:00.000Z');
  const old = '2026-06-24T00:00:00.000Z'; // 7 天前
  const stateOld = effectiveConfidence({ confidence: 300, contentType: 'state', updatedAt: old }, now);
  const prefOld = effectiveConfidence({ confidence: 700, contentType: 'preference', updatedAt: old }, now);
  assert.ok(stateOld < 50, `情绪 7 天没提 → 有效置信大幅降（实际 ${stateOld}）`);
  assert.equal(prefOld, 700, '明确偏好不衰减（preference 没配半衰期）');
  // 新鲜的情绪 ≈ 原值
  const stateFresh = effectiveConfidence({ confidence: 300, contentType: 'state', updatedAt: now.toISOString() }, now);
  assert.equal(stateFresh, 300, '刚印证的情绪不打折');
});

test('自然过期：临时类超阈值标 invalidAt，稳定类永不失效', () => {
  const s = new SqliteCognitionStore(':memory:');
  try {
    const mood = s.put({ subjectId: 'u', content: '用户今天很烦', contentType: 'state', formedBy: 'stated', confidence: 250, credStatus: 'low' });
    const pref = s.put({ subjectId: 'u', content: '用户喜欢喝茶', contentType: 'preference', formedBy: 'stated', confidence: 700, credStatus: 'stable' });
    // 站在 30 天后回看（认知此刻刚建、updatedAt≈现在，未来 now 让它们"久未印证"）
    const future = new Date(Date.now() + 30 * DAY);
    const r = expire('u', { cognitionStore: s }, future);
    assert.equal(r.expired, 1, '只过期 1 条（情绪）');
    assert.ok(s.get(mood.id)?.invalidAt, '情绪自然过期标失效');
    assert.equal(s.get(pref.id)?.invalidAt, null, '明确偏好永不自动失效');
    assert.equal(s.active('u').length, 1, '只剩偏好活跃');
  } finally {
    s.close();
  }
});
