/**
 * 写路径适配器：`persistFromAgentStream(core, stream, extras)`——透传式 async generator，包住
 *   `agent.runStream(userMsg)` 返回的事件流，【原样 re-yield 全部事件】、顺路把该沉淀的证据摄入。
 *
 * 三条写路径里的 ②③（① 召回注入走 memoryBlock.ts）：
 *   ② 用户原话 = 由宿主在【注入前】持有的原话，经 extras.userMessage 显式传入 → ingestUserMessage(spoken)；
 *   ③ 工具结果 = 扫流里的事件，【只认】`agentToolCallResultEvent`（工具真实【返回结果】）→ ingestToolResult。
 *
 * tool-result-only ingestion boundary（代码级 by-construction·物理隔离）：③ 只用 `agentToolCallResultEvent.include(ev)` 判别【结果事件】，
 *   取 `ev.data.toolOutput.result`（实测 `AgentToolCallResult.toolOutput: ToolResult{ id,result,isError }`）。
 *   `agentToolCallEvent`（LLM 的【调用意图 / 入参 toolKwargs】）是【另一个事件类型】——本适配器【绝不】匹配它，
 *   它仅被原样 re-yield，事件判别器从类型层面将其排除在写路径之外。
 *
 * 用户原话必须由宿主在注入前捕获并显式传入。模型输入已包含 memoryBlock 注入的记忆，
 *   因此从流事件派生用户原话会造成召回内容被错误地重新存储为用户证据。
 *
 * 降级：摄入走 `runIngestWithRetry`（真错重试一次、超时不重试）；【绝不向 stream 抛 / 中断】——
 *   re-yield 完全不受摄入成败影响，摄入 promise 收集到末尾统一 settle（runIngestWithRetry 从不 reject）。
 *
 * 写路径边界：只落工具【返回结果】文本（不落调用意图）→ `core.ingestToolResult` → sourceKind='tool' → 默认不进入内建云写模型 prompt；
 *   用户原话 → `core.ingestUserMessage` → spoken（不传云读取授权覆盖）。
 *
 * 类型/值 import 自 `@llamaindex/workflow`（peer + dev 依赖）：`agentToolCallResultEvent` 是运行时值（判别器），
 *   故为值 import；`WorkflowEventData` 仅签名用，用 `import type`（该包 `export *` 了 `@llamaindex/workflow-core`）。
 */
import { agentToolCallResultEvent } from '@llamaindex/workflow';
import type { WorkflowEventData } from '@llamaindex/workflow';
import type { MemoWeftCore } from 'memoweft';
import { RecallTimeoutError, withTimeout, type MemoWeftLogger } from './degrade.ts';

/** 只依赖 ingestUserMessage + ingestToolResult 两方法——测试可传最小 stub。 */
type StreamTapCore = Pick<MemoWeftCore, 'ingestUserMessage' | 'ingestToolResult'>;

/** 只依赖 ingestUserMessage 一个方法——`persistUserTurn` 的最小 Core 面。 */
type UserIngestOnly = Pick<MemoWeftCore, 'ingestUserMessage'>;

/**
 * ingest（写路径）执行器：契约 ——【真错】重试一次再放弃；仍失败经 logger 记一条结构化事件后【静默吞】。
 * 绝不向 stream / 调用方抛（本函数从不 reject）。ingest 的降级 reason 恒为 'error'（含超时也归 error）。
 * 幂等前提：重试去重靠【稳定非空 originId】（spoken 用宿主 turnId、tool 用 toolId）。originId=null 时 Core 不去重，
 *   故【超时不重试】：withTimeout 只 race、不取消底层写，超时后底层 ingest 可能仍提交，盲重试会重复落库。
 *
 * （与 adapter-langchain writeCallback.ts / adapter-openai-agents runner.ts 的同名私有件逐字对齐，保持包间行为一致。）
 */
async function runIngestWithRetry(
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
 * 从 `agentToolCallResultEvent` 的 `data.toolOutput.result` 规整出可落库文本：
 *   - string 原样用（实测 `ToolResult.result: string`，正常即此路）；
 *   - 兜底：非 string（形状漂移）→ JSON 序列化；序列化失败/产出非串 → null（守住 string|null 契约，绝不向外抛）。
 * 注意：本函数【只】接收工具【返回结果】(toolOutput.result)，从不接收调用意图/入参（toolKwargs 在另一事件里）。
 */
export function toolOutputText(result: unknown): string | null {
  if (result == null) return null;
  if (typeof result === 'string') return result;
  try {
    const s = JSON.stringify(result);
    return typeof s === 'string' ? s : null;
  } catch {
    return null;
  }
}

/** `persistFromAgentStream` 的每轮附加配置。 */
export interface PersistFromAgentStreamExtras {
  /**
   * 宿主在发起 `runStream` 前捕获的用户原话；该值不得从流事件中派生。
   * 空串 / 全空白跳过（不落 spoken）。
   */
  userMessage: string;
  /**
   * 这轮【用户原话】(spoken) 摄入的稳定幂等键。同一轮重放/重试只落一条。
   * 建议宿主用自己的 turnId / messageId；不传则不去重（每次都落，见 Core originId 语义）。
   */
  originId?: string | null;
  /** 召回/摄入归属的 subject；缺省交给 Core（config.identity.subjectId）。 */
  subjectId?: string;
  /**
   * ingest 单次尝试超时（毫秒，可选）。不传/≤0 = 不套超时（只靠失败重试一次兜底）；
   * 传正数则每次尝试套 withTimeout，超时按失败计入「重试一次」（超时不重试，见 runIngestWithRetry）。
   */
  ingestTimeoutMs?: number;
  /** 注入式 logger（降级契约）：一次重试后仍失败降级时记结构化事件。缺省不注入 = 静默降级。 */
  logger?: MemoWeftLogger;
}

/**
 * 透传式摄入包装器：包住 `agent.runStream(userMsg)` 的事件流，原样 re-yield 每个事件、顺路摄入 ②③。
 *
 * 流程：
 *   1. 先摄【用户原话】(② spoken；空白跳过)——在迭代流之前，呼应「注入前持有的原话」；
 *   2. `for await` 遍历上游流：每个事件【先原样 yield】(消费者不受摄入影响)，再判别——
 *        · `agentToolCallResultEvent.include(ev)` → 取 `ev.data.toolOutput.result` → ingestToolResult(③，originId=toolId)；
 *        · 其余事件（含 `agentToolCallEvent` 调用意图）→ 只 re-yield、【不摄】(tool-result-only ingestion boundary)。
 *   3. 摄入 promise 全程收集、末尾统一 settle——不阻塞 re-yield（runIngestWithRetry 从不 reject，绝不中断 stream）。
 *
 * @param core 只需持有 ingestUserMessage / ingestToolResult 两方法的 Core（或其最小实现）。
 * @param stream `agent.runStream(userMsg)` 返回的事件流（任何 `AsyncIterable<WorkflowEventData>` 皆可，便于测试传入构造流）。
 * @param extras userMessage（必填·注入前原话）+ originId / subjectId / ingestTimeoutMs / logger。
 * @returns async generator：逐个 re-yield 上游事件（`T` 原封不动透传，宿主拿去正常消费）。
 */
export async function* persistFromAgentStream<T extends WorkflowEventData<unknown>>(
  core: StreamTapCore,
  stream: AsyncIterable<T>,
  extras: PersistFromAgentStreamExtras,
): AsyncGenerator<T, void, unknown> {
  const { userMessage, originId, subjectId, ingestTimeoutMs, logger } = extras;

  // 摄入 promise 收集处：runIngestWithRetry 从不 reject，收集起来末尾统一 await，不阻塞 re-yield。
  const pending: Promise<void>[] = [];

  // ② 用户原话（spoken）：注入前持有的原话，空白跳过，不传云读取授权覆盖。
  if (typeof userMessage === 'string' && userMessage.trim() !== '') {
    pending.push(
      runIngestWithRetry(
        () =>
          core.ingestUserMessage({ content: userMessage, originId: originId ?? null, subjectId }),
        ingestTimeoutMs,
        logger,
      ),
    );
  }

  try {
    for await (const event of stream) {
      // 【先原样 re-yield】——消费者第一时间获取事件，完全不受下面摄入成败影响（透传纪律 +  不中断）。
      yield event;

      // ③ 工具结果：只认 agentToolCallResultEvent（结果事件）。tool-result-only ingestion boundary：agentToolCallEvent（调用意图）不是本类型 → 不匹配、不摄。
      if (agentToolCallResultEvent.include(event)) {
        const text = toolOutputText(event.data.toolOutput?.result);
        if (text !== null && text.trim() !== '') {
          // originId 用 toolId（该工具调用的稳定 id）保证幂等；缺失/非串 → null（不去重）。
          const toolId = event.data.toolId;
          const toolOriginId = typeof toolId === 'string' && toolId !== '' ? toolId : null;
          pending.push(
            runIngestWithRetry(
              () => core.ingestToolResult({ content: text, originId: toolOriginId, subjectId }),
              ingestTimeoutMs,
              logger,
            ),
          );
        }
      }
    }
  } finally {
    // 无论正常收尾还是消费者提前 break（generator return）→ 都等所有摄入 settle 后再结束。
    // runIngestWithRetry 从不 reject，allSettled 只为再兜一层（绝不因摄入让本 generator 抛）。
    await Promise.allSettled(pending);
  }
}

export interface PersistUserTurnInput {
  /** 宿主在发起 runStream 前捕获的用户原话；该值不得从流事件中派生。 */
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
  /** ingest 单次尝试超时（毫秒，可选，同 PersistFromAgentStreamExtras.ingestTimeoutMs）。 */
  ingestTimeoutMs?: number;
  /** 注入式 logger（降级契约）：一次重试后仍失败降级时记结构化事件。 */
  logger?: MemoWeftLogger;
}

/**
 * 宿主闭包摄入【用户原话】→ spoken 证据（薄封装 `core.ingestUserMessage`，走 `runIngestWithRetry` 降级）。
 *
 * 何时用：宿主不走 `persistFromAgentStream`（例如自己驱动 `runStream` / 只想单独落原话）时，用它把用户原话落 spoken。
 *   走 `persistFromAgentStream` 的宿主则由其内部摄原话（extras.userMessage），无须再调本函数。
 *
 * 为什么由宿主显式传原话、不从流解析：发给模型的输入已被召回注入过（memoryBlock 的 'memory' 消息）——
 *   从流事件派生该值会将注入的记忆错误地重新存储为用户证据，因此必须由宿主在注入前显式传入。
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
