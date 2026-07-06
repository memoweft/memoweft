/**
 * 写适配器：一轮对话结束后，把【用户这轮说的原话】沉淀成 MemoWeft 的 spoken 证据。
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

/** 只依赖 ingestUserMessage 一个方法——测试可传最小 stub。 */
type IngestOnly = Pick<MemoWeftCore, 'ingestUserMessage'>;

export interface PersistUserTurnInput {
  /** 用户这轮说的原话（由调用方在发起 generateText 时就持有的那份，别从模型响应里回捞）。 */
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
export async function persistUserTurn(core: IngestOnly, input: PersistUserTurnInput): Promise<void> {
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
    // 注意：不传任何授权位——ingestUserMessage 存 spoken，本就不涉上云授权位（红线）。
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
}

/**
 * 造一个可直接塞进 `generateText({ onEnd })` / `streamText({ onEnd })` 的回调。
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
  //   声明它只为契合 SDK Callback = (event) => PromiseLike<void>，能直接塞进 onEnd。
  return async (_event?: unknown) => {
    try {
      await persistUserTurn(core, {
        userMessage: opts.userMessage,
        originId: opts.originId,
        subjectId: opts.subjectId,
        hostId: opts.hostId,
        occurredAt: opts.occurredAt,
      });
    } catch (err) {
      if (opts.onError) opts.onError(err);
      // 无 onError 时静默吞：落记忆失败不该让宿主这轮对话失败。
    }
  };
}
