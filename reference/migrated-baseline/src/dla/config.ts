/**
 * 可调参数集中地 —— DecisionProvider 雏形。
 * 对应决策：D-008（整数千分制）/ D-007（权重系数 w1/w2/w3 + 放大函数）/ D-012（T≈8000、M≈5）/ D-019（召回阈值）/ D-015 裁定三（State 重算条件）/ D-024（窗口长度）。
 * 重要：所有数值为"运行前占位猜测"，须在真实 Event 跑出后按实际数据校准（D-006 纪律）。
 */

export const config = {
  /** D-024 短期对话窗口参数。 */
  workingMemory: {
    /**
     * 窗口最大容量（估算 token）。占位 3000（D-024 建议 2000~4000）。
     * ⚠️ 运行后按真实体感校准——窗口太短会频繁滑出、太长会臃肿费 token。
     */
    maxTokens: 3000,
  },

  /** D-019 召回参数（A1 阶段）。 */
  association: {
    /**
     * 召回候选上限（SQL IN 取回的最多条数）。占位 50。
     * ⚠️ 运行后校准——A1 阶段够用；候选过多是启用 A2(summary 精挑) 的信号。
     */
    maxCandidates: 50,
  },

  /**
   * D-007 权重参数。⚠️ 全为"起步占位"，运行后按真实数据校准。
   * 不变量（不可调坏）：w1 最大（来源主动性主导）；各档高低分明；全整数。
   * 最终权重 = 基础分(0~1000) × 放大系数(≥1000) / 1000，是【派生排序分，不设上限】（D-007/D-008 修订）。
   */
  weight: {
    // 基础分系数（加法），w1 最大 → 用户主动说的话权重天然高（D-007）
    w1: 5, // 来源主动性
    w2: 3, // 时效指向
    w3: 2, // 是否纠正
    // 各维度档位分值（千分制 0~1000）
    source: { user: 1000, observed: 300 },
    temporal: { long_term: 1000, present: 400 },
    correction: { yes: 1000, no: 0 },
    // 放大系数分项（加到 1000 基线之上；恒使放大系数 ≥1000）
    repetitionStep: 100, // 每多一条同 topic Event +100
    repetitionCap: 500, // 重复度放大封顶
    associationStep: 100, // 每个 related_event_id +100
    associationCap: 500, // 关联广度放大封顶
  },
};

/**
 * 字符级 token 估算（不引分词器依赖，守 D-021）。
 * 策略：**宁可高估，别低估**（高估只会让窗口偏保守地早滑出，安全）。
 * - 中文等 CJK 字符：1 字符 ≈ 1 token（保守）。
 * - 其他字符（英文/数字/符号）：约 0.5 token/字符（真实英文约 0.25~0.3，这里取 0.5 仍偏高估）。
 * - 每条消息再加固定开销（role 包裹等）。
 * ⚠️ 占位估算，运行后按真实分词校准。
 */
export function estimateTokens(text: string): number {
  let cjk = 0;
  let other = 0;
  for (const ch of text) {
    // CJK 统一表意文字 + 中日韩标点/假名 + 全角符号
    if (/[　-〿぀-ヿ一-鿿＀-￯]/.test(ch)) cjk++;
    else other++;
  }
  return Math.ceil(cjk + other * 0.5);
}

/** 每条对话消息的固定结构开销（role 包裹等），估算用。占位值，运行后校准。 */
export const PER_TURN_TOKEN_OVERHEAD = 4;
