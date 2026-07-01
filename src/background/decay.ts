/**
 * 分型衰减（地图 cell 8 规则 8 · 阶段 4-B）：不同类型的认知，过期速度不一样。
 *
 * 情绪此刻就该淡、进行中的项目看活跃度、明确说过的偏好哪怕久不提也不自动失效——
 * **不能一刀切"越久越不信"**。这里把"按真实时间衰减"落成纯函数。
 *
 * 纪律（用户拍板）：衰减【读时算、不持久化】——confidence 字段保持"证据强度"语义不动，
 *   展示/回话时再乘衰减因子得"有效置信"。不破坏静态算法、不动 updatedAt 衰减锚、不动表。
 */
import { config } from '../config.ts';
import type { Cognition, ContentType } from '../cognition/model.ts';

const DAY_MS = 86_400_000;

/** 衰减因子 0~1：半衰期 ≤0（或没配）= 不衰减返回 1；否则按 2^(-age/半衰期)。 */
export function decayFactor(halfLifeDays: number, ageMs: number): number {
  if (!(halfLifeDays > 0)) return 1;
  const ageDays = Math.max(0, ageMs) / DAY_MS;
  return Math.pow(2, -ageDays / halfLifeDays);
}

/** 半衰期（天）按类型取；没配 = 0 = 不衰减。 */
export function halfLifeOf(contentType: ContentType): number {
  return config.background.halfLifeDays[contentType] ?? 0;
}

/**
 * 有效置信 = confidence × 衰减因子（按距上次印证 updatedAt 的时间）。
 * 读时算，恒整数。新鲜的≈原值；久没被印证的、且属易忘类型的，明显降。
 */
export function effectiveConfidence(
  cog: Pick<Cognition, 'confidence' | 'contentType' | 'updatedAt'>,
  now: Date = new Date(),
): number {
  const ageMs = now.getTime() - new Date(cog.updatedAt).getTime();
  return Math.round(cog.confidence * decayFactor(halfLifeOf(cog.contentType), ageMs));
}
