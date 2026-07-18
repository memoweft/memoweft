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

/** 由把握度 + 反对证据 + 内容类型定可信状态。cfg 可注入（缺省=全局单例）。 */
export function deriveCredStatus(
  confidence: number,
  contradictCount: number,
  contentType: ContentType,
  cfg: MemoWeftConfig = config,
): CredStatus {
  if (contradictCount > 0) return 'conflicted'; // 有反对证据 → 先暴露，不消解
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
