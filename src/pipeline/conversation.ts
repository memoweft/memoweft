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
import type { Evidence, SourceKind } from '../evidence/model.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { EvidenceRelation, ContentType } from '../cognition/model.ts';
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

/** 召回解释（D-0021）：一条召回认知背后的一条支撑/反证证据简报，供宿主看"这条记忆建立在什么上"（可追溯卖点）。
 *  带 allowCloudRead/allowInference 授权位（对齐 buildMemoryGraph 惯例）：summary 是证据【原文】（可能比派生认知更敏感、
 *  含云受限的 observed/tool），宿主把 provenance 转发云模型前应据此按 tier 自筛——库不自动喂云，DTO 附授权位让宿主筛得了。 */
export interface RecalledEvidence {
  evidenceId: string;
  relation: EvidenceRelation;
  /** 证据简报（summary，无则回退 rawContent）。 */
  summary: string;
  sourceKind: SourceKind;
  /** 授权位（对齐 buildMemoryGraph）：宿主据此在转发云模型前自筛（observed/tool 默认 allowCloudRead=false）。 */
  allowCloudRead: boolean;
  allowInference: boolean;
}

/** 召回到、注入了回话的一条（含相似度，供透视）。 */
export interface RecalledCognition extends RelevantCognition {
  score: number;
  /** 认知 id（批次2 增量：共享召回函数随手带回，供管理/透视反查）。可选以兼容旧构造处。 */
  id?: string;
  /** 认知类型（D-0022：召回结果带回,供宿主看类型 + core.recall 的 contentTypes 过滤）。可选以兼容旧构造处。 */
  contentType?: ContentType;
  /** 召回解释（D-0021）：仅在 core.recall({ explain: true }) 时带；本条认知的支撑/反证证据链（可追溯,带授权位供宿主自筛）。 */
  provenance?: RecalledEvidence[];
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
    // 附和/AI 上下文（D-0033 Phase 1b）:先存后答 → 此刻 window 里还留着【上一轮 AI 那句】
    //   （本轮 user/assistant 要到 L119-120 才 push）。抓下它作为【只读上下文】随证据落进
    //   preceding_ai_context 列（写入端 EvidenceInput，不进 Evidence 读结构 → 永不外泄为证据）。
    //   让孤儿回应("AI:你喜欢爬山吧? 用户:是的")的信息进得了 distill/consolidate、产得出 confirmed 认知。
    //   只此 Conversation 路捕得到（裸 ingest 无 working memory 窗口 → 缺省 null）。
    const precedingAiContext = [...this.window.context()].reverse().find((t) => t.role === 'assistant')?.content ?? null;
    const stored = store.put({ ...perceive(userMsg, opts, cfg), precedingAiContext });

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
