/**
 * @memoweft/adapter-mastra · Mastra Processor 适配器（读写两路一体）。
 *
 * 把 MemoWeft 的长期记忆接进 Mastra Agent 的 Processor 面（@mastra/core/processors）：
 *   - 读（processInput，模型调用【前】）：取本轮最后一条 user 文本 → core.recall → 注入进
 *       【system 通道】（返回 { messages, systemMessages }，绝不改 user 消息 → 用户原话零污染）。
 *   - 写（processOutputResult，模型答【完】后）：
 *       ① 用户原话 → spoken 证据（在 processInput 捕获【注入前】原文、经 processor state 递到这里再落库）；
 *       ② 工具返回结果 → tool 证据（只取 payload.result、绝不取 args/调用意图，tool-result-only ingestion boundary）；
 *       ③ AI 回复 → recordAssistantReply（0.6 面·只进上下文窗口、永不落证据；能力探测，0.5 缺此面则跳过）。
 *
 * 为什么用户原话在 processInput【捕获】、在 processOutputResult【落库】：
 *   - 捕获：processInput 的 messages 是【模型输入】，最后一条 user 即本轮原话；注入只落 system 通道
 *     → messages 保持原始输入，捕获到的原话不包含召回注入内容。
 *   - 落库放到模型答完之后：0.6 的 preceding_ai_context 语义要求「先有【上一轮】AI 那句在 session 里，
 *     再 ingest 本轮 user」。此刻 session 里正是上一轮 AI 回复；随后 recordAssistantReply(本轮 AI) 供下一轮捕获。
 *     捕获值通过 processor `state` 在同一 request 的不同阶段之间传递；若 state 未包含该值，
 *     processOutputResult 从本阶段 messages 读取备用值（注入不修改 messages，因此仍为原始用户文本）。
 *
 * 边界：注入文案沿用 Core 的中性 knowledgeBlock，不添加适配器专属角色指令。
 * 降级：recall 超时（默认 200ms）/抛错 → 不注入、经注入 logger 记一条；
 *   写路径失败重试一次再放弃、静默吞、绝不崩对话。
 */
import type { MemoWeftCore, ContentType, RecalledCognition } from 'memoweft';
import type { Processor, ProcessInputArgs, ProcessOutputResultArgs } from '@mastra/core/processors';
import { buildKnowledgeBlock, type RecalledLike } from './knowledgeBlock.ts';
import {
  DEFAULT_RECALL_TIMEOUT_MS,
  RecallTimeoutError,
  withTimeout,
  type MemoWeftLogger,
} from './degrade.ts';

/**
 * 适配器只用 Core 这几个方法（松耦合，测试可传最小 stub）。
 * recordAssistantReply 是 0.6 面 → 可选（0.5 无此方法，运行时能力探测决定用不用）。
 */
type ProcessorCore = Pick<MemoWeftCore, 'recall' | 'ingestUserMessage' | 'ingestToolResult'> &
  Partial<Pick<MemoWeftCore, 'recordAssistantReply'>>;

export interface MemoWeftProcessorOptions {
  /** Processor 的 id（Mastra 用它标识/排序 processor）。缺省 'memoweft-memory'。 */
  processorId?: string;
  /** 召回 / 摄入归属的 subject；缺省交给 Core（config.identity.subjectId）。 */
  subjectId?: string;
  /** 注入块语言（措辞沿用 Core 双语口径）。缺省 'en'。只影响这段说明文字，不改 Core 行为。 */
  lang?: 'en' | 'zh';
  /** 召回按认知类型过滤：透传进 core.recall 的 contentTypes（允许名单）。不传/空 = 全类型。 */
  contentTypes?: ContentType[];
  /** 召回解释：透传进 core.recall 的 explain。true → onRecall 每项带 provenance（含授权位）。
   *  隐私保证：provenance【绝不】进注入 prompt——只经 onRecall 交宿主自筛。 */
  explain?: boolean;
  /** 每次成功召回后的回调（可选）：透传召回 v2 面（id/contentType/score，explain 时含 provenance）供宿主自筛/透视。
   *  仅在 recall 成功返回后触发（空召回也以空数组触发）；无 user 文本 / recall 降级时不触发。 */
  onRecall?: (items: RecalledLike[]) => void;
  /** recall 超时阈值（毫秒）。缺省 200ms。超时即视为召回失败 → 降级为不注入。读路径不重试。 */
  recallTimeoutMs?: number;
  /** 注入式 logger（可选）：召回超时/抛错、写路径重试后仍失败 → 记一条结构化事件。缺省无 = 静默。
   *  只记事件/原因，绝不记用户内容 / 原话 / 密钥。 */
  logger?: MemoWeftLogger;
}

/** processInput 捕获、经 state 递给 processOutputResult 的一轮用户输入。 */
interface CapturedTurn {
  userText: string;
  originId: string | null;
  conversationId?: string;
}

/** processor state 里存捕获轮的键（`__memoweft__` 前后缀降低与宿主/其它 processor 撞键的概率）。 */
const STATE_KEY = '__memoweft__capturedTurn';

// ── 消息读取 helper（按 Mastra MastraDBMessage 结构自研；只读、防御式，不 import 内部类型以抗版本漂移）──

interface UserMsgView {
  text: string | null;
  id?: string;
  threadId?: string;
}

/** 从一条消息的 content 取纯文本：content 为 string 原样；为对象则拼 parts 里的 text part，回退其 content 串。 */
function extractText(content: unknown): string | null {
  if (typeof content === 'string') return content.trim() === '' ? null : content;
  if (content && typeof content === 'object') {
    const c = content as { parts?: unknown; content?: unknown };
    if (Array.isArray(c.parts)) {
      const texts = c.parts
        .filter((p): p is { type: 'text'; text: string } => {
          const part = p as { type?: string; text?: unknown };
          return part?.type === 'text' && typeof part.text === 'string';
        })
        .map((p) => p.text);
      if (texts.length > 0) return texts.join('\n');
    }
    if (typeof c.content === 'string' && c.content.trim() !== '') return c.content;
  }
  return null;
}

/** 取最后一条 role==='user' 消息的 { 文本, id(幂等键), threadId(会话标识) }。找不到返回 text:null。 */
export function lastUserMessage(messages: readonly unknown[]): UserMsgView {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] as { role?: string; id?: unknown; threadId?: unknown; content?: unknown };
    if (!m || m.role !== 'user') continue;
    return {
      text: extractText(m.content),
      id: typeof m.id === 'string' ? m.id : undefined,
      threadId: typeof m.threadId === 'string' ? m.threadId : undefined,
    };
  }
  return { text: null };
}

/** 工具返回结果 → 文本（string 原样，其它 JSON 序列化）。绝不碰 payload.args（LLM 调用入参，tool-result-only ingestion boundary）。 */
function toolResultText(result: unknown): string | null {
  if (typeof result === 'string') return result.trim() === '' ? null : result;
  if (result === undefined || result === null) return null;
  try {
    return JSON.stringify(result);
  } catch {
    return null;
  }
}

/** 写路径重试一次：首发失败重试一次，仍失败则经 logger 记一条 ingest 降级、静默吞（不崩对话）。 */
async function ingestWithRetry(fn: () => Promise<unknown>, logger?: MemoWeftLogger): Promise<void> {
  try {
    await fn();
    return;
  } catch {
    /* 重试一次 */
  }
  try {
    await fn();
  } catch {
    logger?.({ event: 'memory_degraded', op: 'ingest', reason: 'error' });
  }
}

/**
 * 创建集成 MemoWeft 读写路径的 Mastra Processor。
 * 同一实例同时注册进 Agent 的 `inputProcessors`（跑 processInput·读）与 `outputProcessors`（跑 processOutputResult·写）。
 *
 * @param core 需持有 recall / ingestUserMessage / ingestToolResult；recordAssistantReply 可选（0.6 面，能力探测）。
 * @param opts processorId / subjectId / lang / contentTypes / explain / onRecall / recallTimeoutMs / logger。
 */
export function createMemoWeftProcessor(
  core: ProcessorCore,
  opts: MemoWeftProcessorOptions = {},
): Processor {
  const {
    processorId = 'memoweft-memory',
    subjectId,
    lang = 'en',
    contentTypes,
    explain,
    onRecall,
    recallTimeoutMs = DEFAULT_RECALL_TIMEOUT_MS,
    logger,
  } = opts;
  // 能力探测（peer ^0.5 || ^0.6）：recordAssistantReply 是 0.6 面；0.5 无此方法 → 不启用会话上下文线。
  const canRecordReply = typeof core.recordAssistantReply === 'function';

  return {
    id: processorId,

    // 读：模型调用前召回 + 注入 system 通道；顺带捕获【注入前】用户原话经 state 递给写路径。
    async processInput(args: ProcessInputArgs) {
      const u = lastUserMessage(args.messages);
      if (u.text) {
        const captured: CapturedTurn = {
          userText: u.text,
          originId: u.id ?? null,
          conversationId: canRecordReply ? u.threadId : undefined,
        };
        args.state[STATE_KEY] = captured;
      }
      // 无 user 文本（纯多模态 / 无 user 消息）→ 不召回、原样透传。
      if (!u.text) return args.messages;

      let recalled: RecalledCognition[];
      try {
        // ：Promise.race 包 recallTimeoutMs（默认 200ms）超时；读路径不重试。
        // 召回 v2 透传：contentTypes / explain 原样交给 Core（过滤/解释都在 Core 侧做）。
        recalled = await withTimeout(
          core.recall({ query: u.text, subjectId, contentTypes, explain }),
          recallTimeoutMs,
        );
      } catch (err) {
        // 召回失败/超时不挡回话：降级为不注入 + 记一条结构化事件（缺省无 logger = 静默）。
        logger?.({
          event: 'memory_degraded',
          op: 'recall',
          reason: err instanceof RecallTimeoutError ? 'timeout' : 'error',
        });
        return args.messages;
      }
      onRecall?.(recalled as RecalledLike[]);
      const block = buildKnowledgeBlock(recalled as RecalledLike[], lang);
      if (block === '') return args.messages;

      // 注入进 system 通道（绝不碰 user 消息）：追加一条 system 消息，去掉块首前导空行。
      type SysMsg = ProcessInputArgs['systemMessages'][number];
      const sysMsg = { role: 'system', content: block.replace(/^\n+/, '') } as SysMsg;
      return { messages: args.messages, systemMessages: [...args.systemMessages, sysMsg] };
    },

    // 写：模型答完后落库——用户原话(spoken) → AI 回复(上下文) → 工具结果(tool)。绝不改输出。
    async processOutputResult(args: ProcessOutputResultArgs) {
      // 优先使用 processInput 写入 state 的捕获值；缺失时从未被注入修改的 messages 读取备用值。
      let captured = args.state[STATE_KEY] as CapturedTurn | undefined;
      if (!captured) {
        const u = lastUserMessage(args.messages);
        if (u.text)
          captured = {
            userText: u.text,
            originId: u.id ?? null,
            conversationId: canRecordReply ? u.threadId : undefined,
          };
      }

      // ① 用户原话 → spoken（带 conversationId 时启用 0.6 会话上下文；此刻 session 里是【上一轮】AI 那句）。
      if (captured) {
        await ingestWithRetry(
          () =>
            core.ingestUserMessage({
              content: captured!.userText,
              originId: captured!.originId,
              subjectId,
              conversationId: captured!.conversationId,
            }),
          logger,
        );
      }

      // ② AI 回复 → recordAssistantReply（0.6 面·只进上下文窗口、永不落证据；供下一轮捕获）。
      if (canRecordReply && captured?.conversationId) {
        const text = args.result?.text;
        if (typeof text === 'string' && text.trim() !== '') {
          try {
            core.recordAssistantReply!({ conversationId: captured.conversationId, content: text });
          } catch {
            /* 上下文记录失败不崩对话（永不落证据，丢了只是下一轮少一句上文） */
          }
        }
      }

      // ③ 工具返回结果 → tool 证据（只取 payload.result / toolCallId，绝不取 payload.args/toolName，tool-result-only ingestion boundary）。
      const steps = args.result?.steps ?? [];
      for (const step of steps) {
        const toolResults = (step as { toolResults?: unknown }).toolResults;
        if (!Array.isArray(toolResults)) continue;
        for (const tr of toolResults) {
          const payload = (tr as { payload?: unknown }).payload as
            { toolCallId?: unknown; result?: unknown } | undefined;
          if (!payload) continue;
          const text = toolResultText(payload.result);
          if (text === null) continue;
          const originId = typeof payload.toolCallId === 'string' ? payload.toolCallId : null;
          await ingestWithRetry(
            () => core.ingestToolResult({ content: text, originId, subjectId }),
            logger,
          );
        }
      }

      // 适配器只观测/落库，绝不改输出：原样返回。
      return args.messages;
    },
  };
}
