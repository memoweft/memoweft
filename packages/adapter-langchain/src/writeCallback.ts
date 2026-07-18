/**
 * 写路径适配器：`MemoWeftWriteCallback extends BaseCallbackHandler`——把【工具返回结果】沉淀成 tool 证据；
 * 另导出宿主闭包 `persistUserTurn`——把【用户原话】沉淀成 spoken 证据。
 *
 * tool-result-only ingestion boundary（代码级 by-construction·物理隔离）：本 handler【只】实现 `handleToolEnd`（output = 工具真实
 *   【返回结果】），【绝不】声明 `handleToolStart`——LangChain 的 CallbackManager 是
 *   `if (handler.handleToolStart) …` 才投递，本类无此方法 → 工具的【调用意图 / 入参 string】永不到达本适配器。
 *   不是"实现了但不落库"，是"根本没有这个方法" → 调用意图物理上进不来。
 *
 * 框架行为（仅观察）：LangChain callbacks 丢弃 handler 返回值，故 callback【不能】用于注入（注入走 retriever.ts）；
 *   本 handler 是纯写路径观察者，读取 output 落库、不回吐任何东西给链。
 *
 * originId 承载字段（实测 `@langchain/core` .d.ts 核对）：
 *   `handleToolEnd(output: any, runId: string, parentRunId?, tags?)`——【没有】 tool_call_id 形参，
 *   故 tool 证据的幂等 originId 用 `runId`（该工具调用这一 run 的稳定 id）。
 *
 * 降级：写路径 `runIngestWithRetry`——真错重试一次、超时不重试；仍失败经 logger 记事件后静默吞，绝不向链抛。
 *
 * 写路径边界：只落工具【返回结果】文本（不落调用意图），走 `core.ingestToolResult` → sourceKind='tool' → 默认不进入内建云写模型 prompt。
 */
import { BaseCallbackHandler } from '@langchain/core/callbacks/base';
import type { MemoWeftCore } from 'memoweft';
import { RecallTimeoutError, withTimeout, type MemoWeftLogger } from './degrade.ts';

/** 只依赖 ingestToolResult 一个方法——测试可传最小 stub。 */
type ToolIngestOnly = Pick<MemoWeftCore, 'ingestToolResult'>;

/** 只依赖 ingestUserMessage 一个方法——测试可传最小 stub。 */
type UserIngestOnly = Pick<MemoWeftCore, 'ingestUserMessage'>;

/**
 * ingest（写路径）执行器：契约 ——【真错】重试一次再放弃；仍失败经 logger 记一条结构化事件后【静默吞】。
 * 绝不向链 / 调用方抛（本函数从不 reject）。ingest 的降级 reason 恒为 'error'（含超时也归 error）。
 * 幂等前提：重试去重靠【稳定非空 originId】（tool 用 runId、spoken 用宿主 turnId）。originId=null 时 Core 不去重，
 *   故【超时不重试】：withTimeout 只 race、不取消底层写，超时后底层 ingest 可能仍提交，盲重试会重复落库。
 *
 * （与 adapter-openai-agents runner.ts 的同名私有件逐字对齐，保持包间行为一致。）
 */
export async function runIngestWithRetry(
  fn: () => Promise<unknown>,
  ingestTimeoutMs: number | undefined,
  logger?: MemoWeftLogger,
): Promise<void> {
  const attempt = () =>
    ingestTimeoutMs != null && ingestTimeoutMs > 0 ? withTimeout(fn(), ingestTimeoutMs) : fn();
  try {
    await attempt();
  } catch (err) {
    // 超时（withTimeout 拒绝）→ 底层写可能已提交，不重试；真错 → 写确未提交，重试一次。
    if (err instanceof RecallTimeoutError) {
      logger?.({ event: 'memory_degraded', op: 'ingest', reason: 'error' });
      return;
    }
    try {
      await attempt();
    } catch {
      // 一次重试仍失败 → 降级：记结构化事件（绝不记内容），静默吞——落记忆失败不该让这轮对话失败。
      logger?.({ event: 'memory_degraded', op: 'ingest', reason: 'error' });
    }
  }
}

/**
 * 从工具返回结果（`handleToolEnd` 的 `output`，实测类型 `any`）规整出可落库文本：
 *   - string 直接用；
 *   - BaseMessage / ToolMessage 形（有 `content` 键）：content 为 string 用原文，为 part 数组拼其 text 部分，
 *     content 非文本（如媒体 part）→ null（无诚实文本载荷，不落库）；
 *   - 普通结构化对象 / 数组（如 LangGraph 工具直接返回 obj）→ JSON 序列化；
 *   - 其它 primitive（number/boolean）→ JSON 序列化。
 * 序列化失败（循环引用）或产出非串 → 返回 null（守住 string|null 契约，绝不向外抛）。
 * 注意：本函数【只】接收工具的【返回结果】，从不接收调用意图/入参（handleToolStart 根本没实现）。
 */
export function toolOutputText(output: unknown): string | null {
  if (output == null) return null;
  if (typeof output === 'string') return output;
  if (typeof output === 'object') {
    const o = output as { content?: unknown; _getType?: unknown };
    // 只有【真·BaseMessage / ToolMessage】(有 `_getType` 方法)才走取 content 分支——只取文本 content
    //   (跳过 role / tool_call_id 等元信息)。判据用 `_getType` 鸭子类型而非 `'content' in o`:后者会把
    //   【恰好带 content 键的普通对象】(如工具直接 invoke 返回 { content:{...}, source })误判为 message →
    //   content 非串时丢整个对象、content 为串时丢兄弟字段。用 _getType 判据后,这类普通对象落到下面整体
    //   JSON 序列化,不丢数据(与本函数「普通结构化对象/数组 → JSON 序列化」承诺一致)。
    if (typeof o._getType === 'function') {
      if (typeof o.content === 'string') return o.content;
      if (Array.isArray(o.content)) {
        const parts = o.content
          .map((p) => {
            const part = p as { type?: unknown; text?: unknown };
            return part?.type === 'text' && typeof part.text === 'string' ? part.text : null;
          })
          .filter((t): t is string => t !== null);
        return parts.length > 0 ? parts.join('\n') : null;
      }
      return null; // BaseMessage 的 content 非串非文本数组(纯媒体 part)→ 无诚实文本载荷
    }
    // 普通结构化对象 / 数组（含恰好带 content 键者）→ 整体 JSON 序列化，不丢数据。
    try {
      const s = JSON.stringify(output);
      return typeof s === 'string' ? s : null;
    } catch {
      return null; // 循环引用等
    }
  }
  // number / boolean 等 primitive → 字符串化
  try {
    const s = JSON.stringify(output);
    return typeof s === 'string' ? s : null;
  } catch {
    return null;
  }
}

export interface MemoWeftWriteCallbackOptions {
  /** tool 证据归属的 subject；缺省交给 Core（config.identity.subjectId）。 */
  subjectId?: string;
  /**
   * ingest（写路径）单次尝试的超时阈值（毫秒，可选）。不传/≤0 = 不套超时（只靠失败重试一次兜底）；
   * 传正数则每次尝试套 withTimeout，超时按失败计入「重试一次」（超时不重试，见 runIngestWithRetry）。
   */
  ingestTimeoutMs?: number;
  /**
   * 注入式 logger（可选，降级契约）：ingest 一次重试后仍失败降级时记一条【结构化事件】
   *   （`{ event:'memory_degraded', op:'ingest', reason:'error' }`）。缺省不注入 = 静默降级。
   * 认知纪律 + 隐私：只记事件/原因，绝不记工具返回内容 / 密钥。
   */
  logger?: MemoWeftLogger;
}

/**
 * MemoWeft 写回调：注册到 LangChain 的 `callbacks`（Runnable/链的 config.callbacks），在工具完成后存储 tool 证据。
 *
 * 必填抽象成员（实测 `@langchain/core` .d.ts 核对）：`abstract name: string`——须给（`lc_namespace` 基类已实现）。
 *
 * tool-result-only ingestion boundary：本类【只】有 `handleToolEnd`，【没有】 `handleToolStart`——调用意图物理上进不来（见文件头）。
 */
export class MemoWeftWriteCallback extends BaseCallbackHandler {
  /** `BaseCallbackHandler` 抽象成员：handler 名（序列化 / 日志用）。 */
  name = 'MemoWeftWriteCallback';

  private readonly core: ToolIngestOnly;
  private readonly opts: MemoWeftWriteCallbackOptions;

  /**
   * @param core 只需持有 `ingestToolResult` 方法的 Core（或其最小实现）。
   * @param opts subjectId / ingestTimeoutMs / logger。
   */
  constructor(core: ToolIngestOnly, opts: MemoWeftWriteCallbackOptions = {}) {
    super();
    this.core = core;
    this.opts = opts;
  }

  /**
   * ③ 工具结果摄入：`output` = 工具真实【返回结果】→ ingestToolResult（tool 证据，默认不进入内建云写模型 prompt）。
   * originId 用 `runId`（该工具调用 run 的稳定 id）保证幂等。空/无文本载荷不落库。
   * 降级不中断：`runIngestWithRetry` 内部从不抛——落记忆失败不该掀翻链。
   *
   * 【绝不】声明 `handleToolStart`：CallbackManager 无此方法便不投递调用意图（tool-result-only ingestion boundary·物理隔离）。
   */
  async handleToolEnd(output: unknown, runId: string): Promise<void> {
    const text = toolOutputText(output);
    if (text === null || text.trim() === '') return; // 空/无载荷不落库
    const originId = typeof runId === 'string' && runId !== '' ? runId : null;
    await runIngestWithRetry(
      () => this.core.ingestToolResult({ content: text, originId, subjectId: this.opts.subjectId }),
      this.opts.ingestTimeoutMs,
      this.opts.logger,
    );
  }
}

export interface PersistUserTurnInput {
  /** 宿主在发起链调用前捕获的用户原话；该值不得从模型响应中派生。 */
  text: string;
  /**
   * 稳定幂等键：同一轮对话给同一个 originId，重复摄入也只落一条。
   * 建议宿主用自己的 turnId / messageId；不传则不去重（每次都落，见 Core originId 语义）。
   */
  originId?: string | null;
  subjectId?: string;
  hostId?: string;
  /** ISO 时间戳；缺省交给 Core（perceive 里取"现在"）。 */
  occurredAt?: string;
  /** ingest 单次尝试超时（毫秒，可选，同 MemoWeftWriteCallbackOptions.ingestTimeoutMs）。 */
  ingestTimeoutMs?: number;
  /** 注入式 logger（降级契约）：一次重试后仍失败降级时记结构化事件。 */
  logger?: MemoWeftLogger;
}

/**
 * 宿主闭包摄入【用户原话】→ spoken 证据（薄封装 `core.ingestUserMessage`，走 `runIngestWithRetry` 降级）。
 *
 * 为什么由宿主显式传原话、不从事件解析：LangChain 的 callbacks 是仅观察，且发给模型的输入已被召回
 *   注入过；从事件载荷派生该值会将召回内容错误地重新存储为用户证据。原话必须由宿主在注入前显式传入。
 *
 * Core 约束：只存用户原话（spoken），不存助手回复，也不传云读取授权覆盖。
 * 空串 / 全空白不落库。降级不中断：失败经一次重试仍失败 → logger 记事件、静默吞，绝不向宿主抛。
 */
export async function persistUserTurn(
  core: UserIngestOnly,
  input: PersistUserTurnInput,
): Promise<void> {
  const text = input.text;
  if (typeof text !== 'string' || text.trim() === '') return;
  await runIngestWithRetry(
    () =>
      core.ingestUserMessage({
        content: text,
        originId: input.originId ?? null,
        subjectId: input.subjectId,
        hostId: input.hostId,
        occurredAt: input.occurredAt,
        // sourceKind 不传：Core 默认使用 spoken；不传云读取授权覆盖。
      }),
    input.ingestTimeoutMs,
    input.logger,
  );
}
