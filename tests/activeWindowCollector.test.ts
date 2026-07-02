/**
 * 阶段 8-A 离线护栏：活动窗口采集循环（纯逻辑，不碰真 Win32、不起真定时器）。
 * 验收：连续相同合并、阈值过滤碎片、切换产出、pause 期间不采、stop 冲刷最后一段、
 * 采不到保守截断、产出不带显式授权位（下游走 observed 保守默认=不上云）、onEmit 抛错不崩。
 * 手法：sampler / 时钟 / 定时器全注入，直接 tick() 驱动。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  createActiveWindowCollector,
  type ActiveWindowEmit,
  type ForegroundWindow,
} from '../src/perception/collectors/activeWindowCollector.ts';

/** 全注入测试台架：假时钟 + 假采样队列 + no-op 定时器，feed(win, atMs) 手动驱动一拍。 */
function makeHarness(over: { minDurationSec?: number; onEmit?: (e: ActiveWindowEmit) => void } = {}) {
  let nowMs = 0;
  const queue: (ForegroundWindow | null)[] = [];
  const emitted: ActiveWindowEmit[] = [];
  const sampledAt: number[] = []; // 每次 sampler 真被调用的时刻（验证 pause 期间不采）
  const errors: unknown[] = [];
  const collector = createActiveWindowCollector({
    sampler: async () => {
      sampledAt.push(nowMs);
      return queue.shift() ?? null;
    },
    onEmit: over.onEmit ?? ((e) => { emitted.push(e); }),
    sampleIntervalSec: 5,
    minDurationSec: over.minDurationSec ?? 30,
    now: () => nowMs,
    onError: (err) => { errors.push(err); },
    setIntervalFn: () => 0,   // 假定时器：不真跑，测试手动 tick
    clearIntervalFn: () => {},
  });
  const setNow = (ms: number) => { nowMs = ms; };
  /** 推进假时钟到 atMs 并喂一个采样结果、驱动一拍。 */
  const feed = async (win: ForegroundWindow | null, atMs: number) => {
    setNow(atMs);
    queue.push(win);
    await collector.tick();
  };
  return { collector, emitted, sampledAt, errors, feed, setNow };
}

const A: ForegroundWindow = { app: 'Code', title: 'DLA_rebuild' };
const B: ForegroundWindow = { app: 'chrome', title: 'GitHub' };

test('采集循环：连续相同 app+title 合并累计，切换时旧段产出（时长按真实时钟差）', async () => {
  const h = makeHarness();
  h.collector.start();
  await h.feed(A, 0);
  await h.feed(A, 5_000);
  await h.feed(A, 35_000);   // 同窗口只合并，不产出
  assert.equal(h.emitted.length, 0, '没切换就不产出');
  await h.feed(B, 40_000);   // 切换：A 段截到发现切换这一刻（40s ≥ 30s 阈值）
  assert.equal(h.emitted.length, 1, '切换产出一段');
  const { sample, observation } = h.emitted[0]!;
  assert.equal(sample.app, 'Code');
  assert.equal(sample.title, 'DLA_rebuild');
  assert.equal(sample.durationSec, 40, '合并时长 = 段首到发现切换（0→40s）');
  assert.equal(sample.occurredAt, new Date(0).toISOString(), 'occurredAt = 段开始时刻');
  // 产出走 activeWindowToObservation：kind / 幂等键 / meta 都在
  assert.equal(observation.kind, 'active_window');
  assert.ok(observation.originId!.includes('Code'), '带幂等键');
  assert.equal(observation.meta!.durationSec, 40);
  await h.collector.stop();
});

test('采集循环：不足阈值的碎片丢弃；恰好等于阈值的产出（≥）', async () => {
  const h = makeHarness();
  h.collector.start();
  await h.feed(A, 0);
  await h.feed(B, 10_000);   // A 只停留 10s < 30s → 碎片丢弃
  assert.equal(h.emitted.length, 0, '碎片不产出');
  h.setNow(40_000);
  await h.collector.stop();  // B 段 10s→40s = 恰好 30s → 产出（阈值是 ≥）
  assert.equal(h.emitted.length, 1);
  assert.equal(h.emitted[0]!.sample.app, 'chrome');
  assert.equal(h.emitted[0]!.sample.durationSec, 30);
});

test('采集循环：stop 冲刷最后一段（截到 stop 时刻）；stop 后 tick 不再采', async () => {
  const h = makeHarness();
  h.collector.start();
  await h.feed(A, 0);
  await h.feed(A, 35_000);
  h.setNow(42_000);
  await h.collector.stop();
  assert.equal(h.emitted.length, 1, 'stop 冲刷最后一段');
  assert.equal(h.emitted[0]!.sample.durationSec, 42, '截到 stop 时刻');
  assert.equal(h.collector.state, 'stopped');
  const sampledBefore = h.sampledAt.length;
  await h.collector.tick(); // stop 后再 tick：不采样、不崩
  assert.equal(h.sampledAt.length, sampledBefore, 'stop 后不再采样');
});

test('采集循环：pause 冲刷当前段且期间一次都不采；resume 后新起一段', async () => {
  const h = makeHarness();
  h.collector.start();
  await h.feed(A, 0);
  await h.feed(A, 31_000);
  h.setNow(33_000);
  await h.collector.pause();  // 冲刷：A 段 33s ≥ 30s → 产出
  assert.equal(h.collector.state, 'paused');
  assert.equal(h.emitted.length, 1, 'pause 冲刷当前段');
  assert.equal(h.emitted[0]!.sample.durationSec, 33);

  const sampledBefore = h.sampledAt.length;
  h.setNow(60_000);
  await h.collector.tick();   // pause 期间 tick 被无视
  assert.equal(h.sampledAt.length, sampledBefore, 'pause 期间不采样');

  h.collector.resume();
  await h.feed(B, 100_000);   // resume 后新起一段（不接续 pause 前）
  await h.feed(B, 140_000);
  h.setNow(140_000);
  await h.collector.stop();
  assert.equal(h.emitted.length, 2);
  assert.equal(h.emitted[1]!.sample.app, 'chrome');
  assert.equal(h.emitted[1]!.sample.durationSec, 40, 'resume 后按新段起点计时');
});

test('采集循环：采不到（null）→ 当前段保守截到最后一次确认看见的时刻', async () => {
  const h = makeHarness();
  h.collector.start();
  await h.feed(A, 0);
  await h.feed(A, 35_000);
  await h.feed(null, 60_000); // 锁屏/出错：60s 这刻不算，段截到 35s
  assert.equal(h.emitted.length, 1);
  assert.equal(h.emitted[0]!.sample.durationSec, 35, '不知道的时间不算停留');
  await h.collector.stop();   // 没有未完段可冲刷
  assert.equal(h.emitted.length, 1);
});

test('采集循环：产出不带显式授权位——下游 ingest 才套 observed 保守默认（不上云红线）', async () => {
  const h = makeHarness();
  h.collector.start();
  await h.feed(A, 0);
  h.setNow(40_000);
  await h.collector.stop();
  const obs = h.emitted[0]!.observation;
  assert.equal(obs.allowCloudRead, undefined, '不显式授权上云（由 config.observedDefaults 决定=false）');
  assert.equal(obs.allowLocalRead, undefined);
  assert.equal(obs.allowInference, undefined);
});

test('采集循环：onEmit 抛错（如测试台没起）→ 上报 onError，循环不崩、继续采', async () => {
  const h = makeHarness({
    onEmit: () => { throw new Error('测试台没起'); },
  });
  h.collector.start();
  await h.feed(A, 0);
  await h.feed(B, 40_000);    // A 段产出 → onEmit 抛错 → 不崩
  assert.equal(h.errors.length, 1, '错误进 onError');
  await h.feed(B, 80_000);    // 循环还活着，B 段继续累计
  h.setNow(80_000);
  await h.collector.stop();   // B 段冲刷 → 又一次 onEmit 抛错
  assert.equal(h.errors.length, 2, '循环没崩，后续段照常走到产出');
});
