/**
 * ① Perception 感知 —— 收原话，组装原始输入。只记录不判断。
 * 对应决策：D-014 / D-015（输出 {raw_content, source_type}）。
 * 阶段：TASK-02 实现。
 */

import type { SourceType } from '../event/model.ts';

/** 感知层产出的原始输入——尚未解析、未判断，只是把"进来的东西"装好。 */
export interface RawInput {
  raw_content: string;
  source_type: SourceType;
}

/**
 * 收一句话，组装成 RawInput。
 * @param raw_content 用户原话 / 观测原始描述
 * @param source_type 来源；本阶段默认用户主动（'user'）
 */
export function perception(raw_content: string, source_type: SourceType = 'user'): RawInput {
  return { raw_content, source_type };
}
