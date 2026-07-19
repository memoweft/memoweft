/**
 * 把握度自算（confidence policy：由规则计算，不采信模型自报）。
 *
 * 关键纪律：**confidence 由 MemoWeft 按规则算，不采信 LLM 自报。**
 * 规则：起步分按形成方式（推测最低）+ 支持证据加分 - 反对证据扣分。参数在 config（运行后校准）。
 *
 * 分型时间策略：临时类（state）置信封顶、永不进"稳定/有限"——
 * 临时情绪重复 ≠ 稳定特质，不能越攒越高；时间衰减与有效期由独立的后台策略处理。
 */
import { config, type MemoWeftConfig } from '../config.ts';
import type { ContentType, FormedBy, CredStatus } from '../cognition/model.ts';

export interface ConfidenceInputs {
  contentType: ContentType;
  formedBy: FormedBy;
  supportCount: number;
  contradictCount: number;
}

function isTransient(contentType: ContentType, cfg: MemoWeftConfig): boolean {
  return cfg.consolidation.transientTypes.includes(contentType);
}

/** 计算 0~1000 的置信度（恒 >0）；临时类封顶。cfg 可注入，省略时使用全局单例。 */
export function computeConfidence(i: ConfidenceInputs, cfg: MemoWeftConfig = config): number {
  const c = cfg.consolidation;
  const base = c.baseByFormedBy[i.formedBy];
  const support = Math.min(Math.max(i.supportCount - 1, 0), c.supportCap) * c.supportStep;
  const penalty = i.contradictCount * c.contradictPenalty;
  let result = Math.max(c.minConfidence, Math.min(1000, Math.round(base + support - penalty)));
  // 临时类（如 state）封顶：重复不升成稳定。
  if (isTransient(i.contentType, cfg)) result = Math.min(result, c.transientCap);
  return result;
}

/** 由把握度 + 反对证据 + 内容类型定可信状态。cfg 可注入（缺省=全局单例）。
 *
 *  supportCount 是【中间态判据】：支撑条数多于反证 → `contested`（有争议但仍成立），
 *  否则 `conflicted`（对峙或反证占优，不消解、原样暴露）。此前只要有一条反证就一律
 *  conflicted，于是 6 支撑 1 反证与 1 支撑 1 反证状态完全相同——computeConfidence 早已
 *  算出两者差别，是这里把它抹平了，连带让 revisitConflicts 反复拿明明站得住的认知去打扰用户。
 *
 *  判据不走置信度阈值：`stated` 类支撑加分封顶 200（base 600 + 200 − penalty 120 = 680），
 *  6 支撑 1 反证也够不到 stable 的 750——用阈值做判据在结构上就走不通。
 *
 *  supportCount 省略 → 退回旧行为（保守判 conflicted）。不知道支撑数时【不能假设】
 *  支撑压倒反证；库内调用点一律显式传。 */
export function deriveCredStatus(
  confidence: number,
  contradictCount: number,
  contentType: ContentType,
  cfg: MemoWeftConfig = config,
  supportCount = 0,
): CredStatus {
  // 有反对证据 → 先暴露，不消解；力量对比决定暴露成哪一档。
  if (contradictCount > 0) return supportCount > contradictCount ? 'contested' : 'conflicted';
  // 临时类永不进"稳定/有限"，最多"低置信"。
  if (isTransient(contentType, cfg)) {
    return confidence >= cfg.consolidation.credThresholds.low ? 'low' : 'candidate';
  }
  const t = cfg.consolidation.credThresholds;
  if (confidence >= t.stable) return 'stable';
  if (confidence >= t.limited) return 'limited';
  if (confidence >= t.low) return 'low';
  return 'candidate';
}
