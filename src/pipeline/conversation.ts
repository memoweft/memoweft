/**
 * 会话编排（地图 cell 4：读写解耦）。阶段 0 骨架 + 阶段 1b 召回注入。
 *
 * 一轮 = 感知用户消息 → 存为证据 → 召回相关认知 → 带窗口 + 注入认知回话。
 * 纪律：
 *   - 只把【用户消息】存为证据；助手回话【不】落证据（禁止系统自证，cell 8 规则 4）。
 *   - 召回的是【认知】（画像条目），由 retriever 在 updateProfile 时索引；这里只 search。
 *   - 召回 / 回话失败不影响证据已落库（先存后答）。
 */
import { config, resolveLang, type MemoWeftConfig } from '../config.ts';
import type { EvidenceStore } from '../evidence/store.ts';
import type { Evidence } from '../evidence/model.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { Retriever } from '../retrieval/retriever.ts';
import type { LLMClient } from '../llm/client.ts';
import { WorkingMemory, type Turn } from './workingMemory.ts';
import { perceive, type PerceiveOptions } from './perceive.ts';
import { reply, type RelevantCognition } from './action.ts';
import { recallCognitions } from '../retrieval/recall.ts';
import { systemClock, type Clock } from '../clock.ts';

export interface ConversationDeps {
  store: EvidenceStore;
  retriever: Retriever;
  cognitionStore: CognitionStore;
  llm: LLMClient;
  /** 可注入配置（P2-5 config 去单例）：不传 = 用全局单例（含召回 topK/阈值、窗口轮数）。 */
  config?: MemoWeftConfig;
  /**
   * 续聊种子（S4 会话持久化）：打开一条旧会话时，把它最近几轮种回工作记忆，
   * 让续聊带上下文。缺省空 = 全新会话，行为同旧。只影响回话窗口，不落库、不产证据。
   */
  seedTurns?: Turn[];
  /** 宿主注入的回话人设/系统提示（cell 9：语气·角色归宿主）；缺省用库内最朴素提示。 */
  systemPrompt?: string;
  /** 可注入时钟（Phase 4）：召回衰减门控的"现在"走它；缺省真实系统时间。前进它 → 淡了的情绪衰减出局。 */
  clock?: Clock;
}

/** 召回到、注入了回话的一条（含相似度，供透视）。 */
export interface RecalledCognition extends RelevantCognition {
  score: number;
  /** 认知 id（批次2 增量：共享召回函数随手带回，供管理/透视反查）。可选以兼容旧构造处。 */
  id?: string;
}

export interface TurnOutcome {
  reply: string;
  storedEvidence: Evidence;
  recall: RecalledCognition[];
  llmCalls: number;
  error: string | null;
}

export class Conversation {
  private readonly deps: ConversationDeps;
  private readonly window: WorkingMemory;

  constructor(deps: ConversationDeps) {
    this.deps = deps;
    this.window = new WorkingMemory((deps.config ?? config).workingMemory.maxTurns);
    // 续聊：把旧会话最近几轮种回工作记忆（超窗上限由 WorkingMemory 自己丢最老的）。
    for (const t of deps.seedTurns ?? []) this.window.push(t);
  }

  async handle(userMsg: string, opts: PerceiveOptions = {}): Promise<TurnOutcome> {
    const { store, retriever, cognitionStore, llm } = this.deps;
    const cfg = this.deps.config ?? config; // 可注入配置（缺省=单例）

    // 1) 感知 → 存证据（只存用户的，亲口）。先存，后答。
    const stored = store.put(perceive(userMsg, opts, cfg));

    // 2) 召回相关认知（阶段 1b）。失败不挡回话。
    // 召回段已抽为共享函数 retrieval/recall.ts（批次2）：门槛顺序与判断条件原样搬走、语义零变化，
    // Conversation 与 core.recall 共用同一段；这里只保留"失败不挡回话"的容错壳。
    let recall: RecalledCognition[] = [];
    try {
      recall = await recallCognitions(userMsg, stored.subjectId, { retriever, cognitionStore }, cfg, (this.deps.clock ?? systemClock)());
    } catch {
      /* 召回失败 → 当作无召回，照常回话 */
    }

    // 3) 回话：带最近几轮窗口 + 注入相关认知。助手回话不落证据。
    const recent = this.window.context();
    let replyText = '';
    let llmCalls = 0;
    let error: string | null = null;
    try {
      const r = await reply(userMsg, recent, recall, llm, this.deps.systemPrompt);
      replyText = r.text;
      llmCalls = r.llmCalls;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
      replyText =
        resolveLang(cfg) === 'zh'
          ? '（回话失败，但你的话已存为证据）'
          : '(Reply failed, but your message has been saved as evidence.)';
    }

    this.window.push({ role: 'user', content: userMsg });
    this.window.push({ role: 'assistant', content: replyText });

    return { reply: replyText, storedEvidence: stored, recall, llmCalls, error };
  }
}
