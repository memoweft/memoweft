/**
 * config 注入：验证同一进程可运行两套互不干扰的配置，且省略 config 时不修改全局单例。
 * 纯离线，用假 LLM。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { config } from '../src/config.ts';
import { computeConfidence } from '../src/consolidation/confidence.ts';
import { openStores } from '../src/store/openStores.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import { perceive } from '../src/pipeline/perceive.ts';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import type { ChatMessage, LLMClient } from '../src/llm/client.ts';

test('config 注入 · 纯函数：同输入不同 cfg → 不同结果；不传=单例；单例不被改', () => {
  const inputs = {
    contentType: 'preference',
    formedBy: 'stated',
    supportCount: 1,
    contradictCount: 0,
  } as const;
  const singletonResult = computeConfidence(inputs); // 缺省=单例

  const cfgLow = structuredClone(config);
  cfgLow.consolidation.baseByFormedBy.stated = 100;
  const cfgHigh = structuredClone(config);
  cfgHigh.consolidation.baseByFormedBy.stated = 900;

  assert.ok(
    computeConfidence(inputs, cfgLow) < computeConfidence(inputs, cfgHigh),
    '不同注入配置 → 不同把握度',
  );
  assert.equal(computeConfidence(inputs), singletonResult, '不传 cfg 仍走单例、结果不变');
  assert.equal(
    config.consolidation.baseByFormedBy.stated,
    600,
    '全局单例未被注入调用改动（仍为出厂 600）',
  );
});

test('config 注入 · 端到端：同进程两套配置跑 consolidate，把握度各按各的算、互不干扰', async () => {
  const mk = (subjectId: string, base: number) => {
    const s = openStores(':memory:');
    const ev = s.evidenceStore.put({
      subjectId,
      sourceKind: 'spoken',
      hostId: 't',
      rawContent: '我喜欢喝茶',
    });
    s.eventStore.put({
      subjectId,
      summary: '聊了茶',
      occurredAt: ev.occurredAt,
      evidenceIds: [ev.id],
    });
    const llm: LLMClient = {
      callCount: 0,
      async chat(_msgs: ChatMessage[]): Promise<string> {
        return `{"new":[{"content":"喜欢喝茶","content_type":"preference","formed_by":"stated","support_evidence_ids":["${ev.id}"]}]}`;
      },
    };
    const cfg = structuredClone(config);
    cfg.consolidation.baseByFormedBy.stated = base;
    return { s, llm, cfg };
  };
  const A = mk('a', 900);
  const B = mk('b', 150);
  try {
    const ra = await consolidate('a', {
      eventStore: A.s.eventStore,
      evidenceStore: A.s.evidenceStore,
      cognitionStore: A.s.cognitionStore,
      llm: A.llm,
      transaction: A.s.transaction,
      config: A.cfg,
    });
    const rb = await consolidate('b', {
      eventStore: B.s.eventStore,
      evidenceStore: B.s.evidenceStore,
      cognitionStore: B.s.cognitionStore,
      llm: B.llm,
      transaction: B.s.transaction,
      config: B.cfg,
    });
    assert.equal(ra.created.length, 1);
    assert.equal(rb.created.length, 1);
    assert.ok(
      ra.created[0]!.confidence > rb.created[0]!.confidence,
      '注入的高 base → 更高把握度；两套配置同进程互不干扰',
    );
    assert.equal(config.consolidation.baseByFormedBy.stated, 600, '全局单例不受影响');
  } finally {
    A.s.close();
    B.s.close();
  }
});

test('config 注入 · perceive 用注入的 identity（缺省=单例的 owner/local）', () => {
  assert.equal(perceive('hi').subjectId, config.identity.subjectId, '缺省走单例 identity');
  const cfg = structuredClone(config);
  cfg.identity.subjectId = 'alice';
  cfg.identity.hostId = 'app-x';
  const injected = perceive('hi', {}, cfg);
  assert.equal(injected.subjectId, 'alice');
  assert.equal(injected.hostId, 'app-x');
  assert.equal(config.identity.subjectId, 'owner', '全局单例 identity 不被改');
});

test('config 注入 · evidence store 按注入配置补授权默认：同进程两套 privacyMode 互不干扰', () => {
  const openCfg = structuredClone(config);
  openCfg.privacyMode = false; // 默认可上云
  const privCfg = structuredClone(config);
  privCfg.privacyMode = true; // 隐私模式默认不上云
  const openStore = new SqliteEvidenceStore(':memory:', openCfg);
  const privStore = new SqliteEvidenceStore(':memory:', privCfg);
  try {
    const e1 = openStore.put({
      subjectId: 'u',
      sourceKind: 'spoken',
      hostId: 't',
      rawContent: 'x',
    });
    const e2 = privStore.put({
      subjectId: 'u',
      sourceKind: 'spoken',
      hostId: 't',
      rawContent: 'x',
    });
    assert.equal(e1.allowCloudRead, true, '非隐私配置 → 默认可上云');
    assert.equal(e2.allowCloudRead, false, '隐私配置 → 默认不上云（同进程两套配置互不干扰）');
  } finally {
    openStore.close();
    privStore.close();
  }
});
