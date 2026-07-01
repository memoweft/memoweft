/**
 * ⑤ Conflict 冲突检测 —— 新 Event 与召回的旧信息/画像是否矛盾。
 * 对应决策：D-015 / D-005。
 * 阶段：第一阶段【占位：一律无冲突】；第三阶段实现。
 */

import type { Event } from '../event/model.ts';

/** 冲突检测结果。 */
export interface ConflictResult {
  /** 新 Event 是否与召回信息矛盾（本阶段恒 false）。 */
  hasConflict: boolean;
}

/**
 * 【占位】本阶段一律判"无冲突"。
 * 签名已为下阶段留好：届时比对 event 与 recalled，替换函数体即可。
 */
export function conflict(_event: Event, _recalled: Event[]): ConflictResult {
  return { hasConflict: false };
}
