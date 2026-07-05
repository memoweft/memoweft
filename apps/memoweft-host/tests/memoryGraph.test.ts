/**
 * 记忆图谱端点数据冒烟测试（步5-G2）。
 *
 * 验的是【链路 + payload 形状】而非渲染：
 *   core.graph.buildMemoryGraph（Host 的 /api/memory-graph 就直接转发它）产出的 payload
 *   有 nodes/edges/stats 三键、类型对、stats 各计数是数字；带 include* 选项不抛。
 * 用 :memory: 库，无运行时残留、不碰真实库、不依赖网络与模型。
 *
 * ⚠ 依赖 memoweft dist：跑本测试前须先在根仓 `npm run build`（Host 靠 Core 的 dist）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createMemoWeftCore } from 'memoweft';

test('记忆图谱：buildMemoryGraph 产 { nodes, edges, stats }，形状对、空库不抛', () => {
  const core = createMemoWeftCore({ dbPath: ':memory:' });
  try {
    const g = core.graph.buildMemoryGraph({});
    assert.ok(Array.isArray(g.nodes), 'nodes 是数组');
    assert.ok(Array.isArray(g.edges), 'edges 是数组');
    assert.ok(g.stats && typeof g.stats === 'object', 'stats 是对象');

    // stats 各计数字段都在且是数字（前端 grStats 一行直接读这些）
    const s = g.stats;
    assert.equal(typeof s.nodeCount, 'number', 'stats.nodeCount 是数字');
    assert.equal(typeof s.edgeCount, 'number', 'stats.edgeCount 是数字');
    assert.equal(typeof s.hiddenCount, 'number', 'stats.hiddenCount 是数字');
    assert.equal(typeof s.activeCognitionCount, 'number', 'stats.activeCognitionCount 是数字');
    assert.equal(typeof s.conflictedCount, 'number', 'stats.conflictedCount 是数字');
    assert.equal(typeof s.hypothesisCount, 'number', 'stats.hypothesisCount 是数字');
    assert.equal(typeof s.observedEvidenceCount, 'number', 'stats.observedEvidenceCount 是数字');
    // 计数与数组长度自洽
    assert.equal(s.nodeCount, g.nodes.length, 'nodeCount 与 nodes 长度一致');
    assert.equal(s.edgeCount, g.edges.length, 'edgeCount 与 edges 长度一致');
  } finally {
    core.close();
  }
});

test('记忆图谱：includeInvalid / includeArchived 选项（Host query 会传的组合）不抛、形状仍对', () => {
  const core = createMemoWeftCore({ dbPath: ':memory:' });
  try {
    // Host /api/memory-graph 收到 includeInvalid=true&includeArchived=true 时的等价调用
    const g = core.graph.buildMemoryGraph({
      includeEvidence: true, includeInvalid: true, includeArchived: true,
    });
    assert.ok(Array.isArray(g.nodes), 'nodes 是数组');
    assert.ok(Array.isArray(g.edges), 'edges 是数组');
    assert.ok(g.stats && typeof g.stats === 'object', 'stats 是对象');
  } finally {
    core.close();
  }
});
