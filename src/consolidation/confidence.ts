/**
 * 把握度自算（地图 cell 8 规则 8/9 / cell 11 难点 1 / cell 12 "把握度怎么算"）。
 *
 * 关键纪律：**confidence 由 MemoWeft 按规则算，不采信 LLM 自报。**
 * v1 规则：起步分按形成方式（推测最低）+ 支持证据加分 - 反对证据扣分。参数在 config（运行后校准）。
 *
 * 分型时间策略 v1（cell 8 规则 8）：临时类（state）置信封顶、永不进"稳定/有限"——
 * 临时情绪重复 ≠ 稳定特质，不能越攒越高。完整版（按真实时间衰减/有效期）留后续。
 */
import { config } from '../config.ts';
import type { ContentType, FormedBy, CredStatus } from '../cognition/model.ts';

export interface ConfidenceInputs {
  contentType: ContentType;
  formedBy: FormedBy;
  supportCount: number;
  contradictCount: number;
}

function isTransient(contentType: ContentType): boolean {
  return config.consolidation.transientTypes.includes(contentType);
}

/** 算把握度 0~1000（恒 >0）；临时类封顶。 */
export function computeConfidence(i: ConfidenceInputs): number {
  const c = config.consolidation;
  const base = c.baseByFormedBy[i.formedBy];
  const support = Math.min(Math.max(i.supportCount - 1, 0), c.supportCap) * c.supportStep;
  const penalty = i.contradictCount * c.contradictPenalty;
  let result = Math.max(c.minConfidence, Math.min(1000, Math.round(base + support - penalty)));
  // 临时类（如 state）封顶：重复不升成稳定。
  if (isTransient(i.contentType)) result = Math.min(result, c.transientCap);
  return result;
}

/** 由把握度 + 反对证据 + 内容类型定可信状态。 */
export function deriveCredStatus(
  confidence: number,
  contradictCount: number,
  contentType: ContentType,
): CredStatus {
  if (contradictCount > 0) return 'conflicted'; // 有反对证据 → 先暴露，不消解
  // 临时类永不进"稳定/有限"，最多"低置信"。
  if (isTransient(contentType)) {
    return confidence >= config.consolidation.credThresholds.low ? 'low' : 'candidate';
  }
  const t = config.consolidation.credThresholds;
  if (confidence >= t.stable) return 'stable';
  if (confidence >= t.limited) return 'limited';
  if (confidence >= t.low) return 'low';
  return 'candidate';
}
