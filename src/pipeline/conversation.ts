/**
 * 会话编排（地图 cell 4：读写解耦）。阶段 0 骨架 + 阶段 1b 召回注入。
 *
 * 一轮 = 感知用户消息 → 存为证据 → 召回相关认知 → 带窗口 + 注入认知回话。
 * 纪律：
 *   - 只把【用户消息】存为证据；助手回话【不】落证据（禁止系统自证，cell 8 规则 4）。
 *   - 召回的是【认知】（画像条目），由 retriever 在 updateProfile 时索引；这里只 search。
 *   - 召回 / 回话失败不影响证据已落库（先存后答）。
 */
import { config } from '../config.ts';
import type { EvidenceStore } from '../evidence/store.ts';
import type { Evidence } from '../evidence/model.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { Retriever } from '../retrieval/retriever.ts';
import type { LLMClient } from '../llm/client.ts';
import { WorkingMemory } from './workingMemory.ts';
import { perceive, type PerceiveOptions } from './perceive.ts';
import { reply, type RelevantCognition } from './action.ts';
import { effectiveConfidence } from '../background/decay.ts';

export interface ConversationDeps {
  store: EvidenceStore;
  retriever: Retriever;
  cognitionStore: CognitionStore;
  llm: LLMClient;
}

/** 召回到、注入了回话的一条（含相似度，供透视）。 */
export interface RecalledCognition extends RelevantCognition {
  score: number;
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
    this.window = new WorkingMemory();
  }

  async handle(userMsg: string, opts: PerceiveOptions = {}): Promise<TurnOutcome> {
    const { store, retriever, cognitionStore, llm } = this.deps;

    // 1) 感知 → 存证据（只存用户的，亲口）。先存，后答。
    const stored = store.put(perceive(userMsg, opts));

    // 2) 召回相关认知（阶段 1b）。失败不挡回话。
    const recall: RecalledCognition[] = [];
    try {
      const hits = await retriever.search(userMsg, config.retrieval.topK);
      for (const h of hits) {
        const c = cognitionStore.get(h.id);
        if (!c || c.invalidAt) continue; // 失效的不注入（即便索引还没重建，也别把过期/被纠正的塞回话）
        // 衰减门控（cell 8 规则 8）：把握度用【有效置信】，淡了的情绪/过气的假设直接不注入。
        const eff = effectiveConfidence(c);
        if (eff < config.retrieval.minEffectiveConfidence) continue;
        recall.push({ content: c.content, confidence: eff, credStatus: c.credStatus, score: h.score });
      }
    } catch {
      /* 召回失败 → 当作无召回，照常回话 */
    }

    // 3) 回话：带最近几轮窗口 + 注入相关认知。助手回话不落证据。
    const recent = this.window.context();
    let replyText = '';
    let llmCalls = 0;
    let error: string | null = null;
    try {
      const r = await reply(userMsg, recent, recall, llm);
      replyText = r.text;
      llmCalls = r.llmCalls;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
      replyText = '（回话失败，但你的话已存为证据）';
    }

    this.window.push({ role: 'user', content: userMsg });
    this.window.push({ role: 'assistant', content: replyText });

    return { reply: replyText, storedEvidence: stored, recall, llmCalls, error };
  }
}
