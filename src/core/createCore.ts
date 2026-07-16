/**
 * MemoWeft Core 统一入口（架构归位·批次2，路线 §5.1）。
 *
 * Host 后续优先经这里调 Core，不到处直接碰 Sqlite*Store。工厂负责把散装部件
 * （stores / retriever / LLM 池 / 事务器 / 受控管理 API）按既有降级路数装配好：
 *   - LLM 缺配不崩：loadLLMPool 的 failStub 语义——真调用才报错（与 testbench 现行为一致）。
 *   - 嵌入缺配不崩：loadEmbedConfig 返回 null → KeywordRetriever（D-0017,FTS5 关键词召回;FTS5 不可用再降 NullRetriever 召回空）。
 * 所以【无 .env 也能建 core】，能干存证据/管理记忆这类不碰模型的活。
 *
 * subjectId 口径：各方法入参 subjectId 可选，缺省 config.identity.subjectId（v1 单人单宿主）。
 * vectorDbPath 口径：向量索引"一个 subject 一个实例"的既有契约不变（见地图「召回边界」）；
 *   缺省与 dbPath 同库（vectors 表挂同一文件，testbench 同款）。
 */
import { config as globalConfig, type MemoWeftConfig } from '../config.ts';
import { systemClock, type Clock } from '../clock.ts';
import { openStores } from '../store/openStores.ts';
import { perceive } from '../pipeline/perceive.ts';
import { Conversation, type TurnOutcome, type RecalledCognition } from '../pipeline/conversation.ts';
import type { Turn } from '../pipeline/workingMemory.ts';
import { InteractionSession } from '../pipeline/interactionSession.ts';
import type { VisibleTurn } from '../interaction/model.ts';
import { ingestObservations, type Observation } from '../perception/ingest.ts';
import { updateProfile as runUpdateProfile, type UpdateProfileResult } from '../consolidation/updateProfile.ts';
import { recallCognitions } from '../retrieval/recall.ts';
import type { ContentType } from '../cognition/model.ts';
import { NullRetriever } from '../retrieval/nullRetriever.ts';
import { VectorRetriever } from '../retrieval/vectorRetriever.ts';
import { KeywordRetriever, FtsUnavailableError } from '../retrieval/keywordRetriever.ts';
import { OpenAICompatEmbedder, loadEmbedConfig, type Embedder } from '../retrieval/embedder.ts';
import type { Retriever } from '../retrieval/retriever.ts';
import { loadLLMPool, type LLMPool } from '../llm/pool.ts';
import { OpenAICompatClient, type LLMClient, type UsageStats } from '../llm/client.ts';
import { createMemoryManagementAPI, type MemoryManagementAPI } from '../memory/managementApi.ts';
import { exportBundle, importBundle, validateBundle } from '../portable/index.ts';
import type { MemoryBundle, ImportPlan, ValidateResult } from '../portable/model.ts';
import type { ExportOptions } from '../portable/exportBundle.ts';
import type { ImportOptions } from '../portable/importBundle.ts';
import { buildMemoryGraph, type BuildGraphOptions } from '../graph/buildMemoryGraph.ts';
import type { MemoryGraphPayload } from '../graph/model.ts';
import type { Evidence, SourceKind } from '../evidence/model.ts';
import type { MemoWeftPlugin, PluginContext, PluginUserMessage } from '../plugin/contract.ts';

// ── 工厂入参 ──

export interface CreateCoreOptions {
  /** 记忆库路径（必填）：三层数据 + 审计表落这里；传 ':memory:' 得到一次性内存库。 */
  dbPath: string;
  /** 大模型：LLMPool（按用途分流 chat/write）或单个 LLMClient（两用途同一个）。缺省 loadLLMPool()（.env 加载，缺配 = 真调用才报错）。 */
  llm?: LLMPool | LLMClient;
  /** 嵌入器：显式注入则用它建 VectorRetriever（优先级高于 env 配置，低于 retriever）。 */
  embedder?: Embedder;
  /** 召回器：注入则最优先；不注入 → loadEmbedConfig() 有配置建 VectorRetriever、无则 KeywordRetriever(D-0017,FTS5 不可用降 NullRetriever)。 */
  retriever?: Retriever;
  /** 可注入配置（P2-5 口径）：缺省全局单例。 */
  config?: MemoWeftConfig;
  /** 向量库路径；缺省与 dbPath 同库。一个 subject 一个实例的既有契约不变。 */
  vectorDbPath?: string;
  /** 可注入时钟（Phase 4）：三个 store 的落库/更新时间源；缺省真实系统时间。
   *  注入固定/前进的 clock 得确定性（两次运行时间戳一致）+ 时间旅行（demo --fast-forward）。
   *  只产时间戳、绝不进置信度自算（铁律 3b）。 */
  clock?: Clock;
  /** 插件（第 7 步·契约 v2·experimental）：experience（systemPrompt·Host 每轮传）/ tool / collector（hook + 声明权限）。
   *  不传 = 无插件，行为同旧。hook 在方法层烧、只观察不改管线；每个 hook 拿受限 PluginContext（按声明权限门控、不持 store）。 */
  plugins?: MemoWeftPlugin[];
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
  /**
   * 会话标识（v0.6·D-0034）：带上它，core 就为该会话维护一份上下文窗口——把「上一轮 AI 那句」
   *   捕获进证据的 preceding_ai_context（供下游理解附和/短回答），并落一条 interaction_context。
   *   宿主生成 AI 回复后调 recordAssistantReply(conversationId) 报告，下一轮即可捕获。
   *   不传 = 无会话上下文，行为同旧（裸摄入）。
   */
  conversationId?: string;
  /** 交互 episode 标识（v0.6）：宿主可选传（如按会话/天切段）；不传则库内按 idle 间隔自动切分。仅带 conversationId 时有意义。 */
  episodeId?: string;
}

export interface ObservationInput {
  /** 一批标准化好的观察（授权位显式 > config.observedDefaults）。 */
  observations: Observation[];
  subjectId?: string;
  hostId?: string;
}

export interface ToolResultInput {
  /** 工具执行的【返回结果】载荷（文本；结构化结果请序列化后传入）。
   *  铁律 3a：只应传工具真实返回的外部数据，不应传 LLM 的调用意图/入参（那是助手输出，禁摄入）。 */
  content: string;
  subjectId?: string;
  hostId?: string;
  /** 幂等键：同 originId 重复摄入只落一条（建议用 toolCallId）。 */
  originId?: string | null;
  occurredAt?: string;
}

export interface RecallInput {
  query: string;
  subjectId?: string;
  /** 召回解释（D-0021）：true → 每条召回认知带上其支撑证据链（provenance）。缺省 false = 不做额外查询、行为不变。 */
  explain?: boolean;
  /** 按认知类型过滤（D-0022）：只召回这些类型（允许名单）。不传/空 = 全类型（行为不变）。
   *  后过滤（top-K 取完之后）：若命中里没有匹配类型的，可能返回 <topK 甚至空（同 similarity/衰减门控层）。 */
  contentTypes?: ContentType[];
}

export interface ConversationInput {
  /** 用户这轮说的话。 */
  message: string;
  /** 会话标识；缺省 'default'。同 id 复用同一个 Conversation 实例（窗口连续）。 */
  conversationId?: string;
  /** 交互 episode 标识（v0.6·D-0034）：宿主可选传；不传则库内按 idle 间隔自动切分。落进本轮 interaction_context。 */
  episodeId?: string;
  subjectId?: string;
  hostId?: string;
  originId?: string | null;
  occurredAt?: string;
  /** 宿主人设/系统提示：仅该 conversationId 首次调用（建实例）时生效，后续调用不重建、忽略此参。 */
  systemPrompt?: string;
  /** 续聊种子：同上，仅首次建实例时生效。 */
  seedTurns?: Turn[];
}

/** 报告一轮 AI 回复（v0.6·D-0034）：宿主自建 agent 循环时，把生成的助手回复交给 core 作后续上下文。 */
export interface RecordAssistantReplyInput {
  /** 目标会话（须与 ingestUserMessage 用的一致）。 */
  conversationId: string;
  /** 这轮 AI 回复的文本。**只进上下文窗口、永不落证据**（铁律 3a）。 */
  content: string;
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

/** token 用量累计报告（档8·观测/计费·「宿主能算钱」）：本 core 至今累计，按 llm / embed 分桶 + 合计。
 *  只给原始计数（宿主乘单价算钱），库不内置价目表。宿主要按对话/画像切分，调用前后取差值即可。 */
export interface UsageReport {
  /** 对话 + 写路径模型累计（chat/write 两用途去重后）。 */
  llm: UsageStats;
  /** 嵌入模型累计（自建 embedder 时有值；注入自定义 retriever 时为 0，embed 归宿主自管）。 */
  embed: UsageStats;
  /** llm + embed 合计。 */
  total: UsageStats;
}

/** 统一 Core Facade（路线 §5.1 定稿形态 + close 资源收口）。 */
export interface MemoWeftCore {
  /** 摄入用户消息 → spoken 证据（perceive + put，只存不答；先存后答纪律里"存"的那半）。 */
  ingestUserMessage(input: UserMessageInput): Promise<Evidence>;
  /** 摄入观察 → observed 证据（默认不上云；带 originId 幂等）。返回本次新落库的。 */
  ingestObservation(input: ObservationInput): Promise<Evidence[]>;
  /** 摄入一条工具执行结果 → tool 证据（默认不上云，config.toolDefaults；带 originId 幂等）。
   *  只摄入结果载荷、不摄入调用意图（铁律 3a）；要给某条 tool 证据开上云走 memory.updateEvidenceAuthorization（带审计）。 */
  ingestToolResult(input: ToolResultInput): Promise<Evidence>;
  /** 召回相关认知（与 Conversation 同一段共享召回语义：invalid/archived/越界/衰减门控全走）。 */
  recall(input: RecallInput): Promise<RecalledCognition[]>;
  /** 处理一轮对话（存证据 → 召回 → 回话）。同 conversationId 复用实例、窗口连续。 */
  handleConversationTurn(input: ConversationInput): Promise<TurnOutcome>;
  /** 报告一轮 AI 回复（v0.6·D-0034）：自建 agent 循环的宿主用它把助手回复交给 core 作后续上下文（只进窗口、不落证据，3a）。
   *  下一轮 ingestUserMessage(同 conversationId) 即把它当「上一轮 AI 那句」捕获进 preceding_ai_context。 */
  recordAssistantReply(input: RecordAssistantReplyInput): void;
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
  /** token 用量累计（档8·观测/计费）：本 core 至今累计（llm/embed 分桶 + 合计）。端点常不回 usage 时对应桶为 0。 */
  usage(): UsageReport;
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

const ZERO_USAGE: UsageStats = { promptTokens: 0, completionTokens: 0, totalTokens: 0, callsWithUsage: 0 };

/** 累加两份 usage（b 缺省——client/embedder 没实现 usage——则原样返回 a）。 */
function sumUsage(a: UsageStats, b: UsageStats | undefined): UsageStats {
  if (!b) return a;
  return {
    promptTokens: a.promptTokens + b.promptTokens,
    completionTokens: a.completionTokens + b.completionTokens,
    totalTokens: a.totalTokens + b.totalTokens,
    callsWithUsage: a.callsWithUsage + b.callsWithUsage,
  };
}

/** 无 embedder 时的召回兜底（D-0017）:KeywordRetriever（FTS5 关键词,零嵌入成本,大语料强基线);
 *  FTS5 建虚表失败（FtsUnavailableError,见 D-0007）→ 降 NullRetriever(召回空、不崩)。 */
function keywordOrNull(dbPath: string): Retriever {
  try {
    return new KeywordRetriever(dbPath);
  } catch (e) {
    if (e instanceof FtsUnavailableError) return new NullRetriever();
    throw e;
  }
}

export function createMemoWeftCore(options: CreateCoreOptions): MemoWeftCore {
  const cfg = options.config ?? globalConfig;
  const stores = openStores(options.dbPath, cfg, options.clock);
  const { evidenceStore, eventStore, cognitionStore, transaction } = stores;
  const pool = asPool(options.llm);

  // 召回器解析：注入 > 注入 embedder 建向量召回 > env 有嵌入配置建向量召回 > 空召回（降级不崩）。
  let retriever: Retriever;
  let ownsRetriever = false; // 只关自建的；注入的归调用方管
  let embedderRef: Embedder | null = null; // 自建时持有，供 usage() 读 embed token；注入 retriever 时拿不到（宿主自管）
  if (options.retriever) {
    retriever = options.retriever;
  } else {
    const vectorDbPath = options.vectorDbPath ?? options.dbPath;
    const embedder = options.embedder ?? (() => {
      const ec = loadEmbedConfig();
      return ec ? new OpenAICompatEmbedder(ec) : null;
    })();
    embedderRef = embedder;
    // 无 embedder 兜底（D-0017,§14.4b 重评估）:优先 KeywordRetriever（FTS5,零嵌入;§19.2 LoCoMo 大语料 Recall
    //   远胜空召回）;FTS5 不可用（FtsUnavailableError,D-0007)再降 NullRetriever(不崩)。有 embedder 照旧走向量。
    retriever = embedder ? new VectorRetriever(vectorDbPath, embedder) : keywordOrNull(vectorDbPath);
    ownsRetriever = true;
  }

  const subjectOf = (explicit?: string) => explicit ?? cfg.identity.subjectId;
  // 会话缓存：conversationId → Conversation。首次建实例（systemPrompt/seedTurns 此时生效），后续复用不重建。
  const conversations = new Map<string, Conversation>();
  // 交互会话缓存（v0.6·D-0034）：conversationId → InteractionSession。裸 ingestUserMessage 路的上下文窗口 +
  //   episode 切分在此维护（与 conversations 分开：那是回话实例，这是纯上下文；不走回话的宿主只用 sessions）。
  const sessions = new Map<string, InteractionSession>();

  // ── 插件（第 7 步·契约 v2）：hook 全在本工厂的方法层烧，conversation.ts / ingest.ts 纯逻辑不碰。──
  const plugins = options.plugins ?? [];

  /** 插件出错不崩主流程：记日志、吞掉（呼应"召回失败不挡回话"）。 */
  function logPluginError(hook: string, pluginId: string, e: unknown): void {
    console.warn(`[memoweft/plugin] plugin '${pluginId}' ${hook} hook threw (ignored):`, e instanceof Error ? e.message : e);
  }

  /**
   * 受限上下文（闭包给·绝不交 store）：按【该插件声明的权限】门控 + 绑当次 subject。
   * submitObservation 显式只取白名单字段、【不带授权位】→ ingestObservations 走 observedDefaults（cloud=false）；
   *   防插件运行时塞 allowCloudRead:true 因"显式>默认"绕过"observed 不上云"（Host sanitizeObservation 的 Core 侧等价）。
   * 注意：submitObservation 走【纯函数 ingestObservations】、【不走烧 onObservation 的方法】——插件提交的观察照常落库，
   *   但【不再级联触发 onObservation】，杜绝"插件 onObservation→submitObservation→再 onObservation"的重入死循环。
   */
  function makePluginContext(plugin: MemoWeftPlugin, subjectId: string): PluginContext {
    return {
      async submitObservation(input) {
        if (!plugin.permissions?.submitObservation) {
          throw new Error(`plugin '${plugin.id}' has no 'submitObservation' permission`);
        }
        const clean: Observation = {
          kind: input.kind,
          occurredAt: input.occurredAt,
          content: input.content,
          originId: input.originId ?? null,
          meta: input.meta,
        };
        ingestObservations(subjectId, [clean], { evidenceStore, config: cfg });
      },
      async requestMemory(query) {
        if (!plugin.permissions?.requestMemory) {
          throw new Error(`plugin '${plugin.id}' has no 'requestMemory' permission`);
        }
        return recallCognitions(query, subjectId, { retriever, cognitionStore }, cfg);
      },
    };
  }

  /** 逐插件烧一个 hook（await + try/catch，出错不崩主流程）。 */
  async function fireHook(
    name: 'onUserMessage' | 'onObservation',
    subjectId: string,
    run: (plugin: MemoWeftPlugin, ctx: PluginContext) => void | Promise<void>,
  ): Promise<void> {
    for (const p of plugins) {
      if (typeof p[name] !== 'function') continue;
      try {
        await run(p, makePluginContext(p, subjectId));
      } catch (e) {
        logPluginError(name, p.id, e);
      }
    }
  }

  // onLoad hook：建 core 时烧一次。【fire-and-forget·不 await】——createMemoWeftCore 保持同步返回（await 会把签名变 Promise、破坏全部调用方）。
  //   此时 stores/retriever 已就绪：ctx 的 submitObservation/requestMemory 可用（新库 requestMemory 可能空）。Promise 链兜住同步/异步抛错。
  for (const p of plugins) {
    if (typeof p.onLoad !== 'function') continue;
    Promise.resolve()
      .then(() => p.onLoad!(makePluginContext(p, cfg.identity.subjectId)))
      .catch((e) => logPluginError('onLoad', p.id, e));
  }

  return {
    async ingestUserMessage(input) {
      // testbench/server.mjs 现行组合（perceive → put）的正式归位：Host 以后调这里，不再自己拼。
      // 交互上下文捕获（v0.6·D-0034）：带 conversationId 时，用 InteractionSession 抓「上一轮 AI 那句」填进
      //   EvidenceInput.precedingAiContext（D-0033 结构墙列）→ 下游 distill/consolidate 的注入逻辑一字不改即对
      //   【裸 ingest 路】生效（修复「weftmate 全走裸 ingest、附和上下文从未捕获」的头号缺口）；并落一条
      //   interaction_context（供 Phase 2 resolver / 审计 / 导出）。不带 conversationId → precedingAiContext=null、不落上下文，行为同旧。
      let precedingAiContext: string | null = null;
      if (input.conversationId) {
        const subjectId = subjectOf(input.subjectId);
        let session = sessions.get(input.conversationId);
        if (!session) {
          session = new InteractionSession({ maxTurns: cfg.workingMemory.maxTurns });
          sessions.set(input.conversationId, session);
        }
        const atMs = input.occurredAt ? Date.parse(input.occurredAt) : (options.clock ?? systemClock)().getTime();
        const turn = session.beginUserTurn(atMs, input.episodeId);
        precedingAiContext = turn.precedingAiContext;
        // 本轮可见上下文快照 = [上一轮 AI（若有）, 本轮用户]——最小可解释单元（禁 system/hidden/CoT）。
        const context: VisibleTurn[] = [];
        if (precedingAiContext) context.push({ role: 'assistant', content: precedingAiContext });
        context.push({ role: 'user', content: input.content });
        stores.interactionContextStore.record({ subjectId, conversationId: input.conversationId, episodeId: turn.episodeId, context });
        session.pushUser(input.content);
      }
      return evidenceStore.put({
        ...perceive(input.content, {
          subjectId: input.subjectId,
          hostId: input.hostId,
          sourceKind: input.sourceKind,
          originId: input.originId,
          occurredAt: input.occurredAt,
        }, cfg),
        precedingAiContext,
      });
    },

    async ingestObservation(input) {
      const subjectId = subjectOf(input.subjectId);
      const r = ingestObservations(subjectId, input.observations, {
        evidenceStore,
        hostId: input.hostId,
        config: cfg,
      });
      // onObservation hook（方法层烧·观察不改管线）：对提交的每条观察烧一遍。ingest.ts 纯逻辑不碰。
      if (plugins.some((p) => typeof p.onObservation === 'function')) {
        for (const obs of input.observations) {
          await fireHook('onObservation', subjectId, (p, ctx) => p.onObservation!(obs, ctx));
        }
      }
      return r.stored;
    },

    async ingestToolResult(input) {
      // 同 ingestUserMessage 的 perceive → put 组合，sourceKind 钉死 'tool'：
      //   授权缺省由 put 按 sourceKind 兜底（toolDefaults：local✓/cloud✗/infer✓，最后防线）；带 originId 幂等。
      return evidenceStore.put(
        perceive(input.content, {
          subjectId: input.subjectId,
          hostId: input.hostId,
          sourceKind: 'tool',
          originId: input.originId,
          occurredAt: input.occurredAt,
        }, cfg),
      );
    },

    async recall(input) {
      // 读路径 now 走注入 clock（Phase 4 S3）：前进 clock → 淡了的情绪衰减出局、事实留存。
      let items = await recallCognitions(input.query, subjectOf(input.subjectId), { retriever, cognitionStore }, cfg, (options.clock ?? systemClock)());
      // 按 contentType 过滤（D-0022，门面特性）：只留允许名单里的类型；后过滤（top-K 取完之后），可能欠填（同 similarity/衰减门控层）。
      if (input.contentTypes?.length) {
        const allow = new Set(input.contentTypes);
        items = items.filter((it) => allow.has(it.contentType));
      }
      if (!input.explain) return items;
      // 召回解释（D-0021）：门面已有两个 store → 逐条补支撑/反证证据链，不动 recallCognitions/RecallDeps。
      //   隐私（对抗审查加固）：provenance 面向宿主、库不自动喂云；但 summary 是证据【原文】（可能比派生认知更敏感、
      //   含云受限的 observed/tool，默认 allowCloudRead=false）→ 随附 allowCloudRead/allowInference 授权位（对齐
      //   buildMemoryGraph L158-159），让宿主转发云模型前能按 tier 自筛；write-path 的 filterReadableByTier 不受影响。
      //   证据已不在（悬挂链，正常级联删不该发生）则跳过、不凭空造字段。
      return items.map((it) => ({
        ...it,
        provenance: cognitionStore.sourcesOf(it.id).flatMap((link) => {
          const e = evidenceStore.get(link.evidenceId);
          return e ? [{ evidenceId: link.evidenceId, relation: link.relation, summary: e.summary || e.rawContent, sourceKind: e.sourceKind, allowCloudRead: e.allowCloudRead, allowInference: e.allowInference }] : [];
        }),
      }));
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
          clock: options.clock, // 读路径 now 走注入 clock（Phase 4 S3）
        });
        conversations.set(id, convo);
      }
      const outcome = await convo.handle(input.message, {
        subjectId: input.subjectId,
        hostId: input.hostId,
        originId: input.originId,
        occurredAt: input.occurredAt,
      });
      // onUserMessage hook（方法层烧·回话已生成后·观察不改管线；返回值丢弃）。conversation.ts 纯逻辑不碰。
      if (plugins.some((p) => typeof p.onUserMessage === 'function')) {
        const msg: PluginUserMessage = {
          content: input.message,
          subjectId: outcome.storedEvidence.subjectId,
          reply: outcome.reply,
        };
        await fireHook('onUserMessage', msg.subjectId, (p, ctx) => p.onUserMessage!(msg, ctx));
      }
      return outcome;
    },

    recordAssistantReply(input) {
      // 宿主（自建 agent 循环）把生成的 AI 回复报告给 core：push 进该会话的 InteractionSession 窗口，
      //   下一轮 ingestUserMessage 就能把它当「上一轮 AI 那句」捕获。**只作后续上下文、永不落证据**（铁律 3a）。
      //   该 conversationId 尚无 session（还没 ingest 过）→ 静默略过（下次 ingest 会建）。
      const session = sessions.get(input.conversationId);
      if (session) session.pushAssistant(input.content);
    },

    dropConversation(conversationId) {
      // 丢弃活跃实例 → 下次该 conversationId 会重建（systemPrompt/seedTurns 仅在建实例时生效，
      //   不丢就换不了人设、也重种不了续聊窗口）。只删内存里的实例，不碰库。
      conversations.delete(conversationId);
      sessions.delete(conversationId); // 交互会话上下文窗口一并丢（v0.6·D-0034）
    },

    async updateProfile(input = {}) {
      return runUpdateProfile(subjectOf(input.subjectId), {
        evidenceStore,
        eventStore,
        cognitionStore,
        semanticResolutionStore: stores.semanticResolutionStore, // v0.6 Phase 2·D-0034：落语义解析
        retriever,
        llm: pool.for('write'), // 写路径走小快模型（缺配自动回退 chat，见 llm/pool.ts）
        transaction,
        config: cfg,
        clock: options.clock, // 透传注入时钟（缺省=系统时间）：consolidate/attribute 的显式时间戳走它
      });
    },

    // 把本 core 的 retriever 传进 memory：resetSubject 清向量索引要它（indexAll([])）。
    memory: createMemoryManagementAPI(stores, cfg, { retriever, clock: options.clock }),

    portable: {
      exportBundle(opts = {}) {
        const { subjectId, ...rest } = opts;
        // exportedAt 走注入 clock（Phase 4）：显式 opts.now 优先（rest 在后覆盖）。opts.now 是 ISO 串。
        return exportBundle(
          subjectOf(subjectId),
          { evidenceStore, eventStore, cognitionStore, interactionContextStore: stores.interactionContextStore, semanticResolutionStore: stores.semanticResolutionStore },
          { now: (options.clock ?? systemClock)().toISOString(), ...rest },
        );
      },
      importBundle(bundle, opts) {
        return importBundle(
          bundle,
          { evidenceStore, eventStore, cognitionStore, interactionContextStore: stores.interactionContextStore, semanticResolutionStore: stores.semanticResolutionStore, transaction },
          opts,
        );
      },
      validateBundle,
    },

    graph: {
      buildMemoryGraph(opts = {}) {
        const { subjectId, ...rest } = opts;
        // generatedAt 走注入 clock（Phase 4）：显式 opts.now 优先（rest 在后覆盖）。opts.now 是 ISO 串。
        return buildMemoryGraph(subjectOf(subjectId), { evidenceStore, eventStore, cognitionStore }, { now: (options.clock ?? systemClock)().toISOString(), ...rest });
      },
    },

    health() {
      // 口径（与 testbench /api/health 对齐、基于 core 实际持有的部件判断，不重查 env）：
      //   llmReady = 持有真的对话模型客户端。env 装配时：缺 chat 配 → pool.for('chat') 是 failStub（非
      //     OpenAICompatClient）→ false；有配 → OpenAICompatClient → true，与 testbench loadLLMConfig() 一致。
      //     注入自定义 client（如测试 stub）判 false——stub 不是真能聊的模型，语义正确。
      //   embedReady = 持有【向量】召回器（专指"语义召回可用"）。有 EMBED 配置 → VectorRetriever → true;
      //     无 → KeywordRetriever（D-0017,关键词召回仍可用但非语义）或 NullRetriever → false。注入空召回器同判 false。
      //     ⚠ embedReady=false ≠ 召回恒空:D-0017 起,无 embedder 也有 keyword 召回;此位专表"语义/向量召回"。
      return {
        llmReady: pool.for('chat') instanceof OpenAICompatClient,
        embedReady: retriever instanceof VectorRetriever,
      };
    },

    usage() {
      // 累计总账（档8·观测/计费·「宿主能算钱」）：宿主要按对话/画像切分成本，自己在调用前后取 usage 差值即可
      //   （同现有 llmCalls 走 callCount 差值的路子）。库只给累计原料、不替宿主切分、不内置价目表。
      // llm：pool 的 chat/write 两用途 client 去重后累加——write 缺配回退 chat 时是同一实例，Set 去重防重复计。
      const clients = new Set<LLMClient>([pool.for('chat'), pool.for('write')]);
      let llm = ZERO_USAGE;
      for (const c of clients) llm = sumUsage(llm, c.usage);
      // embed：自建 embedder 时可读；注入 retriever 时拿不到（embedderRef=null），embed 归宿主自管、计 0。
      const embed = embedderRef?.usage ?? ZERO_USAGE;
      return { llm, embed, total: sumUsage(llm, embed) };
    },

    close() {
      stores.close();
      // 自建的向量/关键词召回器有独立连接要关；NullRetriever 没有 close，注入的归调用方。
      if (ownsRetriever && (retriever instanceof VectorRetriever || retriever instanceof KeywordRetriever)) retriever.close();
    },
  };
}
