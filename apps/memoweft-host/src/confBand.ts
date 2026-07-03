/**
 * 把握度用户档（架构归位·批次5 步3 · 审查 must-fix）。
 *
 * 基于【有效把握度】effectiveConfidence（读时按龄衰减后的值）定档，而非静态 credStatus——
 *   后者纯时间流逝不重算，会让"会变淡"的认知（如 goal 半衰期 14 天、trait 60 天）长期显示偏高档，
 *   与产品核心「记≠信 / 会变淡」自相矛盾。用有效值定档，才让管理页如实反映召回层的真实权重。
 * 冲突态（conflicted）优先——不被衰减档覆盖（"有冲突，需确认"比把握度档更该先让用户看到）。
 * 阈值由调用方传入（来自 Core 的 config.consolidation.credThresholds，不硬编码、不漂移）。
 *
 * 纯函数、无副作用，供 server.ts 与单测共用。
 */
export type CredBand = 'conflicted' | 'stable' | 'limited' | 'low' | 'candidate';

export function credBand(
  input: { credStatus: string; effectiveConfidence: number },
  thresholds: { stable: number; limited: number; low: number },
): CredBand {
  if (input.credStatus === 'conflicted') return 'conflicted';
  const eff = input.effectiveConfidence;
  if (eff >= thresholds.stable) return 'stable';
  if (eff >= thresholds.limited) return 'limited';
  if (eff >= thresholds.low) return 'low';
  return 'candidate';
}
