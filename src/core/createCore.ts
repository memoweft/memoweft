/**
 * MemoWeft Core 统一入口（架构归位·批次2，路线 §5.1）。
 *
 * Host 后续优先经这里调 Core，不到处直接碰 Sqlite*Store。工厂负责把散装部件
 * （stores / retriever / LLM 池 / 事务器 / 受控管理 API）按既有降级路数装配好：
 *   - LLM 缺配不崩：loadLLMPool 的 failStub 语义——真调用才报错（与 testbench 现行为一致）。
 *   - 嵌入缺配不崩：loadEmbedConfig 返回 null → NullRetriever（召回降级为空，回话不注入画像）。
 * 所以【无 .env 也能建 core】，能干存证据/管理记忆这类不碰模型的活。
 *
 * subjectId 口径：各方法入参 subjectId 可选，缺省 config.identity.subjectId（v1 单人单宿主）。
 * vectorDbPath 口径：向量索引"一个 subject 一个实例"的既有契约不变（见地图「召回边界」）；
 *   缺省与 dbPath 同库（vectors 表挂同一文件，testbench 同款）。
 */
import { config as globalConfig, type MemoWeftConfig } from '../config.ts';
import { openStores } from '../store/openStores.ts';
import { perceive } from '../pipeline/perceive.ts';
import { Conversation, type TurnOutcome, type RecalledCognition } from '../pipeline/conversation.ts';
import type { Turn } from '../pipeline/workingMemory.ts';
import { ingestObservations, type Observation } from '../perception/ingest.ts';
import { updateProfile as runUpdateProfile, type UpdateProfileResult } from '../consolidation/updateProfile.ts';
import { recallCognitions } from '../retrieval/recall.ts';
import { NullRetriever } from '../retrieval/nullRetriever.ts';
import { VectorRetriever } from '../retrieval/vectorRetriever.ts';
import { OpenAICompatEmbedder, loadEmbedConfig, type Embedder } from '../retrieval/embedder.ts';
import type { Retriever } from '../retrieval/retriever.ts';
import { loadLLMPool, type LLMPool } from '../llm/pool.ts';
import { OpenAICompatClient, type LLMClient } from '../llm/client.ts';
import { createMemoryManagementAPI, type MemoryManagementAPI } from '../memory/managementApi.ts';
import { exportBundle, importBundle, validateBundle } from '../portable/index.ts';
import type { MemoryBundle, ImportPlan, ValidateResult } from '../portable/model.ts';
import type { ExportOptions } from '../portable/exportBundle.ts';
import type { ImportOptions } from '../portable/importBundle.ts';
import { buildMemoryGraph, type BuildGraphOptions } from '../graph/buildMemoryGraph.ts';
import type { MemoryGraphPayload } from '../graph/model.ts';
import type { Evidence, SourceKind } from '../evidence/model.ts';

// ── 工厂入参 ──

export interface CreateCoreOptions {
  /** 记忆库路径（必填）：三层数据 + 审计表落这里；传 ':memory:' 得到一次性内存库。 */
  dbPath: string;
  /** 大模型：LLMPool（按用途分流 chat/write）或单个 LLMClient（两用途同一个）。缺省 loadLLMPool()（.env 加载，缺配 = 真调用才报错）。 */
  llm?: LLMPool | LLMClient;
  /** 嵌入器：显式注入则用它建 VectorRetriever（优先级高于 env 配置，低于 retriever）。 */
  embedder?: Embedder;
  /** 召回器：注入则最优先；不注入 → loadEmbedConfig() 有配置建 VectorRetriever、无则 NullRetriever。 */
  retriever?: Retriever;
  /** 可注入配置（P2-5 口径）：缺省全局单例。 */
  config?: MemoWeftConfig;
  /** 向量库路径；缺省与 dbPath 同库。一个 subject 一个实例的既有契约不变。 */
  vectorDbPath?: string;
}

// ── 各方法输入类型（Core 只认标准输入，boundaries.md §3）──

export interface UserMessageInput {
  /** 用户原话。 */
  content: string;
  subjectId?: string;
  hostId?: string;
  /** 缺省 'spoken'（用户亲口）。 */
  sourceKind?: SourceKind;
  /** 幂等键：同 originId 重复摄入只落一条。 */
  originId?: string | null;
  occurredAt?: string;
}

export interface ObservationInput {
  /** 一批标准化好的观察（授权位显式 > config.observedDefaults）。 */
  observations: Observation[];
  subjectId?: string;
  hostId?: string;
}

export interface RecallInput {
  query: string;
  subjectId?: string;
}

export interface ConversationInput {
  /** 用户这轮说的话。 */
  message: string;
  /** 会话标识；缺省 'default'。同 id 复用同一个 Conversation 实例（窗口连续）。 */
  conversationId?: string;
  subjectId?: string;
  hostId?: string;
  originId?: string | null;
  occurredAt?: string;
  /** 宿主人设/系统提示：仅该 conversationId 首次调用（建实例）时生效，后续调用不重建、忽略此参。 */
  systemPrompt?: string;
  /** 续聊种子：同上，仅首次建实例时生效。 */
  seedTurns?: Turn[];
}

export interface UpdateProfileInput {
  subjectId?: string;
}

/** 便携记忆包薄封装：deps 已绑定，Host 只管传业务参数。 */
export interface PortableAPI {
  exportBundle(opts?: ExportOptions & { subjectId?: string }): MemoryBundle;
  importBundle(bundle: MemoryBundle, opts: ImportOptions): ImportPlan;
  validateBundle(bundle: unknown): ValidateResult;
}

/** 图谱薄封装：deps 已绑定。 */
export interface MemoryGraphAPI {
  buildMemoryGraph(opts?: BuildGraphOptions & { subjectId?: string }): MemoryGraphPayload;
}

/** 健康自检结果（首启门用：决定进配置向导还是直接聊天）。 */
export interface HealthReport {
  /** 这个 core 能否聊天：持有真的对话模型客户端（缺配时是抛错 stub → false）。 */
  llmReady: boolean;
  /** 这个 core 能否语义召回：持有向量召回器（缺嵌入配置时是空召回器 → false）。 */
  embedReady: boolean;
}

/** 统一 Core Facade（路线 §5.1 定稿形态 + close 资源收口）。 */
export interface MemoWeftCore {
  /** 摄入用户消息 → spoken 证据（perceive + put，只存不答；先存后答纪律里"存"的那半）。 */
  ingestUserMessage(input: UserMessageInput): Promise<Evidence>;
  /** 摄入观察 → observed 证据（默认不上云；带 originId 幂等）。返回本次新落库的。 */
  ingestObservation(input: ObservationInput): Promise<Evidence[]>;
  /** 召回相关认知（与 Conversation 同一段共享召回语义：invalid/archived/越界/衰减门控全走）。 */
  recall(input: RecallInput): Promise<RecalledCognition[]>;
  /** 处理一轮对话（存证据 → 召回 → 回话）。同 conversationId 复用实例、窗口连续。 */
  handleConversationTurn(input: ConversationInput): Promise<TurnOutcome>;
  /** 丢弃某会话的活跃实例：下次 handleConversationTurn 会【重建】该会话（此时传的 systemPrompt / seedTurns 才生效）。
   *  Host 切换人设、或重开会话续聊时调它——否则同 conversationId 命中旧实例、新 systemPrompt/seedTurns 被静默忽略。
   *  不存在的 id 静默略过。会话历史是 Host 的持久数据（这里只丢内存里的活跃窗口实例、不碰任何库）。 */
  dropConversation(conversationId: string): void;
  /** 一键更新画像：distill → consolidate → attribute → 重建召回索引。 */
  updateProfile(input?: UpdateProfileInput): Promise<UpdateProfileResult>;
  /** 受控记忆管理（7 操作 + 审计表）。 */
  memory: MemoryManagementAPI;
  /** 便携记忆包（导出/导入/校验）。 */
  portable: PortableAPI;
  /** 图谱 payload。 */
  graph: MemoryGraphAPI;
  /** 健康自检（首启门）：基于本 core 实际持有的部件判断能否聊天 / 能否语义召回。 */
  health(): HealthReport;
  /** 关掉共享连接与自建的向量库连接（注入的 retriever 归调用方管，不动）。 */
  close(): void;
}

/** llm 入参归一成池：单个 client → 两用途同一个；缺省 → env 装配（缺配不崩，真调用才报）。 */
function asPool(llm?: LLMPool | LLMClient): LLMPool {
  if (!llm) return loadLLMPool();
  if ('for' in llm && typeof llm.for === 'function') return llm;
  const client = llm as LLMClient;
  return { for: () => client };
}

export function createMemoWeftCore(options: CreateCoreOptions): MemoWeftCore {
  const cfg = options.config ?? globalConfig;
  const stores = openStores(options.dbPath, cfg);
  const { evidenceStore, eventStore, cognitionStore, transaction } = stores;
  const pool = asPool(options.llm);

  // 召回器解析：注入 > 注入 embedder 建向量召回 > env 有嵌入配置建向量召回 > 空召回（降级不崩）。
  let retriever: Retriever;
  let ownsRetriever = false; // 只关自建的；注入的归调用方管
  if (options.retriever) {
    retriever = options.retriever;
  } else {
    const vectorDbPath = options.vectorDbPath ?? options.dbPath;
    const embedder = options.embedder ?? (() => {
      const ec = loadEmbedConfig();
      return ec ? new OpenAICompatEmbedder(ec) : null;
    })();
    retriever = embedder ? new VectorRetriever(vectorDbPath, embedder) : new NullRetriever();
    ownsRetriever = true;
  }

  const subjectOf = (explicit?: string) => explicit ?? cfg.identity.subjectId;
  // 会话缓存：conversationId → Conversation。首次建实例（systemPrompt/seedTurns 此时生效），后续复用不重建。
  const conversations = new Map<string, Conversation>();

  return {
    async ingestUserMessage(input) {
      // testbench/server.mjs 现行组合（perceive → put）的正式归位：Host 以后调这里，不再自己拼。
      return evidenceStore.put(
        perceive(input.content, {
          subjectId: input.subjectId,
          hostId: input.hostId,
          sourceKind: input.sourceKind,
          originId: input.originId,
          occurredAt: input.occurredAt,
        }, cfg),
      );
    },

    async ingestObservation(input) {
      const r = ingestObservations(subjectOf(input.subjectId), input.observations, {
        evidenceStore,
        hostId: input.hostId,
        config: cfg,
      });
      return r.stored;
    },

    async recall(input) {
      return recallCognitions(input.query, subjectOf(input.subjectId), { retriever, cognitionStore }, cfg);
    },

    async handleConversationTurn(input) {
      const id = input.conversationId ?? 'default';
      let convo = conversations.get(id);
      if (!convo) {
        convo = new Conversation({
          store: evidenceStore,
          retriever,
          cognitionStore,
          llm: pool.for('chat'),
          config: cfg,
          systemPrompt: input.systemPrompt,
          seedTurns: input.seedTurns,
        });
        conversations.set(id, convo);
      }
      return convo.handle(input.message, {
        subjectId: input.subjectId,
        hostId: input.hostId,
        originId: input.originId,
        occurredAt: input.occurredAt,
      });
    },

    dropConversation(conversationId) {
      // 丢弃活跃实例 → 下次该 conversationId 会重建（systemPrompt/seedTurns 仅在建实例时生效，
      //   不丢就换不了人设、也重种不了续聊窗口）。只删内存里的实例，不碰库。
      conversations.delete(conversationId);
    },

    async updateProfile(input = {}) {
      return runUpdateProfile(subjectOf(input.subjectId), {
        evidenceStore,
        eventStore,
        cognitionStore,
        retriever,
        llm: pool.for('write'), // 写路径走小快模型（缺配自动回退 chat，见 llm/pool.ts）
        transaction,
        config: cfg,
      });
    },

    // 把本 core 的 retriever 传进 memory：resetSubject 清向量索引要它（indexAll([])）。
    memory: createMemoryManagementAPI(stores, cfg, { retriever }),

    portable: {
      exportBundle(opts = {}) {
        const { subjectId, ...rest } = opts;
        return exportBundle(subjectOf(subjectId), { evidenceStore, eventStore, cognitionStore }, rest);
      },
      importBundle(bundle, opts) {
        return importBundle(bundle, { evidenceStore, eventStore, cognitionStore, transaction }, opts);
      },
      validateBundle,
    },

    graph: {
      buildMemoryGraph(opts = {}) {
        const { subjectId, ...rest } = opts;
        return buildMemoryGraph(subjectOf(subjectId), { evidenceStore, eventStore, cognitionStore }, rest);
      },
    },

    health() {
      // 口径（与 testbench /api/health 对齐、基于 core 实际持有的部件判断，不重查 env）：
      //   llmReady = 持有真的对话模型客户端。env 装配时：缺 chat 配 → pool.for('chat') 是 failStub（非
      //     OpenAICompatClient）→ false；有配 → OpenAICompatClient → true，与 testbench loadLLMConfig() 一致。
      //     注入自定义 client（如测试 stub）判 false——stub 不是真能聊的模型，语义正确。
      //   embedReady = 持有向量召回器。env 装配时：有 EMBED 配置 → VectorRetriever → true；无 → NullRetriever
      //     → false，与 testbench loadEmbedConfig() 一致。注入空召回器同样判 false。
      return {
        llmReady: pool.for('chat') instanceof OpenAICompatClient,
        embedReady: retriever instanceof VectorRetriever,
      };
    },

    close() {
      stores.close();
      // 自建的向量召回器有独立连接要关；NullRetriever 没有 close，注入的归调用方。
      if (ownsRetriever && retriever instanceof VectorRetriever) retriever.close();
    },
  };
}
