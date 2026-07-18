/** Unit coverage for provenance-carrier derivation, weakest-evidence aggregation, and fallbacks. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { deriveFormedBy, type CarrierInput } from '../src/consolidation/deriveFormedBy.ts';
import type { ResponseAct, PropositionOrigin } from '../src/interaction/model.ts';

/** 造一条支持证据。缺省 spoken、无 AI 上文、无解析。 */
function ev(over: Partial<CarrierInput> = {}): CarrierInput {
  return { sourceKind: 'spoken', precedingAiContext: null, resolution: null, ...over };
}
/** 造一份解析（只含 deriveFormedBy 看的两维）。 */
const res = (
  propositionOrigin: PropositionOrigin | null,
  responseAct: ResponseAct | null = null,
) => ({ propositionOrigin, responseAct });

// ── 派生表逐行：单条证据 ────────────────────────────────────────────────

test('表第 3 行：spoken ∧ user_stated → stated', () => {
  assert.equal(deriveFormedBy([ev({ resolution: res('user_stated', 'elaborate') })]), 'stated');
});

test('表第 4 行：spoken ∧ assistant_proposed ∧ affirm（附和）→ confirmed，绝不 stated', () => {
  assert.equal(
    deriveFormedBy([
      ev({
        precedingAiContext: '你挺喜欢爬山的吧?',
        resolution: res('assistant_proposed', 'affirm'),
      }),
    ]),
    'confirmed',
  );
});

test('表第 6 行：spoken ∧ assistant_proposed ∧ select（二选一里选）→ confirmed（补充覆盖）', () => {
  assert.equal(
    deriveFormedBy([
      ev({
        precedingAiContext: 'window or aisle?',
        resolution: res('assistant_proposed', 'select'),
      }),
    ]),
    'confirmed',
  );
});

test('assistant_proposed ∧ negate → stated：否定命题是用户自己的明确表达', () => {
  assert.equal(
    deriveFormedBy([
      ev({
        precedingAiContext: "You're a vegetarian, right?",
        resolution: res('assistant_proposed', 'negate'),
      }),
    ]),
    'stated',
  );
});

test('表前两行：observed / tool 不是用户在说话 → observed（绝不 stated），且不需要 resolution', () => {
  assert.equal(deriveFormedBy([ev({ sourceKind: 'observed' })]), 'observed');
  assert.equal(deriveFormedBy([ev({ sourceKind: 'tool' })]), 'observed');
  // sourceKind='inferred'（AI 推测型证据，罕见）同样不是用户亲口 → 一并归 observed
  assert.equal(deriveFormedBy([ev({ sourceKind: 'inferred' })]), 'observed');
});

test('表未覆盖的组合：assistant_proposed ∧ elaborate/ask/other → 收敛到 confirmed（命题是 AI 提的⇒载体不是用户）', () => {
  for (const act of ['elaborate', 'ask', 'none', 'other', null] as (ResponseAct | null)[]) {
    assert.equal(
      deriveFormedBy([
        ev({ precedingAiContext: '你喜欢爬山吧?', resolution: res('assistant_proposed', act) }),
      ]),
      'confirmed',
      `assistant_proposed + ${act} 应收敛到 confirmed`,
    );
  }
});

// ── 兜底：spoken 但没解析（探针实测旧盘 17.5% 如此，不是理论情形）────────────

test('兜底·无 AI 上文：结构上不存在可附和的命题 → proposition_origin 必为 user_stated → stated', () => {
  // 旧 42 盘全是这种（探针实证：它们的 assistant_proposed 计数为 0）→ 兜底恒取 stated → 零回归。
  assert.equal(deriveFormedBy([ev({ precedingAiContext: null })]), 'stated');
  assert.equal(deriveFormedBy([ev({ precedingAiContext: '   ' })]), 'stated', '纯空白等同没有');
});

test('兜底·有 AI 上文但没解析 → 可能是附和 → 保守取 confirmed', () => {
  assert.equal(deriveFormedBy([ev({ precedingAiContext: '你喜欢爬山吧?' })]), 'confirmed');
});

test('兜底·解析里 propositionOrigin 收敛成 null（非法枚举）→ 同无解析处理', () => {
  assert.equal(
    deriveFormedBy([ev({ precedingAiContext: null, resolution: res(null, 'affirm') })]),
    'stated',
  );
  assert.equal(
    deriveFormedBy([ev({ precedingAiContext: '你喜欢爬山吧?', resolution: res(null, 'affirm') })]),
    'confirmed',
  );
});

// ── 取最弱聚合 ────────────────────────────────────────────

test('取最弱：强弱序 confirmed(280) < observed(350) < stated(600)，锚定 config.baseByFormedBy 底分', () => {
  const stated = ev({ resolution: res('user_stated', 'elaborate') });
  const confirmed = ev({
    precedingAiContext: '你喜欢爬山吧?',
    resolution: res('assistant_proposed', 'affirm'),
  });
  const observed = ev({ sourceKind: 'observed' });

  assert.equal(deriveFormedBy([stated, confirmed]), 'confirmed', '[stated, confirmed] → confirmed');
  assert.equal(deriveFormedBy([stated, observed]), 'observed', '[stated, observed] → observed');
  assert.equal(
    deriveFormedBy([observed, confirmed]),
    'confirmed',
    '[observed, confirmed] → confirmed',
  );
  assert.equal(
    deriveFormedBy([stated, observed, confirmed]),
    'confirmed',
    '三者齐 → 最弱的 confirmed',
  );
  assert.equal(deriveFormedBy([stated, stated]), 'stated', '同质不降级');
});

test('取最弱与顺序无关', () => {
  const stated = ev({ resolution: res('user_stated', 'elaborate') });
  const confirmed = ev({
    precedingAiContext: 'x?',
    resolution: res('assistant_proposed', 'affirm'),
  });
  assert.equal(deriveFormedBy([stated, confirmed]), deriveFormedBy([confirmed, stated]));
});

// ── 洗白防线（本文件的核心；取最弱规则否掉「取最强」）──────────

test('反洗白：附和 + 顺带引一条无关的主动陈述 → 仍是 confirmed，绝不 stated', () => {
  // 攻击场景（回归审查确认、不需要恶意模型）：pickSupport 只查 id 白名单、不查相关性，validEvidence 覆盖整批
  //   （生产 batchSize=12 轮对话），「只引真正相关的」只由提示词软判 → 一个「过度引用」的模型就能让
  //   「AI 诱导 + 用户附和」的认知顺带引一条同批的无关主动陈述。
  // 若取最强：得 stated → 600+40 = 640 ≥ limited(500)；引 5 条 → 800 ≥ stable(750)。取最弱把这条堵死。
  const affirmed = ev({
    precedingAiContext: '你是不是特别不爱打电话?',
    resolution: res('assistant_proposed', 'affirm'),
  });
  const unrelated = ev({ resolution: res('user_stated', 'elaborate') }); // 同批里一条无关的用户主动陈述
  assert.equal(deriveFormedBy([affirmed, unrelated]), 'confirmed');
});

test('反洗白：附和 + 五条无关主动陈述（取最强会到 800/stable）→ 仍是 confirmed', () => {
  const affirmed = ev({
    precedingAiContext: '你不太爱去人多的酒吧?',
    resolution: res('assistant_proposed', 'affirm'),
  });
  const many = Array.from({ length: 5 }, () => ev({ resolution: res('user_stated', 'elaborate') }));
  assert.equal(deriveFormedBy([affirmed, ...many]), 'confirmed');
});

test('反洗白：有 AI 上文但没解析（兜底 confirmed）+ 无关主动陈述 → 仍是 confirmed', () => {
  // 兜底也必须扛得住同款攻击——否则「模型漏产解析」就成了绕过防线的后门。
  const noRes = ev({ precedingAiContext: '你喜欢爬山吧?' });
  const unrelated = ev({ resolution: res('user_stated', 'elaborate') });
  assert.equal(deriveFormedBy([noRes, unrelated]), 'confirmed');
});

// ── 边界 ────────────────────────────────────────────────────────────────

test('空支持集 → null（调用方按「算不出」处理，不瞎猜默认值）', () => {
  assert.equal(deriveFormedBy([]), null);
});

test('不返回 inferred / ruled——载体维只有三个取值（推断距离仍由模型报）', () => {
  const all: CarrierInput[] = [
    ev(),
    ev({ sourceKind: 'observed' }),
    ev({ sourceKind: 'tool' }),
    ev({ precedingAiContext: 'x?', resolution: res('assistant_proposed', 'affirm') }),
    ev({ resolution: res('user_stated', 'elaborate') }),
  ];
  for (const e of all) {
    const r = deriveFormedBy([e]);
    assert.ok(r === 'stated' || r === 'confirmed' || r === 'observed', `载体维越界: ${r}`);
  }
});
