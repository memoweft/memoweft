/**
 * 读适配器（RAG-as-middleware）：把 MemoWeft 的长期记忆召回，注入进发给模型的 prompt。
 *
 * 范式：`wrapLanguageModel({ model, middleware: createMemoWeftMiddleware(core) })`。
 * 在 `transformParams` 里取本轮最后一条 user 文本 → `await core.recall({ query })` →
 * 按 Core 现成的 knowledgeBlock 中性口径拼成一段说明 → 注入回最后一条 user 消息。
 *
 * 边界（照 MemoWeft「Core 无头」纪律）：注入文案只搬 Core `action.ts` 的中性措辞，
 *   低置信条目明确标 "only guesses—do not treat as established facts"。适配器不添加专属角色指令。
 *
 * 类型：用 `ai` re-export 的宽松 `LanguageModelMiddleware`（specificationVersion 可选，抗大版本漂移），
 *   不直绑 `@ai-sdk/provider` 的强版类型。
 */
import type { LanguageModelMiddleware } from 'ai';
import type { MemoWeftCore, RecalledCognition, ContentType, RecalledEvidence } from 'memoweft';
import {
  DEFAULT_RECALL_TIMEOUT_MS,
  RecallTimeoutError,
  withTimeout,
  type MemoWeftLogger,
} from './degrade.ts';

/**
 * 召回项形状。注入块只用前三个字段（content/confidence/credStatus）；
 * id/contentType/score/provenance 是召回 v2 面——【只】经 onRecall 透传给宿主，
 * 全部可选以保持与 Core 松耦合、兼容旧构造。
 *
 * 写路径隐私保证：provenance 是证据【原文】+ 授权位（含默认不进入内建云写模型 prompt 的 observed/tool），
 *   【绝不】进 buildKnowledgeBlock / 注入 prompt（否则会绕过 tier，将受限原文提供给云模型）——
 *   只经 onRecall 交宿主，宿主转发云模型前据 allowCloudRead/allowInference 自筛。
 */
interface RecalledLike {
  content: string;
  confidence: number;
  credStatus: string;
  /** 认知 id：随召回带回，仅经 onRecall 交宿主（管理/透视反查），不进注入块。 */
  id?: string;
  /** 认知类型：仅经 onRecall 交宿主，不进注入块。 */
  contentType?: ContentType;
  /** 相似度分：仅经 onRecall 交宿主观测，不进注入块。 */
  score?: number;
  /** 召回解释链（仅在 explain 时提供）：证据原文 + 授权位。仅经 onRecall 交宿主，绝不进注入 prompt。 */
  provenance?: RecalledEvidence[];
}

/** 只依赖 recall 一个方法——测试可传最小 stub。 */
type RecallOnly = Pick<MemoWeftCore, 'recall'>;

export interface MemoWeftMiddlewareOptions {
  /** 召回归属的 subject；缺省交给 Core（config.identity.subjectId）。 */
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
   *  仅在 recall 成功返回后调用——无 user 文本（未召回）或 recall 抛错/超时（降级）时不触发。
   *  透传召回 v2 面：items 带 id/contentType/score，explain 时还带 provenance（含授权位）——宿主据此自筛/透视。 */
  onRecall?: (items: RecalledLike[]) => void;
  /**
   * recall 超时阈值（毫秒，降级契约）。缺省 200ms。超时即视为召回失败 → 降级为不注入。
   * 读路径不重试（超时/抛错直接降级），呼应 Core「召回失败不阻塞对话」纪律。
   */
  recallTimeoutMs?: number;
  /**
   * 注入式 logger（可选，降级契约）：召回超时/抛错降级时记一条【结构化事件】
   *   （`{ event:'memory_degraded', op:'recall', reason:'timeout'|'error' }`）。缺省不注入 = 静默降级。
   * 认知纪律 + 隐私：只记事件/原因，绝不记用户内容 / 原话 / 密钥。
   */
  logger?: MemoWeftLogger;
}

/**
 * 使用与 Core `src/pipeline/action.ts` 一致的中性 knowledgeBlock 文本契约，不添加适配器专属角色设定。
 * 空召回返回空串（调用方据此决定不注入）。
 *
 * 隐私保证：本块【只】用 content/confidence/credStatus。
 *   provenance（证据原文 + 授权位）、contentType、id、score 一律【不】入块——provenance 进 prompt = 绕过 tier
 *   将未获当前 tier 授权的证据原文暴露给模型；这些字段只能通过 onRecall 返回给宿主。
 */
export function buildKnowledgeBlock(relevant: RecalledLike[], lang: 'en' | 'zh' = 'en'): string {
  if (relevant.length === 0) return '';
  const lines = relevant
    .map((c) =>
      lang === 'zh'
        ? `- ${c.content}（把握度 ${c.confidence}/1000，${c.credStatus}）`
        : `- ${c.content} (confidence ${c.confidence}/1000, ${c.credStatus})`,
    )
    .join('\n');
  const head =
    lang === 'zh'
      ? '\n\n你已了解关于这个用户的一些情况（带把握度；低置信的只是假设，别当定论、别生硬复述）：\n'
      : '\n\nHere is some of what you already understand about this user (with confidence; low-confidence items are only guesses—do not treat them as established facts, and do not recite them stiffly):\n';
  return head + lines;
}

// ── last-user-message helper（非 SDK 自带，按 params.prompt 真实结构自研）──
//
// `ai` 的 LanguageModelV4Prompt = Array<LanguageModelV4Message>；
//   user 消息 = { role:'user', content: Array<TextPart|FilePart> }，TextPart = { type:'text', text:string }。
// 这里只碰 text part、不动 file/image part（多模态原样保留）。

/** 从 SDK prompt 数组里取最后一条 user 消息里的纯文本（多个 text part 用换行拼）。找不到返回 null。 */
export function getLastUserMessageText(prompt: unknown): string | null {
  if (!Array.isArray(prompt)) return null;
  for (let i = prompt.length - 1; i >= 0; i--) {
    const msg = prompt[i] as { role?: string; content?: unknown };
    if (!msg || msg.role !== 'user') continue;
    if (!Array.isArray(msg.content)) return null;
    const texts = msg.content
      .filter((p): p is { type: 'text'; text: string } => {
        const part = p as { type?: string; text?: unknown };
        return part?.type === 'text' && typeof part.text === 'string';
      })
      .map((p) => p.text);
    return texts.length > 0 ? texts.join('\n') : null;
  }
  return null;
}

/**
 * 把一段说明追加进最后一条 user 消息（作为额外的 text part 置于原文本之前——
 *   让"你已了解的情况"排在用户这轮问题之前，符合 knowledgeBlock 进 system 的语序意图）。
 * 返回一个【新的】prompt 数组（不原地改，避免污染调用方持有的对象）。找不到 user 消息则原样返回。
 */
export function addToLastUserMessage(prompt: unknown, block: string): unknown {
  if (!Array.isArray(prompt) || block === '') return prompt;
  const next = prompt.slice();
  for (let i = next.length - 1; i >= 0; i--) {
    const msg = next[i] as { role?: string; content?: unknown };
    if (!msg || msg.role !== 'user' || !Array.isArray(msg.content)) continue;
    const injected = { type: 'text' as const, text: block.replace(/^\n+/, '') + '\n\n' };
    next[i] = { ...msg, content: [injected, ...msg.content] };
    return next;
  }
  return prompt;
}

/**
 * 创建 MemoWeft 读取中间件。
 * @param core 只需持有 `recall` 方法的 Core（或任意实现了 recall 的对象）。
 * @param opts subjectId / lang / onRecall。
 */
export function createMemoWeftMiddleware(
  core: RecallOnly,
  opts: MemoWeftMiddlewareOptions = {},
): LanguageModelMiddleware {
  const {
    subjectId,
    lang = 'en',
    contentTypes,
    explain,
    onRecall,
    recallTimeoutMs = DEFAULT_RECALL_TIMEOUT_MS,
    logger,
  } = opts;
  return {
    async transformParams({ params }) {
      const query = getLastUserMessageText(params.prompt);
      // 没有可召回的 query（无 user 文本 / 纯多模态）→ 原样透传，不注入。
      if (!query) return params;

      let recalled: RecalledCognition[];
      try {
        // 契约 ：Promise.race 包 recallTimeoutMs（默认 200ms）超时；读路径不重试。
        // 召回 v2 透传：contentTypes / explain 原样交给 Core（过滤/解释都在 Core 侧做，适配器只透传）。
        recalled = await withTimeout(
          core.recall({ query, subjectId, contentTypes, explain }),
          recallTimeoutMs,
        );
      } catch (err) {
        // 召回失败/超时不挡回话（呼应 Core "召回失败不阻塞对话"纪律）：降级为不注入。
        //   经注入 logger 记一条结构化事件（缺省无 logger = 静默）；绝不记用户内容/原话。
        logger?.({
          event: 'memory_degraded',
          op: 'recall',
          reason: err instanceof RecallTimeoutError ? 'timeout' : 'error',
        });
        return params;
      }
      onRecall?.(recalled);
      const block = buildKnowledgeBlock(recalled, lang);
      if (block === '') return params;

      // addToLastUserMessage 收/返宽松 unknown（对外也当独立工具用）；这里回填 params 时
      //   收窄回 params.prompt 的真实类型——注入只改了 text part、结构与原 prompt 一致，cast 安全。
      const prompt = addToLastUserMessage(params.prompt, block) as typeof params.prompt;
      return { ...params, prompt };
    },
  };
}
