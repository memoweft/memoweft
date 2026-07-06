/**
 * 读适配器（RAG-as-middleware）：把 MemoWeft 的长期记忆召回，注入进发给模型的 prompt。
 *
 * 范式：`wrapLanguageModel({ model, middleware: createMemoWeftMiddleware(core) })`。
 * 在 `transformParams` 里取本轮最后一条 user 文本 → `await core.recall({ query })` →
 * 按 Core 现成的 knowledgeBlock 中性口径拼成一段说明 → 注入回最后一条 user 消息。
 *
 * 边界（照 MemoWeft「Core 无头」纪律）：注入文案只搬 Core `action.ts` 的中性措辞，
 *   低置信条目明确标 "only guesses—do not treat as established facts"。适配器里不自造人格/人设 prompt。
 *
 * 类型：用 `ai` re-export 的宽松 `LanguageModelMiddleware`（specificationVersion 可选，抗大版本漂移），
 *   不直绑 `@ai-sdk/provider` 的强版类型。
 */
import type { LanguageModelMiddleware } from 'ai';
import type { MemoWeftCore, RecalledCognition } from 'memoweft';

/** 最小召回项形状（只用注入需要的三个字段）。故意不引 Core 的完整类型，保持松耦合。 */
interface RecalledLike {
  content: string;
  confidence: number;
  credStatus: string;
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
  /** 召回为空时的回调（可选，便于宿主观测/日志）。 */
  onRecall?: (items: RecalledLike[]) => void;
}

/**
 * 拼注入块：照搬 Core `src/pipeline/action.ts` 的 knowledgeBlock 中性措辞（逐字对齐，别自造人设）。
 * 空召回返回空串（调用方据此决定不注入）。
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
 * 把一段说明追加进最后一条 user 消息（作为额外的 text part 塞在原文本之前——
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
 * 造一个 MemoWeft 读适配器 middleware。
 * @param core 只需持有 `recall` 方法的 Core（或任意实现了 recall 的对象）。
 * @param opts subjectId / lang / onRecall。
 */
export function createMemoWeftMiddleware(
  core: RecallOnly,
  opts: MemoWeftMiddlewareOptions = {},
): LanguageModelMiddleware {
  const { subjectId, lang = 'en', onRecall } = opts;
  return {
    async transformParams({ params }) {
      const query = getLastUserMessageText(params.prompt);
      // 没有可召回的 query（无 user 文本 / 纯多模态）→ 原样透传，不注入。
      if (!query) return params;

      let recalled: RecalledCognition[];
      try {
        recalled = await core.recall(subjectId ? { query, subjectId } : { query });
      } catch {
        // 召回失败不挡回话（呼应 Core "召回失败不阻塞对话"纪律）：静默降级为不注入。
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
