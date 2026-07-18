/**
 * tool-result-ingest 隐私规则的离线护栏：
 * tool（工具执行结果）证据缺省授权由 put 按 sourceKind 兜底（local✓ / cloud✗ / infer✓）——
 * 修「tool 掉进 else 分支 → 默认上云」的隐私陷阱：工具返回值常含敏感外部数据（网页/文件/API 响应）。
 * 任何入口落 tool 且未显式给授权位，一律不上云；显式传值仍优先；spoken/inferred/observed 行为不变。
 * 全离线，不打网络（正门 core 用 ':memory:' 库、缺配 LLM 不崩，只测存证据这类不碰模型的活）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { createMemoWeftCore } from '../src/core/createCore.ts';
import { perceive } from '../src/pipeline/perceive.ts';
import { config } from '../src/config.ts';

test('put tool 无显式授权 → 套 toolDefaults（local✓ / cloud✗ / infer✓）', () => {
  const ev = new SqliteEvidenceStore(':memory:');
  try {
    // 前提：默认 privacyMode=false（此时 spoken 会默认上云，tool 仍必须不上云——不修就是默认上云的雷）。
    assert.equal(config.privacyMode, false, '前提：privacyMode 默认 false');
    const e = ev.put({
      subjectId: 'owner',
      sourceKind: 'tool',
      hostId: 'h',
      rawContent: '{"city":"Xiamen","temp":31}',
    });
    assert.equal(e.allowCloudRead, false, 'tool 默认不上云（tool-result-ingest 隐私雷已拆）');
    assert.equal(e.allowLocalRead, true, 'tool 默认本地可读');
    assert.equal(e.allowInference, true, 'tool 默认可推画像');
  } finally {
    ev.close();
  }
});

test('put tool 显式 allowCloudRead:true → 尊重显式 true（不被兜底覆盖）', () => {
  const ev = new SqliteEvidenceStore(':memory:');
  try {
    const e = ev.put({
      subjectId: 'owner',
      sourceKind: 'tool',
      hostId: 'h',
      rawContent: '工具结果',
      allowCloudRead: true,
    });
    assert.equal(e.allowCloudRead, true, '显式 true 优先于 tool 兜底');
  } finally {
    ev.close();
  }
});

test('回归：observed 兜底与 spoken 通用默认不受 tool 分支影响', () => {
  const ev = new SqliteEvidenceStore(':memory:');
  try {
    const observed = ev.put({
      subjectId: 'owner',
      sourceKind: 'observed',
      hostId: 'h',
      rawContent: '窗口观察',
    });
    assert.equal(observed.allowCloudRead, false, 'observed 仍走 observedDefaults 不上云');
    const spoken = ev.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 'h',
      rawContent: '亲口说的',
    });
    assert.equal(spoken.allowCloudRead, true, 'privacyMode=false → spoken 仍默认上云（行为不变）');
  } finally {
    ev.close();
  }
});

test('正门：core.ingestToolResult → 恰好一条 tool 证据、原文保真、默认不上云', async () => {
  const core = createMemoWeftCore({ dbPath: ':memory:' });
  try {
    const payload = '{"result":"sunny","tempC":31}';
    const ev = await core.ingestToolResult({ content: payload });
    assert.equal(ev.sourceKind, 'tool', '落库来源标 tool');
    assert.equal(ev.rawContent, payload, '存的是工具返回结果原文');
    assert.equal(ev.allowCloudRead, false, '经正门落 tool 也被 put 兜住不上云');
    assert.equal(ev.allowLocalRead, true);
    assert.equal(ev.allowInference, true);
    assert.equal(core.memory.listEvidence({}).length, 1, '恰好一条');
  } finally {
    core.close();
  }
});

test('正门幂等：同 originId 重复 ingestToolResult 只落一条（返回原条）', async () => {
  const core = createMemoWeftCore({ dbPath: ':memory:' });
  try {
    const a = await core.ingestToolResult({ content: 'r1', originId: 'call-1' });
    const b = await core.ingestToolResult({ content: 'r1（重试）', originId: 'call-1' });
    assert.equal(b.id, a.id, '幂等命中返回原条');
    assert.equal(core.memory.listEvidence({}).length, 1, '仍只有一条');
  } finally {
    core.close();
  }
});

test('端到端堵漏：store.put(perceive(raw,{tool})) 绕过正门也不上云（最后防线在 put）', () => {
  const ev = new SqliteEvidenceStore(':memory:');
  try {
    const e = ev.put(perceive('curl 拉回的 API 响应', { sourceKind: 'tool' }));
    assert.equal(e.allowCloudRead, false, '任何入口落 tool 都被 put 兜住不上云');
    assert.equal(e.sourceKind, 'tool');
  } finally {
    ev.close();
  }
});
