/**
 * 短期对话窗口（Working Memory）—— D-024。
 * 对应决策：D-024（在场≠召回；意图补全；回话顺带判断按需召回）/ D-014·D-015（修订主链路形态）/ D-025（窗口用内存）。
 * 阶段：TASK-03 实现。
 *
 * 职责：维护最近若干轮对话（在场，原样给模型，不落库不召回）；按【长度】滑动，
 * 挤出的旧轮交回调用方在"那一刻"判断是否沉淀。
 *
 * 注意：窗口负责"听懂现在"，不负责"找过去"。它不查库、不算相关性。
 */

import { config, estimateTokens, PER_TURN_TOKEN_OVERHEAD } from '../config.ts';

/** 一轮对话（用户一句或助手一句）。 */
export interface Turn {
  role: 'user' | 'assistant';
  content: string;
}

/** 单轮的估算 token（含每轮固定开销）。 */
function turnTokens(turn: Turn): number {
  return estimateTokens(turn.content) + PER_TURN_TOKEN_OVERHEAD;
}

/**
 * 短期对话窗口：纯内存、按 token 长度滑动。
 * 生命极短（最近几轮），丢失无影响（D-025）。
 */
export class WorkingMemory {
  private turns: Turn[] = [];
  private readonly maxTokens: number;

  /** @param maxTokens 窗口容量上限（估算 token）；默认取 config（占位 3000，运行后校准）。 */
  constructor(maxTokens: number = config.workingMemory.maxTokens) {
    this.maxTokens = maxTokens;
  }

  /**
   * 压入一轮；若总长超过上限，从最老一端逐条挤出，直到不超。
   * @returns 本次因超长被【挤出】的旧轮（按从老到新顺序；可能为空）。
   */
  push(turn: Turn): Turn[] {
    this.turns.push(turn);
    const evicted: Turn[] = [];
    while (this.turns.length > 1 && this.estimatedTokens() > this.maxTokens) {
      // 至少保留最新一轮：哪怕它自己就超长，也不弹空（否则当前句无法在场）。
      const oldest = this.turns.shift();
      if (oldest) evicted.push(oldest);
    }
    return evicted;
  }

  /** 取当前在场的所有轮（原样，供拼进上下文给模型）。 */
  getContext(): Turn[] {
    return [...this.turns];
  }

  /** 当前窗口估算 token 总和。 */
  estimatedTokens(): number {
    return this.turns.reduce((sum, t) => sum + turnTokens(t), 0);
  }

  /** 当前窗口轮数（调试/测试用）。 */
  get size(): number {
    return this.turns.length;
  }
}
