/**
 * 交互会话（v0.6）：per-conversation 的上下文窗口 + episode 切分。
 *
 * 把「记住上一句 AI + 切分对话段」从回话逻辑里独立出来——让【不走回话】的宿主（如 weftmate，
 * 自建 agent 循环、只用裸 ingestUserMessage）也能捕获交互上下文。core 为每个 conversationId 维护一个实例：
 *   - ingestUserMessage(conversationId) 前调 beginUserTurn → 拿到「上一轮 AI 那句」+ 本轮 episodeId。
 *   - recordAssistantReply(conversationId) 把宿主生成的 AI 回复 push 进窗口（**只作后续上下文、永不落证据**）。
 *
 * 纪律：只管内存窗口 + episode 归属，不碰库、不产 Cognition、不算置信度。上一轮 AI 那句经此进入
 *   EvidenceInput.precedingAiContext 专用列，下游 distill/consolidate 可直接使用该上下文。
 */
import { randomUUID } from 'node:crypto';
import { WorkingMemory, type Turn } from './workingMemory.ts';

export interface InteractionSessionOptions {
  /** 上下文窗口保留轮数；缺省用 WorkingMemory 默认（config.workingMemory.maxTurns）。 */
  maxTurns?: number;
  /** episode idle 切分阈值（ms）：相邻两轮 user 间隔超过它 → 起一个新 episode。缺省 30 分钟。 */
  idleMs?: number;
  /** 初始 episodeId；缺省生成一个。 */
  episodeId?: string;
}

/** 默认 idle 切分阈值：30 分钟没说话，视作新的一段交互。 */
export const DEFAULT_EPISODE_IDLE_MS = 30 * 60 * 1000;

export class InteractionSession {
  private readonly window: WorkingMemory;
  private readonly idleMs: number;
  private episodeId: string;
  private lastTurnAtMs: number | null = null;

  constructor(opts: InteractionSessionOptions = {}) {
    this.window = new WorkingMemory(opts.maxTurns);
    this.idleMs = opts.idleMs ?? DEFAULT_EPISODE_IDLE_MS;
    this.episodeId = opts.episodeId ?? randomUUID();
  }

  /**
   * 本轮用户消息到来时调（在 push 该轮之前）：
   *   - 定 episodeId：explicit 传了用它；否则 idle 超阈 → 新 episode；否则沿用当前。
   *   - 返回「上一轮 AI 那句」（供 preceding_ai_context）。
   * @param atMs 本轮发生时间（ms）——来自 occurredAt 或注入 clock，切分判定确定性由调用方保证。
   */
  beginUserTurn(
    atMs: number,
    explicitEpisodeId?: string,
  ): { precedingAiContext: string | null; episodeId: string } {
    const preceding = this.precedingAiContext();
    if (explicitEpisodeId) {
      this.episodeId = explicitEpisodeId;
    } else if (this.lastTurnAtMs != null && atMs - this.lastTurnAtMs > this.idleMs) {
      this.episodeId = randomUUID(); // idle 超阈 → 新一段交互
    }
    this.lastTurnAtMs = atMs;
    return { precedingAiContext: preceding, episodeId: this.episodeId };
  }

  /** 窗口里最近一轮 assistant 的内容（无则 null）。 */
  precedingAiContext(): string | null {
    const turns = this.window.context();
    for (let i = turns.length - 1; i >= 0; i--) {
      if (turns[i]!.role === 'assistant') return turns[i]!.content;
    }
    return null;
  }

  pushUser(content: string): void {
    this.window.push({ role: 'user', content });
  }

  /** push 宿主生成的 AI 回复（recordAssistantReply）——只作后续上下文，绝不落证据。 */
  pushAssistant(content: string): void {
    this.window.push({ role: 'assistant', content });
  }

  /** 种入历史轮（续聊）。 */
  seed(turns: Turn[]): void {
    for (const t of turns) this.window.push(t);
  }

  get currentEpisodeId(): string {
    return this.episodeId;
  }
}
