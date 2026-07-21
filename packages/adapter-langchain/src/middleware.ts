/**
 * @memoweft/adapter-langchain · LangChain v1 Agent Middleware 入口（读写一体）。
 *
 * 现有 retriever + callback 面（retriever.ts / writeCallback.ts）是 v0 时代载体（callbacks 仅观察、
 *   召回注入由宿主拼入 prompt）。LangChain v1 的 `createAgent` 推荐 Agent Middleware——本文件用
 *   `createMiddleware`（来自 `langchain` 包）创建统一集成 MemoWeft 长期记忆读写路径的 middleware：
 *
 *   - 读（`wrapModelCall`，每次模型调用前·临时注入不持久）：取本轮最后一条 human 文本 → core.recall →
 *       `handler({ ...request, systemMessage: request.systemMessage.concat(块) })`（官方推荐的临时扩 system 消息法）
 *       → 只对本次模型调用生效，【不写进会话 state】（避开逐轮累积旧召回）。
 *   - 写①（`beforeAgent`，一轮一次）：最后一条 human 原话 → ingestUserMessage(spoken)。
 *   - 写②（`wrapToolCall`，每次工具调用）：调 handler 拿 ToolMessage【返回结果】→ ingestToolResult；
 *       `request.toolCall`（LLM 的调用意图/入参）【绝不】读——tool-result-only ingestion boundary by-construction。
 *   - 写③（`afterAgent`，一轮一次）：最后一条 ai 回复 → recordAssistantReply（0.6 面·只进上下文窗口、
 *       永不落证据；能力探测，0.5 缺此面则跳过）。
 *
 * 0.6 会话上下文：写①用 conversationId ingest（此刻 session 里是【上一轮】AI 那句），写③再 record 本轮 AI 供下一轮捕获。
 *   conversationId 来源：opts.conversationId > opts.getConversationId(runtime) > runtime.configurable?.thread_id（LangGraph 线程）。
 *
 * 边界：注入文案沿用 Core 的中性 knowledgeBlock，不添加适配器专属角色指令。
 * 降级：recall 超时（默认 200ms）/抛错 → 不注入、经注入 logger 记一条；写路径失败重试一次再放弃（`runIngestWithRetry`）。
 * 隐私：provenance/id/score 绝不进注入的 system 消息——只经 onRecall 交宿主。
 *
 * peer 说明：本入口需 `langchain` 伞包（v1 middleware 所在）。它是【可选 peer】——只用 retriever/callback 集成的宿主
 *   仍只需 `@langchain/core`，import 本入口才需伞包。
 */
import { createMiddleware } from 'langchain';
import type { MemoWeftCore, ContentType, RecalledCognition } from 'memoweft';
import { buildKnowledgeBlock, type RecalledLike } from './knowledgeBlock.ts';
import { toolOutputText, runIngestWithRetry } from './writeCallback.ts';
import {
  DEFAULT_RECALL_TIMEOUT_MS,
  RecallTimeoutError,
  withTimeout,
  type MemoWeftLogger,
} from './degrade.ts';

/**
 * 适配器只用 Core 这几个方法。recordAssistantReply 是 0.6 面 → 可选（0.5 无此方法，运行时能力探测）。
 */
type MiddlewareCore = Pick<MemoWeftCore, 'recall' | 'ingestUserMessage' | 'ingestToolResult'> &
  Partial<Pick<MemoWeftCore, 'recordAssistantReply'>>;

export interface MemoWeftMiddlewareOptions {
  /** middleware 名（createMiddleware 必填 name；用于日志/标识）。缺省 'memoweft-memory'。 */
  name?: string;
  /** 召回/摄入归属的 subject；缺省交给 Core（config.identity.subjectId）。 */
  subjectId?: string;
  /** 注入块语言（措辞沿用 Core 双语口径）。缺省 'en'。只影响这段说明文字，不改 Core 行为。 */
  lang?: 'en' | 'zh';
  /** 召回按认知类型过滤：透传进 core.recall 的 contentTypes（允许名单）。不传/空 = 全类型。 */
  contentTypes?: ContentType[];
  /** 召回解释：透传进 core.recall 的 explain。true → onRecall 每项带 provenance（含授权位）。
   *  隐私保证：provenance【绝不】进注入的 system 消息——只经 onRecall 交宿主自筛。 */
  explain?: boolean;
  /** 每次成功召回后的回调（可选）：透传召回 v2 面（id/contentType/score，explain 时含 provenance）供宿主自筛/透视。 */
  onRecall?: (items: RecalledLike[]) => void;
  /** recall 超时阈值（毫秒）。缺省 200ms。超时/出错这次模型调用降级为不注入；读路径不重试。 */
  recallTimeoutMs?: number;
  /** ingest 单次尝试超时（毫秒，可选，同 writeCallback）。传正数则每次尝试套超时；超时按失败计入「重试一次」（超时不重试）。 */
  ingestTimeoutMs?: number;
  /** 注入式 logger：召回/摄入降级时记结构化事件。缺省无 = 静默；只记事件/原因，不记内容。 */
  logger?: MemoWeftLogger;
  /** 0.6 会话上下文的 conversationId（直传）。优先级最高。 */
  conversationId?: string;
  /** 从 runtime 派生 conversationId（次优先）。缺省读 runtime.configurable?.thread_id。 */
  getConversationId?: (runtime: unknown) => string | undefined;
}

// ── 消息读取 helper（BaseMessage 结构，只读、防御式；不 import 内部类型以抗版本漂移）──

/** BaseMessage.content → 纯文本（string 原样 / part 数组拼 text / 否则 null）。 */
function messageText(content: unknown): string | null {
  if (typeof content === 'string') return content.trim() === '' ? null : content;
  if (Array.isArray(content)) {
    const parts = content
      .map((p) => {
        const part = p as { type?: unknown; text?: unknown };
        return part?.type === 'text' && typeof part.text === 'string' ? part.text : null;
      })
      .filter((t): t is string => t !== null);
    return parts.length > 0 ? parts.join('\n') : null;
  }
  return null;
}

interface MsgView {
  content?: unknown;
  id?: unknown;
}

/** 从 messages 里取最后一条指定类型（'human' | 'ai'）的消息（用 getType()/_getType() 鸭子判别）。 */
function lastOfType(messages: unknown, type: 'human' | 'ai'): MsgView | undefined {
  if (!Array.isArray(messages)) return undefined;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] as { getType?: () => string; _getType?: () => string };
    const t =
      typeof m?.getType === 'function'
        ? m.getType()
        : typeof m?._getType === 'function'
          ? m._getType()
          : undefined;
    if (t === type) return m as MsgView;
  }
  return undefined;
}

/** 稳定 id → 幂等 originId（消息 id / 工具调用 id）；非串返回 null。 */
function stableId(v: unknown): string | null {
  return typeof v === 'string' && v !== '' ? v : null;
}

/**
 * 创建 MemoWeft × LangChain v1 的一组 middleware hook（纯函数，便于离线契约测试直接驱动）。
 * `createMemoWeftMiddleware` 用它 + createMiddleware 薄接线。
 */
export function buildMemoWeftHooks(core: MiddlewareCore, opts: MemoWeftMiddlewareOptions = {}) {
  const {
    subjectId,
    lang = 'en',
    contentTypes,
    explain,
    onRecall,
    recallTimeoutMs = DEFAULT_RECALL_TIMEOUT_MS,
    ingestTimeoutMs,
    logger,
    conversationId,
    getConversationId,
  } = opts;
  const canRecordReply = typeof core.recordAssistantReply === 'function';

  function convId(runtime: unknown): string | undefined {
    if (conversationId) return conversationId;
    if (getConversationId) return getConversationId(runtime);
    const r = runtime as
      { configurable?: { thread_id?: unknown }; context?: { thread_id?: unknown } } | undefined;
    const t = r?.configurable?.thread_id ?? r?.context?.thread_id;
    return typeof t === 'string' ? t : undefined;
  }

  /** 写①：最后一条 human 原话 → spoken（带 conversationId 时启用 0.6 上下文）。 */
  async function ingestUserTurn(state: { messages?: unknown }, runtime: unknown): Promise<void> {
    const human = lastOfType(state.messages, 'human');
    const text = human ? messageText(human.content) : null;
    if (!text) return;
    const cid = canRecordReply ? convId(runtime) : undefined;
    await runIngestWithRetry(
      () =>
        core.ingestUserMessage({
          content: text,
          originId: human ? stableId(human.id) : null,
          subjectId,
          conversationId: cid,
        }),
      ingestTimeoutMs,
      logger,
    );
  }

  /** 读：recall + 临时注入 system 消息（不持久）。返回给 wrapModelCall 用的「注入后 request」或原 request。 */
  async function recallInjectedRequest<
    T extends { messages?: unknown; systemMessage?: { concat(x: string): unknown } },
  >(request: T): Promise<T> {
    const human = lastOfType(request.messages, 'human');
    const query = human ? messageText(human.content) : null;
    if (!query) return request;
    let recalled: RecalledCognition[];
    try {
      recalled = await withTimeout(
        core.recall({ query, subjectId, contentTypes, explain }),
        recallTimeoutMs,
      );
    } catch (err) {
      logger?.({
        event: 'memory_degraded',
        op: 'recall',
        reason: err instanceof RecallTimeoutError ? 'timeout' : 'error',
      });
      return request;
    }
    onRecall?.(recalled as RecalledLike[]);
    const block = buildKnowledgeBlock(recalled as RecalledLike[], lang);
    if (block === '' || !request.systemMessage) return request;
    return { ...request, systemMessage: request.systemMessage.concat(block) };
  }

  /** 写②：工具返回结果 → tool 证据（只取 result content、绝不取 request.toolCall 的调用意图/入参，tool-result-only ingestion boundary）。 */
  async function persistToolResult(result: unknown, toolCall: unknown): Promise<void> {
    const text = toolOutputText((result as { content?: unknown } | null | undefined)?.content);
    if (text === null || text.trim() === '') return;
    const originId = stableId((toolCall as { id?: unknown } | undefined)?.id);
    await runIngestWithRetry(
      () => core.ingestToolResult({ content: text, originId, subjectId }),
      ingestTimeoutMs,
      logger,
    );
  }

  /** 写③：最后一条 ai 回复 → recordAssistantReply（0.6·只进上下文窗口、永不落证据）。 */
  function recordReply(state: { messages?: unknown }, runtime: unknown): void {
    if (!canRecordReply) return;
    const cid = convId(runtime);
    if (!cid) return;
    const ai = lastOfType(state.messages, 'ai');
    const text = ai ? messageText(ai.content) : null;
    if (text && text.trim() !== '') {
      try {
        core.recordAssistantReply!({ conversationId: cid, content: text });
      } catch {
        /* 上下文记录失败不崩对话（永不落证据，丢了只是下一轮少一句上文） */
      }
    }
  }

  // hook 参数用 any（SDK 边界）：LangChain v1 的 middleware hook 类型是重泛型（ModelRequest / WrapModelCallHandler /
  //   AgentBuiltInState …），且 wrapModelCall 的 handler-of-handler 逆变使结构化类型无法干净对齐（同 adapter 惯例：
  //   SDK 边界松、内部委托给已窄化的 typed helper）。每个 hook 只把 any 转手给上面结构化 typed 的私有函数，落库/注入
  //   逻辑仍全程类型安全。
  /* eslint-disable @typescript-eslint/no-explicit-any -- SDK 边界：LangChain v1 middleware hook 重泛型，内部已窄化 */
  return {
    /** 一轮一次：摄用户原话。返回 undefined = 不改 state。 */
    beforeAgent: async (state: any, runtime: any): Promise<undefined> => {
      await ingestUserTurn(state, runtime);
      return undefined;
    },
    /** 每次模型调用：临时注入召回进 system 消息（不持久），再调 handler 得 AIMessage。 */
    wrapModelCall: async (request: any, handler: (req: any) => any): Promise<any> => {
      const injected = await recallInjectedRequest(request);
      return handler(injected);
    },
    /** 每次工具调用：先执行拿 ToolMessage 结果，再落库（只 result 非 args）。 */
    wrapToolCall: async (request: any, handler: (req: any) => any): Promise<any> => {
      const result = await handler(request);
      await persistToolResult(result, request?.toolCall);
      return result;
    },
    /** 一轮一次：record 本轮 AI 回复供下一轮捕获。返回 undefined = 不改 state。 */
    afterAgent: async (state: any, runtime: any): Promise<undefined> => {
      recordReply(state, runtime);
      return undefined;
    },
  };
  /* eslint-enable @typescript-eslint/no-explicit-any */
}

/**
 * 创建 MemoWeft × LangChain v1 Agent Middleware，用于 `createAgent({ middleware: [mw] })`。
 * 它统一处理召回临时注入，以及用户原话、工具结果和 AI 回复的写入。
 *
 * @param core 需持有 recall / ingestUserMessage / ingestToolResult；recordAssistantReply 可选（0.6 面·能力探测）。
 * @param opts name / subjectId / lang / contentTypes / explain / onRecall / recallTimeoutMs / ingestTimeoutMs / logger / conversationId。
 * @returns `createMiddleware(...)` 的产物（AgentMiddleware），直接进 createAgent 的 middleware 数组。
 */
export function createMemoWeftMiddleware(
  core: MiddlewareCore,
  opts: MemoWeftMiddlewareOptions = {},
) {
  const hooks = buildMemoWeftHooks(core, opts);
  return createMiddleware({
    name: opts.name ?? 'memoweft-memory',
    beforeAgent: hooks.beforeAgent,
    wrapModelCall: hooks.wrapModelCall,
    wrapToolCall: hooks.wrapToolCall,
    afterAgent: hooks.afterAgent,
  });
}
