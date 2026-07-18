/**
 * hooks 型读写适配器：把 MemoWeft 的长期记忆接进 Claude Agent SDK（`@anthropic-ai/claude-agent-sdk`）。
 *
 * 范式：`query({ prompt, options: { hooks: { ...createMemoWeftAgentHooks(core).hooks } } })`。
 * 一个工厂造两个 hook，覆盖读写三条路径：
 *   ① 召回注入（读）+ ② 用户原话摄入（写）= 同一个 `UserPromptSubmit` hook；
 *   ③ 工具结果摄入（写）= `PostToolUse` hook。
 *
 * 边界（照 MemoWeft「Core 无头」纪律）：注入文案只搬 Core `action.ts` 的中性措辞（见 knowledgeBlock.ts），
 *   适配器不添加专属角色指令。
 *
 * 隐私保证：provenance / contentType / score / id【绝不】进 additionalContext 注入文本，
 *   只经 onRecall 交宿主自筛；buildKnowledgeBlock 只用 content/confidence/credStatus。
 *
 * tool-result-only ingestion（代码级 by design）：`PostToolUse` 只读 `tool_response`/`tool_use_id`，
 *   【绝不】解构/引用 `tool_input`（tool_input = 调用意图，禁摄入）；本适配器不挂 `PreToolUse`。
 *
 * 类型：从 SDK 用 `import type` 引 hook input / Options（peer + dev 依赖）；运行时不启动真实 SDK——
 *   测试直接调用下面的 hook 处理函数并提供构造的 input 对象。
 */
import type {
  Options,
  HookCallback,
  UserPromptSubmitHookInput,
  PostToolUseHookInput,
} from '@anthropic-ai/claude-agent-sdk';
import type { MemoWeftCore, RecalledCognition, ContentType } from 'memoweft';
import {
  DEFAULT_RECALL_TIMEOUT_MS,
  RecallTimeoutError,
  withTimeout,
  type MemoWeftLogger,
} from './degrade.ts';
import { buildKnowledgeBlock, type RecalledLike } from './knowledgeBlock.ts';

/** 只依赖读写三方法——测试可传最小 stub。 */
type AgentCore = Pick<MemoWeftCore, 'recall' | 'ingestUserMessage' | 'ingestToolResult'>;

export interface MemoWeftAgentHooksOptions {
  /** 召回/摄入归属的 subject；缺省交给 Core（config.identity.subjectId）。 */
  subjectId?: string;
  /**
   * 注入块的语言（措辞照 Core action.ts 的 knowledgeBlock 双语口径）。缺省 'en'。
   * 只影响适配器拼的这段说明文字，不改 Core 行为。
   */
  lang?: 'en' | 'zh';
  /**
   * 召回按认知类型过滤：透传进 `core.recall` 的 `contentTypes`（允许名单）。
   * 不传/空 = 全类型（行为不变）。过滤在 Core 侧做（后过滤，可能欠填），适配器只负责透传。
   */
  contentTypes?: ContentType[];
  /**
   * 召回解释：透传进 `core.recall` 的 `explain`。true → onRecall 收到的每项带 provenance
   *   （其支撑/反证证据链，每条含 allowCloudRead/allowInference 授权位）。缺省 false = 不做额外查询、行为不变。
   * 隐私保证：provenance【绝不】进注入 prompt——只经 onRecall 交宿主自筛（见 buildKnowledgeBlock）。
   */
  explain?: boolean;
  /** 每次成功召回后的回调（可选，便于宿主观测/日志）；召回为空也会以空数组触发。
   *  仅在 recall 成功返回后调用——空 prompt（未召回）或 recall 抛错/超时（降级）时不触发。
   *  透传召回 v2 面：items 带 id/contentType/score，explain 时还带 provenance（含授权位）——宿主据此自筛/透视。 */
  onRecall?: (items: RecalledLike[]) => void;
  /**
   * recall 超时阈值（毫秒，降级契约）。缺省 200ms。超时即视为召回失败 → 降级为不注入。
   * 读路径不重试（超时/抛错直接降级），呼应 Core「召回失败不阻塞对话」纪律。
   */
  recallTimeoutMs?: number;
  /**
   * ingest（写路径）单次尝试的超时阈值（毫秒，可选）。不传/≤0 = 不套超时（与 adapter-ai-sdk 写路径一致，
   *   只靠失败重试一次兜底）；传正数则每次尝试套 withTimeout，超时按失败计入「重试一次」。
   * 无论如何写路径都不向 SDK 抛：失败/超时经一次重试仍失败 → logger 记事件、静默吞（返回空 → SDK 正常继续）。
   */
  ingestTimeoutMs?: number;
  /**
   * 注入式 logger（可选，降级契约）：召回超时/抛错、或 ingest 一次重试后仍失败降级时记一条【结构化事件】
   *   （`{ event:'memory_degraded', op, reason }`）。缺省不注入 = 静默降级。
   * 认知纪律 + 隐私：只记事件/原因，绝不记用户内容 / 原话 / 工具返回 / 密钥。
   */
  logger?: MemoWeftLogger;
}

/** 工厂返回：把 `hooks` 直接摊进 `query` 的 `options.hooks`（与 SDK 的 `Options['hooks']` 同型，可直接 spread）。 */
export interface MemoWeftAgentHooks {
  hooks: NonNullable<Options['hooks']>;
}

/**
 * ingest（写路径）执行器：契约 ——【真错】重试一次再放弃；仍失败经 logger 记一条结构化事件后【静默吞】。
 * 绝不向 SDK 抛（本函数从不 reject）。ingest 的降级 reason 恒为 'error'（含超时也归 error）。
 * 幂等前提（重要）：重试去重靠【稳定非空 originId】（spoken 用 prompt_id、tool 用 tool_use_id）。当 prompt_id
 *   缺失 → originId=null 时 Core 不去重，故【超时不重试】：withTimeout 只 race、不取消底层写，超时后底层 ingest
 *   可能仍会提交，此时盲重试会把同一条原话重复落库。超时 → 直接降级；只有【真错】（写确未提交）才重试一次。
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
    // 超时（withTimeout 拒绝）→ 底层写可能已提交，不重试（否则 originId 为空时会重复落库）；真错 → 写确未提交，重试一次。
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
 * 注意：本函数【只】接收 `tool_response`（工具真实返回的外部数据），调用处从不把 `tool_input` 传进来。
 */
function toolResponseText(resp: unknown): string | null {
  if (resp == null) return null;
  if (typeof resp === 'string') return resp;
  try {
    const s = JSON.stringify(resp);
    return typeof s === 'string' ? s : null;
  } catch {
    return null;
  }
}

/**
 * 造一组 MemoWeft 读写 hooks，摊进 `query({ options: { hooks } })`。
 *
 * @param core 只需持有 recall / ingestUserMessage / ingestToolResult 三方法的 Core（或其最小实现）。
 * @param opts subjectId / lang / contentTypes / explain / onRecall / recallTimeoutMs / ingestTimeoutMs / logger。
 * @returns `{ hooks }`：`hooks.UserPromptSubmit`（召回注入 + 存用户原话）、`hooks.PostToolUse`（存工具结果）。
 */
export function createMemoWeftAgentHooks(
  core: AgentCore,
  opts: MemoWeftAgentHooksOptions = {},
): MemoWeftAgentHooks {
  const {
    subjectId,
    lang = 'en',
    contentTypes,
    explain,
    onRecall,
    recallTimeoutMs = DEFAULT_RECALL_TIMEOUT_MS,
    ingestTimeoutMs,
    logger,
  } = opts;

  // ── ① 召回注入（读）+ ② 用户原话摄入（写）= 同一个 UserPromptSubmit hook ──
  //
  // by design 干净（隐私）：注入走【返回值】的 additionalContext，不碰 input.prompt；
  //   故存进证据的原话（input.prompt）永不含召回注入内容。
  // 顺序（纪律）：先存原话（ingestUserMessage, spoken），再 recall → additionalContext。
  const userPromptSubmit: HookCallback = async (input) => {
    const i = input as UserPromptSubmitHookInput;
    const prompt = typeof i.prompt === 'string' ? i.prompt : '';
    // 空/全空白：无原话可存、无 query 可召回 → 不干预（返回空对象，SDK 正常继续）。
    if (prompt.trim() === '') return {};

    // ② 写：把【用户这轮原话】沉淀成 spoken 证据。originId 用 prompt_id（一轮 prompt 的稳定 UUID）保证幂等。
    //    不传任何授权位——ingestUserMessage 存 spoken，该入口不接受云读取授权覆盖。
    await runIngestWithRetry(
      () => core.ingestUserMessage({ content: prompt, originId: i.prompt_id ?? null, subjectId }),
      ingestTimeoutMs,
      logger,
    );

    // ① 读：召回 → additionalContext。契约 ：withTimeout 包 recallTimeoutMs；读路径不重试，超时/抛错即降级为不注入。
    let recalled: RecalledCognition[];
    try {
      recalled = await withTimeout(
        core.recall({ query: prompt, subjectId, contentTypes, explain }),
        recallTimeoutMs,
      );
    } catch (err) {
      // 召回失败/超时不挡回话：降级为不注入。经 logger 记结构化事件（缺省无 logger = 静默）；绝不记用户内容/原话。
      logger?.({
        event: 'memory_degraded',
        op: 'recall',
        reason: err instanceof RecallTimeoutError ? 'timeout' : 'error',
      });
      return {};
    }
    // 观测回调 + 拼注入块一并纳入降级 guard：宿主注入的 onRecall 或拼块万一抛错，
    //   绝不让它 reject 本 hook / 掀翻这轮对话——降级为不注入（记一条 recall 降级事件）。
    try {
      onRecall?.(recalled);
      // 隐私保证：buildKnowledgeBlock 只用 content/confidence/credStatus——
      //   provenance/contentType/score/id 绝不进 additionalContext。
      const block = buildKnowledgeBlock(recalled, lang);
      if (block === '') return {};
      // 逐轮动态召回必须走 additionalContext（不用 systemPrompt.append，那是整会话静态）。
      // 去掉前导换行（block 以 "\n\n" 起头，供 AI SDK 拼进消息用；此处作独立 context 段，前导空行无意义）。
      return {
        hookSpecificOutput: {
          hookEventName: 'UserPromptSubmit',
          additionalContext: block.replace(/^\n+/, ''),
        },
      };
    } catch {
      logger?.({ event: 'memory_degraded', op: 'recall', reason: 'error' });
      return {};
    }
  };

  // ── ③ 工具结果摄入（写）= PostToolUse hook ──
  //
  // tool-result-only ingestion（代码级 by design）：【只】读 tool_response / tool_use_id，绝不解构/引用 tool_input
  //   （tool_input = LLM 的调用意图/入参，是助手输出，永不成为证据）。
  const postToolUse: HookCallback = async (input) => {
    const i = input as PostToolUseHookInput;
    // 只碰 tool_response（外部客观数据）与 tool_use_id（幂等键）。tool_input 一个字都不读。
    const text = toolResponseText(i.tool_response);
    if (text === null || text.trim() === '') return {}; // 空/无载荷不落库
    const originId =
      typeof i.tool_use_id === 'string' && i.tool_use_id !== '' ? i.tool_use_id : null;
    // 落库走 ingestToolResult → sourceKind='tool' → 默认不进入内建云写模型 prompt（config.toolDefaults 兜底）。
    await runIngestWithRetry(
      () => core.ingestToolResult({ content: text, originId, subjectId }),
      ingestTimeoutMs,
      logger,
    );
    return {}; // 不改写工具输出、不干预流程。
  };

  return {
    hooks: {
      // 省略 matcher = 命中全部（UserPromptSubmit 无 matcher 概念；PostToolUse 无 matcher = 所有工具）。
      UserPromptSubmit: [{ hooks: [userPromptSubmit] }],
      PostToolUse: [{ hooks: [postToolUse] }],
    },
  };
}
