/**
 * run-wrapper 型读写适配器：把 MemoWeft 的长期记忆接进 OpenAI Agents SDK（`@openai/agents`）。
 *
 * 范式：`const mw = createMemoWeftRunner(core, opts); await mw.run(agent, input, options)`。
 * 一个工厂造三件套，覆盖读写三条路径：
 *   ① 召回注入（读）= `callModelInputFilter`（模型调用前编辑 instructions）——插进 run 的
 *      `callModelInputFilter` 选项 / `RunConfig` / `Runner` 配置皆可；
 *   ② 用户原话摄入（写）= `run` 包装器闭包捕获【未注入的原始 input】→ ingestUserMessage(spoken)；
 *   ③ 工具结果摄入（写）= `run` 结束后扫 `RunResult.newItems`，筛 `tool_call_output_item` → ingestToolResult。
 *
 * 边界（照 MemoWeft「Core 无头」纪律）：注入文案只搬 Core `action.ts` 的中性措辞（见 knowledgeBlock.ts），
 *   适配器里不自造人格/人设 prompt。
 *
 * 隐私硬约束（D-0024·不可违反）：provenance / contentType / score / id【绝不】进注入的 instructions 文本，
 *   只经 onRecall 交宿主自筛；buildKnowledgeBlock 只用 content/confidence/credStatus。
 *
 * 铁律 3a（代码级 by-construction）：③ 只筛 `type==='tool_call_output_item'`（工具真实【返回结果】）并只读其
 *   `output`/`rawItem.callId`；`tool_call_item`（LLM 的调用意图/入参）是独立 item 类型，从不进入本适配器作用域。
 *
 * 类型：全部从 `@openai/agents` 用 `import type` 引（peer + dev 依赖）。运行时不启真实 SDK——
 *   `run` 包装器用【动态 import】按需加载 SDK，故只测 filter/摄入函数时（喂构造的 ModelInputData / RunItem）
 *   根本不加载 `@openai/agents`。
 */
import type {
  Agent,
  AgentInputItem,
  CallModelInputFilter,
  CallModelInputFilterArgs,
  ModelInputData,
  NonStreamRunOptions,
  RunItem,
  RunResult,
} from '@openai/agents';
import type { MemoWeftCore, RecalledCognition, ContentType } from 'memoweft';
import {
  DEFAULT_RECALL_TIMEOUT_MS,
  RecallTimeoutError,
  withTimeout,
  type MemoWeftLogger,
} from './degrade.ts';
import { buildKnowledgeBlock, type RecalledLike } from './knowledgeBlock.ts';

/** 只依赖读写三方法——测试可传最小 stub。 */
type RunnerCore = Pick<MemoWeftCore, 'recall' | 'ingestUserMessage' | 'ingestToolResult'>;

export interface MemoWeftRunnerOptions {
  /** 召回/摄入归属的 subject；缺省交给 Core（config.identity.subjectId）。 */
  subjectId?: string;
  /**
   * 注入块的语言（措辞照 Core action.ts 的 knowledgeBlock 双语口径）。缺省 'en'。
   * 只影响适配器拼的这段说明文字，不改 Core 行为。
   */
  lang?: 'en' | 'zh';
  /**
   * 召回按认知类型过滤（D-0022/D-0024）：透传进 `core.recall` 的 `contentTypes`（允许名单）。
   * 不传/空 = 全类型（行为不变）。过滤在 Core 侧做（后过滤，可能欠填），适配器只负责透传。
   */
  contentTypes?: ContentType[];
  /**
   * 召回解释（D-0021/D-0024）：透传进 `core.recall` 的 `explain`。true → onRecall 收到的每项带 provenance
   *   （其支撑/反证证据链，每条含 allowCloudRead/allowInference 授权位）。缺省 false = 不做额外查询、行为不变。
   * 隐私硬约束（D-0024·不可违反）：provenance【绝不】进注入 instructions——只经 onRecall 交宿主自筛（见 buildKnowledgeBlock）。
   */
  explain?: boolean;
  /** 每次成功召回后的回调（可选，便于宿主观测/日志）；召回为空也会以空数组触发。
   *  仅在 recall 成功返回后调用——非 user 末条（未召回）或 recall 抛错/超时（降级）时不触发。
   *  透传召回 v2 面：items 带 id/contentType/score，explain 时还带 provenance（含授权位）——宿主据此自筛/透视。 */
  onRecall?: (items: RecalledLike[]) => void;
  /**
   * 宿主已有的 `callModelInputFilter`（可选）：chain 在召回注入【之前】跑（先跑宿主编辑、再在其结果上追加召回块）。
   * 呼应 SDK「每次模型调用前编辑 instructions/input」的语义——两个 filter 顺序组合，互不吞。
   */
  callModelInputFilter?: CallModelInputFilter;
  /**
   * recall 超时阈值（毫秒，契约 §16.2）。缺省 200ms。超时即视为召回失败 → 降级为不注入。
   * 读路径不重试（超时/抛错直接降级），呼应 Core「召回失败不阻塞对话」纪律。
   */
  recallTimeoutMs?: number;
  /**
   * ingest（写路径）单次尝试的超时阈值（毫秒，可选）。不传/≤0 = 不套超时（只靠失败重试一次兜底）；
   * 传正数则每次尝试套 withTimeout，超时按失败计入「重试一次」（超时不重试，见 runIngestWithRetry）。
   * 无论如何写路径都不向 SDK 抛：失败/超时经一次重试仍失败 → logger 记事件、静默吞。
   */
  ingestTimeoutMs?: number;
  /**
   * 注入式 logger（可选，契约 §16.2）：召回超时/抛错、或 ingest 一次重试后仍失败降级时记一条【结构化事件】
   *   （`{ event:'memory_degraded', op, reason }`）。缺省不注入 = 静默降级。
   * 认知纪律 + 隐私：只记事件/原因，绝不记用户内容 / 原话 / 工具返回 / 密钥。
   */
  logger?: MemoWeftLogger;
}

/** 适配器专属的【每轮】附加配置（塞进 `run` 包装器的 options.memoweft，调 SDK 前会被剥掉）。 */
export interface MemoWeftRunExtras {
  /**
   * 稳定幂等键：这轮【用户原话】(spoken) 摄入的 originId。同一轮重放/重试只落一条。
   * 建议宿主用自己的 turnId / messageId；不传则不去重（每次都落，见 Core originId 语义）。
   */
  spokenOriginId?: string | null;
}

/** `run` 包装器的 options：SDK 的 NonStreamRunOptions + 适配器专属 `memoweft` 子对象（调 SDK 前剥离）。 */
export type MemoWeftRunOptions<
  TContext = undefined,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- mirrors the upstream Agent generic constraint without narrowing host agent output types
  TAgent extends Agent<any, any> = Agent<any, any>,
> = NonStreamRunOptions<TContext, TAgent> & { memoweft?: MemoWeftRunExtras };

/** 工厂返回的三件套。 */
export interface MemoWeftRunner {
  /**
   * run 包装器（非流式）：闭包捕获【未注入的原始 input】存 spoken(②) → chain 召回注入 filter(①) 进 run 选项 →
   * 跑真实 `run` → 扫 `RunResult.newItems` 摄 tool_call_output_item(③) → 原样返回 RunResult。
   * 写路径全程降级不中断（不向调用方抛记忆层的错）。
   */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- mirrors the upstream run() Agent constraint so every supported agent output type passes through unchanged
  run<TAgent extends Agent<any, any>, TContext = undefined>(
    agent: TAgent,
    input: string | AgentInputItem[],
    options?: MemoWeftRunOptions<TContext, TAgent>,
  ): Promise<RunResult<TContext, TAgent>>;
  /**
   * ① 召回注入 filter（已 chain 宿主 opts.callModelInputFilter）。供不走 `run` 包装器、自己驱动
   * `run`/`Runner`/`RunConfig` 的宿主直接插进 `callModelInputFilter` 选项。
   */
  callModelInputFilter: CallModelInputFilter;
  /**
   * ③ 工具结果摄入（可测）：扫一批 `RunItem`，筛 `tool_call_output_item` → ingestToolResult。
   * `run` 包装器内部即调它；也供自驱动 `run` 的宿主在拿到 `result.newItems` 后手动调。
   * @returns 符合条件、被【尝试】摄入的 tool_call_output_item 条数（**非**成功落库数）。写路径降级不中断:
   *   ingest 真失败会经 logger 记 `memory_degraded` 后静默吞、不体现在此计数(§16.2)——要观测写入成败请读 logger 事件。
   */
  persistToolOutputs(newItems: readonly RunItem[]): Promise<number>;
}

/**
 * ingest（写路径）执行器：契约 §16.2——【真错】重试一次再放弃；仍失败经 logger 记一条结构化事件后【静默吞】。
 * 绝不向 SDK / 调用方抛（本函数从不 reject）。ingest 的降级 reason 恒为 'error'（含超时也归 error）。
 * 幂等前提：重试去重靠【稳定非空 originId】（spoken 用宿主 turnId、tool 用 callId）。originId=null 时 Core 不去重，
 *   故【超时不重试】：withTimeout 只 race、不取消底层写，超时后底层 ingest 可能仍提交，盲重试会重复落库。
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
 * 从工具返回结果提可落库文本：字符串原样用；其它（对象/数组等）JSON 序列化。
 * 序列化失败（循环引用）或产出非串（undefined 等）→ 返回 null（守住 string|null 契约，绝不向外抛）。
 * 注意：本函数【只】接收 tool_call_output_item 的 `output`（工具真实返回的外部数据），从不接收调用意图/入参。
 */
function toolOutputText(output: unknown): string | null {
  if (output == null) return null;
  if (typeof output === 'string') return output;
  try {
    const s = JSON.stringify(output);
    return typeof s === 'string' ? s : null;
  } catch {
    return null;
  }
}

/** 从 tool_call_output_item 的 rawItem 取 callId（四种结果 item 均含 `callId: string`）；缺失/非串 → null。 */
function readCallId(rawItem: unknown): string | null {
  const raw = rawItem as { callId?: unknown } | null | undefined;
  return raw != null && typeof raw.callId === 'string' && raw.callId !== '' ? raw.callId : null;
}

/**
 * 从 SDK `run` 的 input 实参提【用户这轮原话】（② 的原文取处，闭包在注入之前捕获）。
 *   - string 直接是用户话；
 *   - AgentInputItem[]：取【最后一条 role==='user'】消息的文本（content 为 string 原样，为 part 数组则拼 input_text）。
 * 全空白 / 无 user 文本 → null（不落库）。按 unknown 防御解析，形状不合静默跳过。
 */
export function spokenTextFromRunInput(input: string | readonly AgentInputItem[]): string | null {
  if (typeof input === 'string') return input.trim() === '' ? null : input;
  if (!Array.isArray(input)) return null;
  return lastUserText(input);
}

/** 取一组 input items 里最后一条 user 消息的纯文本（content string 原样；part 数组拼 input_text）。无则 null。 */
function lastUserText(input: readonly AgentInputItem[]): string | null {
  for (let i = input.length - 1; i >= 0; i--) {
    const item = input[i] as { role?: unknown; content?: unknown } | undefined;
    if (!item || item.role !== 'user') continue;
    const content = item.content;
    if (typeof content === 'string') return content.trim() === '' ? null : content;
    if (!Array.isArray(content)) return null;
    const texts = content
      .filter((p): p is { type: 'input_text'; text: string } => {
        const part = p as { type?: unknown; text?: unknown };
        return part?.type === 'input_text' && typeof part.text === 'string';
      })
      .map((p) => p.text);
    if (texts.length === 0) return null;
    const joined = texts.join('\n');
    // 与 string 分支 / spokenTextFromRunInput / persistToolOutputs 的去空判定一致:纯空白不落 spoken、不发起召回
    //   (多模态消息如 [input_image, input_text:'   '] 曾漏此判 → 落一条空白证据 + 一次无意义召回)。
    return joined.trim() === '' ? null : joined;
  }
  return null;
}

/** 末条 input 是否为 user 消息——① 注入 guard：只在一轮开头（末条 user、模型尚未产出工具回合）注一次。 */
function isLastItemUserMessage(input: readonly AgentInputItem[]): boolean {
  const last = input[input.length - 1] as { role?: unknown } | undefined;
  return last != null && last.role === 'user';
}

/** 组合两个 filter：先跑 first（宿主）→ 在其 modelData 结果上跑 second（召回注入）。first 缺省 = 只跑 second。 */
function chainFilters(
  first: CallModelInputFilter | undefined,
  second: CallModelInputFilter,
): CallModelInputFilter {
  if (!first) return second;
  return async (args: CallModelInputFilterArgs) => {
    const mid = await first(args);
    return second({ ...args, modelData: mid });
  };
}

/**
 * 防呆标记「此 filter 已含召回注入」——宿主若把本适配器导出的 callModelInputFilter(已 chain recallInject)又
 *   传进 run 的 options.callModelInputFilter,run 不再重复 chain 召回,避免同一次模型调用把知识块注入两遍。
 */
const RECALL_INJECTED = Symbol('memoweft.recallInjected');
function markRecallInjected(f: CallModelInputFilter): CallModelInputFilter {
  (f as unknown as Record<symbol, unknown>)[RECALL_INJECTED] = true;
  return f;
}
function hasRecallInjected(f: CallModelInputFilter | undefined): boolean {
  return f != null && (f as unknown as Record<symbol, unknown>)[RECALL_INJECTED] === true;
}

/**
 * 造一组 MemoWeft 读写适配器件（run 包装器 + 召回注入 filter + 工具摄入函数）。
 *
 * @param core 只需持有 recall / ingestUserMessage / ingestToolResult 三方法的 Core（或其最小实现）。
 * @param opts subjectId / lang / contentTypes / explain / onRecall / callModelInputFilter / recallTimeoutMs / ingestTimeoutMs / logger。
 * @returns `{ run, callModelInputFilter, persistToolOutputs }`。
 */
export function createMemoWeftRunner(
  core: RunnerCore,
  opts: MemoWeftRunnerOptions = {},
): MemoWeftRunner {
  const {
    subjectId,
    lang = 'en',
    contentTypes,
    explain,
    onRecall,
    callModelInputFilter: hostFilter,
    recallTimeoutMs = DEFAULT_RECALL_TIMEOUT_MS,
    ingestTimeoutMs,
    logger,
  } = opts;

  // ── ① 召回注入 filter（只做召回；宿主 filter 由 chainFilters 前置）──
  //
  // guard（纪律）：只在【末条 input 为 user 消息】时注一次——一轮里模型每次调用（含工具回合）都会触发本 filter，
  //   末条为 tool_call_output/assistant 时不注，避免逐回合重复注入召回块。
  // 隐私 by-construction：注入进【instructions】（system 语境层），绝不碰 input 里的 user 原话——
  //   故 ② 闭包捕获的原始 input 永不含召回注入内容。
  const recallInject: CallModelInputFilter = async (args: CallModelInputFilterArgs) => {
    const modelData: ModelInputData = args.modelData;
    if (!isLastItemUserMessage(modelData.input)) return modelData;
    const query = lastUserText(modelData.input);
    if (!query) return modelData;

    // 契约 §16.2：withTimeout 包 recallTimeoutMs；读路径不重试，超时/抛错即降级为不注入。
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
      return modelData;
    }
    // 观测回调 + 拼块一并纳入降级 guard：宿主 onRecall 或拼块万一抛错，绝不 reject 本 filter / 掀翻这轮对话。
    try {
      onRecall?.(recalled);
      // 隐私硬约束（D-0024）：buildKnowledgeBlock 只用 content/confidence/credStatus——provenance 等绝不进 instructions。
      const block = buildKnowledgeBlock(recalled, lang);
      if (block === '') return modelData;
      const instructions = modelData.instructions
        ? modelData.instructions + block
        : block.replace(/^\n+/, '');
      return { ...modelData, instructions };
    } catch {
      logger?.({ event: 'memory_degraded', op: 'recall', reason: 'error' });
      return modelData;
    }
  };

  // 对外暴露的 filter：宿主 opts.callModelInputFilter 前置 chain（先宿主、后召回注入）。标记「已含召回注入」防呆重复注入。
  markRecallInjected(recallInject);
  const callModelInputFilter: CallModelInputFilter = markRecallInjected(chainFilters(hostFilter, recallInject));

  // ── ③ 工具结果摄入（可测）：只筛 tool_call_output_item，只读 output + rawItem.callId ──
  //
  // 铁律 3a（by-construction）：tool_call_item（调用意图/入参）不是本类型，天然不入循环；只有工具【返回结果】落库。
  const persistToolOutputs = async (newItems: readonly RunItem[]): Promise<number> => {
    let stored = 0;
    for (const item of newItems) {
      if (item == null || item.type !== 'tool_call_output_item') continue;
      const text = toolOutputText(item.output);
      if (text === null || text.trim() === '') continue; // 空/无载荷不落库
      const originId = readCallId(item.rawItem);
      // 落库走 ingestToolResult → sourceKind='tool' → 默认不上云（config.toolDefaults 兜底）。
      await runIngestWithRetry(
        () => core.ingestToolResult({ content: text, originId, subjectId }),
        ingestTimeoutMs,
        logger,
      );
      stored++;
    }
    return stored;
  };

  // ── ② 用户原话摄入 + ①③ 编排 = run 包装器 ──
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- mirrors the upstream run() Agent constraint so every supported agent output type passes through unchanged
  const run = async <TAgent extends Agent<any, any>, TContext = undefined>(
    agent: TAgent,
    input: string | AgentInputItem[],
    options?: MemoWeftRunOptions<TContext, TAgent>,
  ): Promise<RunResult<TContext, TAgent>> => {
    const { memoweft, ...sdkOptions } = options ?? {};

    // ② 写：把【用户这轮原话】(从原始 input 提，注入前) 沉淀成 spoken 证据。originId 用宿主 turnId 保证幂等。
    //    不传任何授权位——ingestUserMessage 存 spoken，本就不涉上云授权位（红线）。
    const spoken = spokenTextFromRunInput(input);
    if (spoken !== null) {
      await runIngestWithRetry(
        () =>
          core.ingestUserMessage({ content: spoken, originId: memoweft?.spokenOriginId ?? null, subjectId }),
        ingestTimeoutMs,
        logger,
      );
    }

    // ① 读：把召回注入 filter chain 进本轮 run 选项（宿主 per-run filter 优先，其次 opts 里的宿主 filter，再追加召回）。
    const perRunHostFilter = sdkOptions.callModelInputFilter ?? hostFilter;
    // 防呆:若宿主把本适配器导出的 callModelInputFilter(已含召回注入)又传进来,不重复 chain 召回(避免注入两遍)。
    const mergedFilter = hasRecallInjected(perRunHostFilter)
      ? perRunHostFilter
      : chainFilters(perRunHostFilter, recallInject);

    // 运行时按需加载真实 SDK（动态 import：只测 filter/摄入函数时根本不加载 @openai/agents）。
    const { run: sdkRun } = await import('@openai/agents');
    const result = (await sdkRun(agent, input, {
      ...sdkOptions,
      callModelInputFilter: mergedFilter,
    })) as RunResult<TContext, TAgent>;

    // ③ 写：run 结束后扫 newItems 摄工具结果。降级不中断——persistToolOutputs 内部从不抛。
    await persistToolOutputs(result.newItems);
    return result;
  };

  return { run, callModelInputFilter, persistToolOutputs };
}
