/**
 * 写适配器：一轮对话结束后，把【用户这轮说的原话】沉淀成 MemoWeft 的 spoken 证据；
 * 另提供 persistToolResults 把【工具返回结果】沉淀成 tool 证据（见文件末段）。
 *
 * 为什么由调用方显式传用户原话，而不是从 onEnd 事件里解析：
 *   Vercel AI SDK 的 onEnd（generateText/streamText）事件字段全是【结果侧】
 *   （text/content/steps/usage/responseMessages/response…），SDK 不保证有原始用户输入。
 *   而发给 provider 的 request.body 已经被读适配器 middleware 注入过召回文本——
 *   若从那里反解析用户话，会把注入的记忆一起当成"用户原话"存回去（脏数据）。
 *   所以用户原话必须由调用方在发起 generateText 时就知道、显式交给本 helper（闭包捕获）。
 *   onEnd 只用来当"这一轮成功结束了、可以落库"的触发时机。
 *
 * Core 纪律（本适配器严格照做）：
 *   - 只存【用户原话】，不存助手回话（conversation.ts 纪律：助手回话不落证据）。
 *   - 走 core.ingestUserMessage（只落一条 spoken 证据、不改画像）。
 *   - 稳定 originId 保证幂等（同一轮即便 onEnd 被触发多次 / 重放，也只落一条）。
 */
import type { MemoWeftCore } from 'memoweft';
import type { ModelMessage } from 'ai';
import type { MemoWeftLogger } from './degrade.ts';

/** 只依赖 ingestUserMessage 一个方法——测试可传最小 stub。 */
type IngestOnly = Pick<MemoWeftCore, 'ingestUserMessage'>;

/** 只依赖 ingestToolResult 一个方法——测试可传最小 stub。 */
type ToolIngestOnly = Pick<MemoWeftCore, 'ingestToolResult'>;

export interface PersistUserTurnInput {
  /** 调用方在发起 generateText 前捕获的用户原话；该值不得从模型响应中派生。 */
  userMessage: string;
  /**
   * 稳定幂等键：同一轮对话给同一个 originId，重复触发 onEnd 也只落一条。
   * 建议宿主用自己的 turnId / messageId；不传则不去重（每次都落，见 Core originId 语义）。
   */
  originId?: string | null;
  subjectId?: string;
  hostId?: string;
  /** ISO 时间戳；缺省交给 Core（perceive 里取"现在"）。 */
  occurredAt?: string;
}

/**
 * 直接落一轮用户原话（薄封装 core.ingestUserMessage，字段名对齐 Core `UserMessageInput`）。
 * 不做任何"从响应里提取"的事——传进来什么原话就存什么。
 */
export async function persistUserTurn(
  core: IngestOnly,
  input: PersistUserTurnInput,
): Promise<void> {
  const text = input.userMessage;
  // 空串 / 全空白不落库（没有"用户原话"可存）。
  if (typeof text !== 'string' || text.trim() === '') return;
  await core.ingestUserMessage({
    content: text,
    originId: input.originId ?? null,
    subjectId: input.subjectId,
    hostId: input.hostId,
    occurredAt: input.occurredAt,
    // sourceKind 不传：Core 缺省 'spoken'（用户亲口）。
    // 不传任何授权位——ingestUserMessage 存 spoken，该入口不接受云读取授权覆盖。
  });
}

/** 造 onEnd 回调时的选项（用户原话由 factory 参数闭包捕获）。 */
export interface PersistOnEndOptions {
  /** 用户这轮说的原话（发起 generateText 时就持有的那份）。 */
  userMessage: string;
  /** 稳定幂等键（强烈建议给：同一轮的 turnId/messageId）。 */
  originId?: string | null;
  subjectId?: string;
  hostId?: string;
  occurredAt?: string;
  /** 落库出错时的回调（可选；不给则静默吞——落记忆失败不该崩主流程）。 */
  onError?: (err: unknown) => void;
  /**
   * 注入式 logger（可选，降级契约）：写路径一次重试后仍失败降级时记一条【结构化事件】
   *   （`{ event:'memory_degraded', op:'ingest', reason:'error' }`）。缺省不注入 = 静默降级。
   * 认知纪律 + 隐私：只记事件/原因，绝不记用户内容 / 原话 / 密钥。
   */
  logger?: MemoWeftLogger;
}

/**
 * 创建可直接传给 `generateText({ onEnd })` / `streamText({ onEnd })` 的回调。
 *
 * onEnd（`onFinish` 是它的 @deprecated 别名）事件对象【不被本回调使用】——
 *   用户原话来自闭包捕获的 opts.userMessage，事件仅作触发时机。
 * 回调返回 Promise：generateText 会 await onEnd（Callback 类型允许返回 PromiseLike），
 *   所以落库完成后才 resolve；出错走 onError、不向外抛（不崩宿主主流程）。
 *
 * 用法：
 *   const userMessage = '……用户这轮的话……';
 *   await generateText({
 *     model: wrapLanguageModel({ model, middleware: createMemoWeftMiddleware(core) }),
 *     prompt: userMessage,
 *     onEnd: createPersistOnEnd(core, { userMessage, originId: turnId }),
 *   });
 */
export function createPersistOnEnd(
  core: IngestOnly,
  opts: PersistOnEndOptions,
): (event?: unknown) => Promise<void> {
  // 形参 event 被【故意忽略】——用户原话来自闭包 opts.userMessage，事件仅作触发时机。
  //   该形参仅用于满足 SDK Callback = (event) => PromiseLike<void> 的接口，可直接传给 onEnd。
  const input: PersistUserTurnInput = {
    userMessage: opts.userMessage,
    originId: opts.originId,
    subjectId: opts.subjectId,
    hostId: opts.hostId,
    occurredAt: opts.occurredAt,
  };
  return async (_event?: unknown) => {
    try {
      await persistUserTurn(core, input);
    } catch {
      // 契约 ：写路径（ingest）失败重试一次再放弃（稳定 originId 保证重试幂等）。
      try {
        await persistUserTurn(core, input);
      } catch (err) {
        // 一次重试仍失败 → 降级：经注入 logger 记一条结构化事件（绝不记用户内容/原话），走 onError。
        opts.logger?.({ event: 'memory_degraded', op: 'ingest', reason: 'error' });
        if (opts.onError) opts.onError(err);
        // 无 onError 时静默吞：落记忆失败不该让宿主这轮对话失败。
      }
    }
  };
}

// ── 工具结果 → tool 证据 ────────────────────────────────────

/** 从一条 tool-result part 提出的可落库载荷。 */
interface ExtractedToolResult {
  toolCallId: string;
  text: string;
}

/**
 * 把 ToolResultOutput 转成可落库文本：'text' 取原文，'json' 序列化。
 * 其余（error-text / error-json / execution-denied / 媒体 content）返回 null 不落证据——
 *   它们是「这次工具没跑成」的运行元信息，不是工具返回的外部客观数据，存进记忆只会掺噪声。
 */
function toolOutputText(output: unknown): string | null {
  if (output == null || typeof output !== 'object') return null;
  const o = output as { type?: unknown; value?: unknown };
  if (o.type === 'text') return typeof o.value === 'string' ? o.value : null;
  if (o.type === 'json') {
    try {
      const s = JSON.stringify(o.value);
      // JSON.stringify(undefined | function | symbol) === undefined（非 string、非抛错）——
      //   必须收成 null（守住本函数 string|null 返回契约），否则下游 text.trim() 会抛 TypeError 逃逸出 persistToolResults。
      return typeof s === 'string' ? s : null;
    } catch {
      return null; // 不可序列化（循环引用等）→ 没有诚实的文本载荷可存
    }
  }
  return null;
}

/**
 * 从一轮消息里提出全部【工具返回结果】（tool-result-only ingestion 的机器化）：
 *   - 只读 role==='tool' 消息的 type==='tool-result' part——那是工具真实返回的外部数据；
 *   - assistant 消息【一概不读】：tool-call part（调用意图/入参）是助手输出，永不成为证据；
 *     provider 执行的工具结果混在 assistant content 里，也一并保守跳过（宁可漏、不可错摄）。
 * 入参按 unknown 防御解析：形状不合的消息/part 静默跳过，不抛。
 */
function extractToolResults(
  messages: ReadonlyArray<ModelMessage> | readonly unknown[],
): ExtractedToolResult[] {
  const out: ExtractedToolResult[] = [];
  for (const m of messages) {
    const msg = m as { role?: unknown; content?: unknown };
    if (msg == null || msg.role !== 'tool' || !Array.isArray(msg.content)) continue;
    for (const part of msg.content) {
      const p = part as { type?: unknown; toolCallId?: unknown; output?: unknown };
      if (p == null || p.type !== 'tool-result') continue; // tool-approval-response 等跳过
      const text = toolOutputText(p.output);
      // 双保险(defense-in-depth):toolOutputText 已守 string|null 契约;这里再挡一层非串,坐实「绝不向外抛」。
      if (typeof text !== 'string' || text.trim() === '') continue; // 空/无载荷不落库（同 persistUserTurn）
      out.push({ toolCallId: typeof p.toolCallId === 'string' ? p.toolCallId : '', text });
    }
  }
  return out;
}

export interface PersistToolResultsInput {
  /** 一轮结束后的消息数组（如 generateText 结果的 `response.messages`）。只读 role==='tool' 消息。 */
  messages: ReadonlyArray<ModelMessage>;
  /**
   * 幂等键前缀：每条结果 originId = `${originIdPrefix}:${toolCallId}`（强烈建议给宿主 turnId，
   * 重放/重试同一轮也只落一遍）。不传则不去重（每次都落，见 Core originId 语义）。
   */
  originIdPrefix?: string | null;
  subjectId?: string;
  hostId?: string;
  /** ISO 时间戳；缺省交给 Core（perceive 里取"现在"）。 */
  occurredAt?: string;
  /** 单条落库出错时的回调（可选；不给则静默吞——落记忆失败不该崩主流程）。 */
  onError?: (err: unknown) => void;
  /** 注入式 logger（降级契约）：单条一次重试后仍失败降级时记 `{ event:'memory_degraded', op:'ingest', reason:'error' }`。 */
  logger?: MemoWeftLogger;
}

/**
 * 把一轮对话里工具执行的【返回结果】沉淀成 tool 证据。
 *
 * 纪律与隐私（本适配器严格照做）：
 *   - tool-result-only ingestion：只摄入 role==='tool' 消息里的 tool-result 载荷（外部客观数据）；
 *     LLM 的工具调用意图/入参在 assistant 消息里，本函数根本不读 assistant 消息。
 *   - 落库走 core.ingestToolResult → sourceKind='tool' → 默认不进入内建云写模型 prompt（config.toolDefaults 兜底）。
 *   - 写路径契约：每条失败重试一次；仍失败经注入 logger 记录、调用 onError，绝不向外抛。
 *
 * @returns 成功落库（或幂等命中）的条数。
 */
export async function persistToolResults(
  core: ToolIngestOnly,
  input: PersistToolResultsInput,
): Promise<number> {
  const results = extractToolResults(input.messages);
  let stored = 0;
  for (const r of results) {
    const originId =
      input.originIdPrefix != null && r.toolCallId !== ''
        ? `${input.originIdPrefix}:${r.toolCallId}`
        : null;
    const ingest = () =>
      core.ingestToolResult({
        content: r.text,
        originId,
        subjectId: input.subjectId,
        hostId: input.hostId,
        occurredAt: input.occurredAt,
      });
    try {
      await ingest();
      stored++;
    } catch {
      // 契约 ：写路径失败重试一次再放弃（originId 保证重试幂等）。
      try {
        await ingest();
        stored++;
      } catch (err) {
        // 一次重试仍失败 → 降级：logger 记结构化事件（绝不记工具返回内容），走 onError，继续处理后面的条目。
        input.logger?.({ event: 'memory_degraded', op: 'ingest', reason: 'error' });
        if (input.onError) input.onError(err);
      }
    }
  }
  return stored;
}
