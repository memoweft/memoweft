/**
 * 后台画像调度器 scheduler（批次5 步6 · S0/S1 用户正门）：验用户"立即整理"与 S1 新理解信号。
 * 注入假 updateProfile，不依赖网络/模型/库——只验调度器自身的透传/单飞/清计数逻辑。
 *
 * 覆盖三件步6 关键行为：
 *   ① refreshNow 把 consolidated.created 转成 lastUpdate.newCognitions（S1 气泡信号：只 id/content/credStatus）；
 *   ② refreshNow 与后台走【同一把单飞锁】——在跑时再 refreshNow 返回 ran:false（不并发跑两趟 updateProfile）；
 *   ③ refreshNow 成功后清 pendingSinceUpdate（这批已整理，不留残值触发重复整理）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createProfileScheduler } from '../src/scheduler.ts';

/** 造一个假 updateProfile：返回给定的 created 列表（模拟 consolidate 出的新认知）。 */
function fakeUpdate(created: Array<{ id: string; content: string; credStatus: string }>) {
  return async () => ({
    consolidated: { created, reinforced: 1, corrected: 0, conflicted: 0 },
  });
}

test('refreshNow：把 created 转成 lastUpdate.newCognitions（S1 气泡信号，只 id/content/credStatus）', async () => {
  const created = [
    { id: 'cog-1', content: '喜欢喝手冲咖啡', credStatus: 'stable' },
    { id: 'cog-2', content: '最近在学吉他', credStatus: 'candidate' },
  ];
  const scheduler = createProfileScheduler({ updateProfile: fakeUpdate(created) });
  const r = await scheduler.refreshNow();

  assert.equal(r.ran, true, 'refreshNow 成功跑了一趟');
  assert.ok(r.summary, 'ran=true 应带 summary');
  assert.equal(r.summary!.created, 2, 'created 计数 = 2');
  assert.deepEqual(
    r.summary!.newCognitions,
    created,
    'newCognitions 原样透出 id/content/credStatus（供前端织气泡）',
  );
  // status().lastUpdate 与 refreshNow 返回一致（bg-status 轮询也能拿到同一份信号）。
  assert.deepEqual(scheduler.status().lastUpdate, r.summary, 'status().lastUpdate = 本轮 summary');
  scheduler.dispose();
});

test('refreshNow 单飞：后台正在跑时再 refreshNow → ran:false（不并发两趟 updateProfile）', async () => {
  let calls = 0;
  let release!: () => void;
  const gate = new Promise<void>((res) => { release = res; });
  // 假 updateProfile：第一趟卡在闸门上不返回，模拟"后台正在整理"。
  const scheduler = createProfileScheduler({
    updateProfile: async () => {
      calls++;
      await gate; // 卡住，保持 profileUpdating=true
      return { consolidated: { created: [], reinforced: 0, corrected: 0, conflicted: 0 } };
    },
  });

  const first = scheduler.refreshNow();          // 抢到锁、卡在闸门
  await Promise.resolve();                         // 让第一趟先进到 await gate
  const second = await scheduler.refreshNow();    // 锁被占 → 立即 ran:false
  assert.equal(second.ran, false, '正忙时第二次 refreshNow 不跑');
  assert.equal(second.summary, null, 'ran=false summary 为 null');

  release();                                        // 放行第一趟
  const firstResult = await first;
  assert.equal(firstResult.ran, true, '第一趟正常收尾');
  assert.equal(calls, 1, 'updateProfile 只被调了一次（没并发跑两趟）');
  scheduler.dispose();
});

test('refreshNow 成功后清 pendingSinceUpdate（这批已整理，不留残值）', async () => {
  const scheduler = createProfileScheduler({ updateProfile: fakeUpdate([]) });
  scheduler.onTurn(); // 攒 1 条（未达 batchSize=5，不触发后台）
  scheduler.onTurn(); // 攒 2 条
  assert.equal(scheduler.status().pendingSinceUpdate, 2, '攒了 2 条待整理');

  await scheduler.refreshNow(); // 用户主动整理这批
  assert.equal(scheduler.status().pendingSinceUpdate, 0, '整理后计数清零');
  scheduler.dispose();
});
