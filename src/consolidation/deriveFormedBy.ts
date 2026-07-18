/**
 * Derive the provenance carrier for a cognition from its supporting evidence.
 *
 * This function answers “whose statement carries the information?” and returns
 * `stated`, `confirmed`, or `observed`. It does not decide whether the cognition is
 * an inference; that separate dimension remains in the model output.
 *
 * When several evidence items support one cognition, the weakest carrier wins. This
 * prevents an unrelated direct statement from upgrading a cognition that otherwise
 * depends on an assistant-proposed affirmation. Missing resolution data falls back to
 * structural facts: spoken evidence without preceding assistant context is `stated`;
 * spoken evidence with such context is conservatively `confirmed`.
 *
 * The function classifies provenance only. It neither drops cognitions nor computes
 * confidence, and it is intentionally internal to consolidation.
 */
import type { SourceKind } from '../evidence/model.ts';
import type { ResponseAct, PropositionOrigin } from '../interaction/model.ts';

/** 载体维的三个取值——`FormedBy` 的子集（不含 `inferred`/`ruled`，见文件头）。 */
export type CarrierFormedBy = 'stated' | 'confirmed' | 'observed';

/** 逐条支持证据的派生输入：来源 + 有没有 AI 上一句 + 它的语义解析（可无）。 */
export interface CarrierInput {
  sourceKind: SourceKind;
  /** 该证据的 preceding_ai_context（经 `evidenceStore.precedingAiContextOf` 取）。空 = 没有 AI 上一句。 */
  precedingAiContext: string | null;
  /** 该证据的语义解析。模型没产 / 没接 store → null，走兜底（见文件头【兜底】）。 */
  resolution: {
    responseAct: ResponseAct | null;
    propositionOrigin: PropositionOrigin | null;
  } | null;
}

/**
 * 载体维强弱序，锚定 `config.baseByFormedBy` 的底分：confirmed(280) < observed(350) < stated(600)。
 * 取最弱 = rank 最小。**改 config 底分时须同步核对本序**（否则「取最弱」会名不副实）。
 */
const CARRIER_RANK: Record<CarrierFormedBy, number> = { confirmed: 0, observed: 1, stated: 2 };

/** 单条证据 → 载体维。规则逐条对应派生表，见文件头。 */
function deriveOne(e: CarrierInput): CarrierFormedBy {
  // 派生表前两行：observed / tool 不是用户在说话 → observed（**绝不 stated**）。这两行不需要 resolution。
  //   sourceKind='inferred'（AI 推测型证据，罕见）一并归此：它同样不是用户亲口说的。
  if (e.sourceKind !== 'spoken') return 'observed';

  const hasAiContext = (e.precedingAiContext ?? '').trim().length > 0;
  const r = e.resolution;

  // 兜底：没解析、或解析里 propositionOrigin 收敛成了 null（非法枚举）——见文件头【兜底】。
  if (!r || r.propositionOrigin === null) return hasAiContext ? 'confirmed' : 'stated';

  // 派生表第 3 行：用户自己说出来的内容 → stated。
  if (r.propositionOrigin === 'user_stated') return 'stated';

  // assistant_proposed：命题是 AI 提的、载体不是用户 → 至多 confirmed（表第 4/5/6 行：affirm / weak / select）。
  //   唯一例外是 negate：用户否认 AI 的猜测时，被断言的是
  //   那个【否定命题】，而那是用户自己的明确表达 → stated。
  if (r.responseAct === 'negate') return 'stated';

  // 其余 response_act（affirm / select / elaborate / ask / none / other / null）在 assistant_proposed 下
  //   一律 confirmed —— 保守：命题既然是 AI 提的，载体就不是用户，绝不可升到 stated。
  //   （表只明确议定了 affirm / weak / select / negate；elaborate / ask / none / other 属表未覆盖的组合，
  //     按「assistant_proposed ⇒ 载体不是用户」这条上位原则收敛，不另立规则。）
  return 'confirmed';
}

/**
 * 支持证据集 → 载体维形成方式（按最保守项取值）。
 *
 * @returns 空集时返回 `null` —— 调用方按「算不出」处理，**不要瞎猜一个默认值**。
 */
export function deriveFormedBy(evidences: readonly CarrierInput[]): CarrierFormedBy | null {
  let weakest: CarrierFormedBy | null = null;
  for (const e of evidences) {
    const c = deriveOne(e);
    if (weakest === null || CARRIER_RANK[c] < CARRIER_RANK[weakest]) weakest = c;
  }
  return weakest;
}
