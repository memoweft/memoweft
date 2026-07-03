/**
 * ② Attention 注意力 —— 判断要不要进 DLA。
 * 对应决策：D-004（记≠信）/ D-015。
 * 阶段：第一阶段【占位：一律判"进"】；第二阶段实现真实过滤。
 */

import type { RawInput } from './perception.ts';

/** 注意力判定结果。 */
export interface AttentionResult {
  /** 是否放行进入 DLA。 */
  admit: boolean;
}

/**
 * 【占位】本阶段一律判定"进"——让所有输入都落库，真实过滤逻辑下阶段再做。
 * 签名已为下阶段留好：届时在此读 rawInput 做真实判断，替换函数体即可。
 */
export function attention(_rawInput: RawInput): AttentionResult {
  return { admit: true };
}
