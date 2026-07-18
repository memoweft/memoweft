/**
 * 会话窗口：在场 ≠ 召回。简化版：保留最近 N 轮，纯内存。
 *
 * 它只负责"让回话带上最近几轮上下文"，不查库、不算相关性、不落库。
 * 召回（找窗口外的旧记忆）是另一回事， 起做。
 */
import { config } from '../config.ts';

export interface Turn {
  role: 'user' | 'assistant';
  content: string;
}

export class WorkingMemory {
  private turns: Turn[] = [];
  private readonly maxTurns: number;

  constructor(maxTurns: number = config.workingMemory.maxTurns) {
    this.maxTurns = maxTurns;
  }

  /** 压入一轮；超过上限时从最老一端丢弃。 */
  push(turn: Turn): void {
    this.turns.push(turn);
    while (this.turns.length > this.maxTurns) this.turns.shift();
  }

  /** 当前在场的轮（原样，供拼上下文）。 */
  context(): Turn[] {
    return [...this.turns];
  }

  get size(): number {
    return this.turns.length;
  }
}
