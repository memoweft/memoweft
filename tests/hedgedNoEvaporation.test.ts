/**
 * hedged 不蒸发（回归护栏）：含糊自述封顶必须在【每一个】把握度计算点重新派生。
 *
 * 背景：`assertion_strength='weak'` 的主动自述（「我可能不太会做饭」）连命题边界都没定，
 * 却与「我是素食者」同拿 stated 底分 600；而点头认 AI 猜测的含糊只有 confirmed 280——保守性倒置。
 * 方案 confidence-penalty：hedged 与载体维正交，policy 统一在 computeConfidence 里以
 * `min(hedgeCap=280)` 兑现，`deriveFormedBy` 完全不动。
 *
 * **本文件钉的是本次改动的最大风险**：hedged **不落库**，每次都从支持证据链重新派生。
 * 因此 8 个 computeConfidence 调用点漏接任何一个，用户增删证据之后封顶就会静默蒸发。
 * 每个 test 对应一个调用点，断言值一律选「漏接时会得到的那个数」的反面（640 / 480 / 600），
 * 而不是只断言「小于某阈值」——后者会在漏接时假绿。
 *
 * 口径（config 默认值，改动时同步核对）：
 *   stated 底分 600、supportStep 40（第 2 条起每条 +40）、contradictPenalty 120、hedgeCap 280。
 *
 * ⚠ consolidate 的 `semanticResolutionStore` 是【可选】依赖，本文件全部显式注入——
 *   不注入就测不到重算期的回查路径（仓内 14 个调 consolidate 的测试里有 12 个没注入它，
 *   若靠它们兜底，这条回归会静默通过）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openStores, type StoreBundle } from '../src/store/openStores.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import { createMemoryManagementAPI } from '../src/memory/managementApi.ts';

/** 含糊自述封顶值（config.consolidation.hedgeCap，与 confirmed 底分对齐）。 */
const HEDGE_CAP = 280;
/** 漏接时会出现的三个「反弹值」，写成常量是为了让失败信息一眼可读。 */
const STATED_1 = 600; // stated 底分，1 条支持
const STATED_2 = 640; // stated + 1 条额外支持（supportStep 40）
const STATED_1_MINUS_1 = 480; // stated 底分 − 1 条反证（contradictPenalty 120）

const stubOf = (body: string) => ({
  callCount: 0,
  async chat() {
    this.callCount++;
    return body;
  },
});

function cDeps(b: StoreBundle, stub: { callCount: number; chat(): Promise<string> }) {
  return {
    eventStore: b.eventStore,
    evidenceStore: b.evidenceStore,
    cognitionStore: b.cognitionStore,
    semanticResolutionStore: b.semanticResolutionStore, // 必须显式注入，否则测不到回查路径
    llm: stub,
    transaction: b.transaction,
  };
}

/** 落一条用户亲口说的证据；`strength` 非 null 时同时落它的语义解析（模拟【历史】轮次已解析过）。 */
function putSpoken(b: StoreBundle, raw: string, strength: 'weak' | 'explicit' | null): string {
  const e = b.evidenceStore.put({
    subjectId: 'owner',
    sourceKind: 'spoken',
    hostId: 'test',
    rawContent: raw,
  });
  if (strength !== null) {
    b.semanticResolutionStore.put({
      evidenceId: e.id,
      resolvedContent: `解析：${raw}`,
      propositionOrigin: 'user_stated',
      assertionStrength: strength,
      resolverVersion: 'test',
    });
  }
  return e.id;
}

/** 播一条【已封顶】的含糊自述认知：stated 载体 + 若干条支持证据（强度逐条指定）。 */
function seedHedgedCognition(b: StoreBundle, strengths: readonly ('weak' | 'explicit')[]) {
  const ids = strengths.map((s, i) => putSpoken(b, `我可能不太会做饭 #${i}`, s));
  const cog = b.cognitionStore.put({
    subjectId: 'owner',
    content: '用户不太会做饭',
    contentType: 'preference',
    formedBy: 'stated',
    confidence: HEDGE_CAP,
    credStatus: 'candidate',
    evidence: ids.map((id) => ({ evidenceId: id, relation: 'support' as const })),
  });
  return { cog, ids };
}

/** 造一个待消化事件（其原话在 prompt 里按出现顺序编号 e1、e2…）。 */
function putEvent(b: StoreBundle, evidenceIds: string[]) {
  b.eventStore.put({
    subjectId: 'owner',
    summary: '用户说了一句',
    occurredAt: '2026-07-01T08:00:00.000Z',
    evidenceIds,
  });
}

/** 一条把 e1 解析成【含糊的主动自述】的 resolution（本轮解析：只在内存里，落库晚于派生）。 */
const WEAK_RESOLUTION_E1 = {
  evidence_id: 'e1',
  resolved_content: '用户含糊表示自己厨艺不佳',
  proposition_origin: 'user_stated',
  assertion_strength: 'weak',
};

// ══════════ consolidate：形成期三点（:486 new / :555 并存 / :589 correct）══════════

test('consolidate new（:486）：含糊自述新建认知 → 封顶 280，不再与明确断言同拿 600', async () => {
  const b = openStores(':memory:');
  try {
    putEvent(b, [putSpoken(b, '我可能不太会做饭', null)]);
    const r = await consolidate(
      'owner',
      cDeps(
        b,
        stubOf(
          JSON.stringify({
            new: [
              {
                content: '用户不太会做饭',
                content_type: 'preference',
                formed_by: 'stated',
                support_evidence_ids: ['e1'],
              },
            ],
            resolutions: [WEAK_RESOLUTION_E1],
          }),
        ),
      ),
    );
    assert.equal(r.created.length, 1, '应新建一条认知');
    assert.equal(r.created[0]!.formedBy, 'stated', '载体维不变：仍是用户亲口说的');
    assert.equal(
      r.created[0]!.confidence,
      HEDGE_CAP,
      `含糊自述应封顶到 ${HEDGE_CAP}（漏接则得 ${STATED_1}）`,
    );
  } finally {
    b.close();
  }
});

test('consolidate 并存新 stated（:555）：附和后含糊复述 → 并存认知封顶 280，不拿满 600', async () => {
  const b = openStores(':memory:');
  try {
    // 旧认知是【附和来的】（confirmed 280）：用户当初只是点头认了 AI 的猜测。
    const old = putSpoken(b, '嗯，是的', null);
    const cog = b.cognitionStore.put({
      subjectId: 'owner',
      content: '用户不太会做饭',
      contentType: 'preference',
      formedBy: 'confirmed',
      confidence: 280,
      credStatus: 'candidate',
      evidence: [{ evidenceId: old, relation: 'support' as const }],
    });
    putEvent(b, [putSpoken(b, '我可能不太会做饭吧', null)]);
    const r = await consolidate(
      'owner',
      cDeps(
        b,
        stubOf(
          JSON.stringify({
            reinforce: [{ cognition_id: cog.id, support_evidence_ids: ['e1'] }],
            resolutions: [WEAK_RESOLUTION_E1],
          }),
        ),
      ),
    );
    assert.equal(r.created.length, 1, '主动含糊复述应并存出一条新的 stated 认知');
    assert.equal(r.created[0]!.formedBy, 'stated');
    assert.equal(
      r.created[0]!.confidence,
      HEDGE_CAP,
      `并存路是保守性倒置最刺眼的一条：漏接则含糊复述拿满 ${STATED_1}，反超它附和的那条`,
    );
  } finally {
    b.close();
  }
});

test('consolidate correct（:589）：用含糊话纠正旧认知 → 新认知封顶 280，纠正不抬高把握度', async () => {
  const b = openStores(':memory:');
  try {
    const { cog } = seedHedgedCognition(b, ['weak']);
    putEvent(b, [putSpoken(b, '其实我可能挺会做饭的？', null)]);
    const r = await consolidate(
      'owner',
      cDeps(
        b,
        stubOf(
          JSON.stringify({
            correct: [
              {
                cognition_id: cog.id,
                content: '用户其实会做饭',
                content_type: 'preference',
                formed_by: 'stated',
                support_evidence_ids: ['e1'],
              },
            ],
            resolutions: [WEAK_RESOLUTION_E1],
          }),
        ),
      ),
    );
    assert.equal(r.corrected, 1);
    assert.equal(
      r.created[0]!.confidence,
      HEDGE_CAP,
      `漏接则新认知 ${STATED_1} > 被标失效的旧认知 ${HEDGE_CAP}——「纠正」反而让系统更笃定`,
    );
  } finally {
    b.close();
  }
});

// ══════════ consolidate：重算期两点（:526 reinforce / :630 conflict）══════════

test('consolidate reinforce（:526）：hedged 不因新一轮强化而蒸发（钉住混合读的【内存】那半边）', async () => {
  const b = openStores(':memory:');
  try {
    // 旧支持证据【没有解析】（历史遗留），本轮新证据的解析只在内存 resolutionOf 里、
    //   要到四个写循环之后才落库 ⇒ 若实现成「纯查表回查」，这里一定判不出 hedged。
    const { cog } = seedHedgedCognition(b, []);
    const oldEv = putSpoken(b, '（历史证据，无解析）', null);
    b.cognitionStore.addEvidence(cog.id, [{ evidenceId: oldEv, relation: 'support' as const }]);
    putEvent(b, [putSpoken(b, '我大概还是不太会做饭', null)]);
    const r = await consolidate(
      'owner',
      cDeps(
        b,
        stubOf(
          JSON.stringify({
            reinforce: [{ cognition_id: cog.id, support_evidence_ids: ['e1'] }],
            resolutions: [WEAK_RESOLUTION_E1],
          }),
        ),
      ),
    );
    assert.equal(r.reinforced, 1);
    assert.equal(
      b.cognitionStore.get(cog.id)!.confidence,
      HEDGE_CAP,
      `强化后仍应封顶（漏接则 ${STATED_2}；纯查表实现会读不到本轮解析而同样失效）`,
    );
  } finally {
    b.close();
  }
});

test('consolidate conflict（:630）：被反驳后仍封顶 280，不会反而暴涨（钉住混合读的【查表】那半边）', async () => {
  const b = openStores(':memory:');
  try {
    // support 集合全是历史证据（本轮新证据挂 contradict）⇒ hedged 判定整个落到表回查上。
    const { cog } = seedHedgedCognition(b, ['weak']);
    putEvent(b, [putSpoken(b, '昨天我做了顿大餐', null)]);
    const r = await consolidate(
      'owner',
      cDeps(
        b,
        stubOf(
          JSON.stringify({ conflict: [{ cognition_id: cog.id, support_evidence_ids: ['e1'] }] }),
        ),
      ),
    );
    assert.equal(r.conflicted, 1);
    assert.equal(
      b.cognitionStore.get(cog.id)!.confidence,
      HEDGE_CAP,
      `漏接则 ${STATED_1_MINUS_1}——一条含糊认知被反驳一次，把握度反而从 ${HEDGE_CAP} 暴涨`,
    );
  } finally {
    b.close();
  }
});

// ══════════ managementApi：force 删除 + 其余重算点（:333 / :425 / :547）══════════

test('removeEvidenceSafely（:333）：删掉一条证据后，派生 cognition 整体移除', () => {
  const b = openStores(':memory:');
  try {
    const api = createMemoryManagementAPI(b);
    const { cog, ids } = seedHedgedCognition(b, ['weak', 'weak']);
    const res = api.removeEvidenceSafely({
      evidenceId: ids[0]!,
      reason: '用户要求删除',
      force: true,
    });
    assert.equal(res.removed, true);
    assert.equal(b.cognitionStore.get(cog.id), null, '认知内容不能脱离已删证据继续存在');
  } finally {
    b.close();
  }
});

test('reinforceCognition（:425）：用户点一下「对」补证据 → 仍封顶 280', () => {
  const b = openStores(':memory:');
  try {
    const api = createMemoryManagementAPI(b);
    const { cog } = seedHedgedCognition(b, ['weak']);
    const extra = putSpoken(b, '嗯我确实不太会', 'weak');
    const r = api.reinforceCognition({
      cognitionId: cog.id,
      evidenceId: extra,
      reason: '用户在确认式 UI 上点了「对」',
    });
    assert.equal(r.reinforced, true);
    assert.equal(
      r.cognition!.confidence,
      HEDGE_CAP,
      `漏接则 ${STATED_2}——一个肯定动作抹掉了「当初说得含糊」这个事实`,
    );
  } finally {
    b.close();
  }
});

test('mergeCognition（:547）：两条含糊认知合并 → 仍封顶 280', () => {
  const b = openStores(':memory:');
  try {
    const api = createMemoryManagementAPI(b);
    const target = seedHedgedCognition(b, ['weak']).cog;
    const source = seedHedgedCognition(b, ['weak']).cog;
    api.mergeCognition({ sourceId: source.id, targetId: target.id, reason: '同一命题' });
    assert.equal(
      b.cognitionStore.get(target.id)!.confidence,
      HEDGE_CAP,
      `合并只是把证据搬到一起（漏接则 ${STATED_2}）`,
    );
  } finally {
    b.close();
  }
});

test('mergeCognition 反向：source 带来一条 explicit → 封顶正确解除（不是无脑封顶）', () => {
  const b = openStores(':memory:');
  try {
    const api = createMemoryManagementAPI(b);
    const target = seedHedgedCognition(b, ['weak']).cog;
    const source = seedHedgedCognition(b, ['explicit']).cog;
    api.mergeCognition({ sourceId: source.id, targetId: target.id, reason: '同一命题' });
    assert.equal(
      b.cognitionStore.get(target.id)!.confidence,
      STATED_2,
      '合并后 support 集里有 explicit ⇒「没有明确断言」不再成立 ⇒ 封顶应解除（判据永远重新从证据算）',
    );
  } finally {
    b.close();
  }
});

test('hypothesis + hedged：两个 min 可组合，取三者最小（hypothesisCap 250 更严则它赢）', () => {
  const b = openStores(':memory:');
  try {
    const api = createMemoryManagementAPI(b);
    const e1 = putSpoken(b, '我可能不太会做饭', 'weak');
    const cog = b.cognitionStore.put({
      subjectId: 'owner',
      content: '用户不太会做饭（推测）',
      contentType: 'hypothesis',
      formedBy: 'stated',
      confidence: 250,
      credStatus: 'candidate',
      evidence: [{ evidenceId: e1, relation: 'support' as const }],
    });
    const extra = putSpoken(b, '嗯大概吧', 'weak');
    const r = api.reinforceCognition({ cognitionId: cog.id, evidenceId: extra, reason: '补证据' });
    assert.equal(
      r.cognition!.confidence,
      250,
      'hedgeCap(280) 与 hypothesisCap(250) 同为 min 封顶，可交换、结果取最小；假设类不因补证据被抬成结论',
    );
  } finally {
    b.close();
  }
});
