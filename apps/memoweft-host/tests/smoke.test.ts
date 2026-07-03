/**
 * @memoweft/host 冒烟测试（架构归位·批次5 步0）。
 *
 * 验的是【链路】而非功能：`import 'memoweft'` 能解析到 Core 的 dist，
 * createMemoWeftCore 建得起来、health/listCognitions/close 都在且形状对。
 * 用 :memory: 库，无运行时残留、不碰真实库、不依赖网络与模型。
 *
 * ⚠ 依赖 memoweft dist：跑本测试前须先在根仓 `npm run build`（Host 类型/运行都靠 Core 的 dist）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createMemoWeftCore } from 'memoweft';

test('冒烟：import memoweft → 建 core、health 结构对、listCognitions 是数组、close 不抛', () => {
  const core = createMemoWeftCore({ dbPath: ':memory:' });
  try {
    const h = core.health();
    assert.equal(typeof h.llmReady, 'boolean', 'health.llmReady 是布尔');
    assert.equal(typeof h.embedReady, 'boolean', 'health.embedReady 是布尔');

    const cognitions = core.memory.listCognitions();
    assert.ok(Array.isArray(cognitions), 'listCognitions 返回数组');
    assert.equal(cognitions.length, 0, '空库 → 空数组');
  } finally {
    core.close(); // 不抛 = 资源收口正常
  }
});
