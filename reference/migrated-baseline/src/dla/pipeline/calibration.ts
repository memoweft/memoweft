/**
 * ⑥ Calibration 校准 —— 不确定/有冲突时决定是否先探测（Hypothesis Probe）。
 * 对应决策：D-015。
 * 阶段：第一阶段【占位：一律直接回应】；第三阶段实现。
 */

import type { Event } from '../event/model.ts';
import type { ConflictResult } from './conflict.ts';

/** 校准结果。 */
export interface CalibrationResult {
  /** 是否需要先发探测性提问而非直接回应（本阶段恒 false）。 */
  probe: boolean;
}

/**
 * 【占位】本阶段一律"直接回应"（不探测）。
 * 签名已为下阶段留好：届时按 conflictResult / 不确定度决定是否 probe，替换函数体即可。
 */
export function calibration(_event: Event, _conflictResult: ConflictResult): CalibrationResult {
  return { probe: false };
}
