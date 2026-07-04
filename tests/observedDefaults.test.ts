/**
 * 隐私红线 B 下沉 core 的离线护栏（T1，地图 cell 8 隐私规则）：
 * observed 证据缺省授权由 put 按 sourceKind 兜底（local✓ / cloud✗ / infer✓），
 * 任何入口落 observed 且未显式给授权位，一律不上云；显式传值仍优先；spoken/inferred 行为不变。
 * 全离线，不打网络（正门 core 用 ':memory:' 库、缺配 LLM 不崩，只测存证据这类不碰模型的活）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { createMemoWeftCore } from '../src/core/createCore.ts';
import { perceive } from '../src/pipeline/perceive.ts';
import { config } from '../src/config.ts';

test('put observed 无显式授权 → 套 observedDefaults（local✓ / cloud✗ / infer✓）', () => {
  const ev = new SqliteEvidenceStore(':memory:');
  try {
    // 前提：默认 privacyMode=false（此时 spoken 会默认上云，observed 仍必须不上云）。
    assert.equal(config.privacyMode, false, '前提：privacyMode 默认 false');
    const e = ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: '窗口观察到打游戏' });
    assert.equal(e.allowCloudRead, false, 'observed 默认不上云（红线 B 下沉 put）');
    assert.equal(e.allowLocalRead, true, 'observed 默认本地可读');
    assert.equal(e.allowInference, true, 'observed 默认可推画像');
  } finally {
    ev.close();
  }
});

test('put observed 显式 allowCloudRead:true → 尊重显式 true（不被兜底覆盖）', () => {
  const ev = new SqliteEvidenceStore(':memory:');
  try {
    const e = ev.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: '观察', allowCloudRead: true });
    assert.equal(e.allowCloudRead, true, '显式 true 优先于 observed 兜底');
  } finally {
    ev.close();
  }
});

test('回归：spoken 无显式授权 → allowCloudRead 跟随 privacyMode（不受本次改动影响）', () => {
  // privacyMode=false（默认）：spoken 默认上云。
  const evOpen = new SqliteEvidenceStore(':memory:');
  try {
    const e = evOpen.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '亲口说的' });
    assert.equal(e.allowCloudRead, true, 'privacyMode=false → spoken 默认上云（行为不变）');
    assert.equal(e.allowLocalRead, true);
    assert.equal(e.allowInference, true);
  } finally {
    evOpen.close();
  }

  // privacyMode=true：spoken 也默认不上云（走通用默认 cloudReadDefault，跟随配置）。
  const privateCfg = { ...config, privacyMode: true };
  const evPrivate = new SqliteEvidenceStore(':memory:', privateCfg);
  try {
    const e = evPrivate.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '亲口说的' });
    assert.equal(e.allowCloudRead, false, 'privacyMode=true → spoken 默认不上云（通用默认跟随配置）');
  } finally {
    evPrivate.close();
  }
});

test('回归：inferred 无显式授权 → 走通用默认（本次红线只管 observed，inferred 不变）', () => {
  const ev = new SqliteEvidenceStore(':memory:');
  try {
    // privacyMode=false → inferred 与 spoken 一样走通用默认，默认上云。
    const e = ev.put({ subjectId: 'owner', sourceKind: 'inferred', hostId: 'h', rawContent: 'AI 推测的' });
    assert.equal(e.allowCloudRead, true, 'inferred 仍走通用默认（未被下沉波及）');
  } finally {
    ev.close();
  }
});

test('正门验证：core.ingestUserMessage({sourceKind:observed}) → 不上云（真实单入参签名）', async () => {
  const core = createMemoWeftCore({ dbPath: ':memory:' });
  try {
    const ev = await core.ingestUserMessage({ content: '窗口观察到…', sourceKind: 'observed' });
    assert.equal(ev.allowCloudRead, false, '经 core 正门落 observed 也被 put 兜住不上云');
    assert.equal(ev.sourceKind, 'observed');
    // 对照：同一正门落 spoken（缺省 sourceKind）默认上云，证明只对 observed 收紧。
    const evSpoken = await core.ingestUserMessage({ content: '亲口说的' });
    assert.equal(evSpoken.sourceKind, 'spoken', '缺省 sourceKind = spoken');
    assert.equal(evSpoken.allowCloudRead, true, 'spoken 行为不变（默认上云）');
  } finally {
    core.close();
  }
});

test('端到端堵漏：模拟 testbench 路径 store.put(perceive(raw,{observed})) → 不上云', () => {
  const ev = new SqliteEvidenceStore(':memory:');
  try {
    // testbench /api/observe 现行组合：perceive(sourceKind:observed) → store.put，之前默认上云是漏，现被兜住。
    const e = ev.put(perceive('凌晨3点还在打游戏', { sourceKind: 'observed' }));
    assert.equal(e.allowCloudRead, false, 'testbench 观察路径落库默认不上云（漏被堵）');
    assert.equal(e.sourceKind, 'observed');
  } finally {
    ev.close();
  }
});
