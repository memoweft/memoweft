/**
 * Shared adapter degradation behavior.
 *
 * Contract:
 *   - recall 超时：默认 200ms，可配（工厂选项 recallTimeoutMs）；超时即视为失败。
 *   - 重试：读路径（recall）不重试，直接降级；写路径（ingest）失败重试一次再放弃。
 *   - 降级 = 注入空上下文（无记忆），对话不中断；经注入 logger 记一条结构化事件（默认无 logger = 静默）。
 *
 * 认知纪律 + 隐私：logger【只】记结构化降级事件（event / reason），
 *   绝不记用户内容 / 原话 / 密钥。
 *
 * 说明：各适配器包自持一份 degrade（与 adapter-ai-sdk 逐字对齐），保持包间零耦合。
 */

/** Default recall timeout in milliseconds. */
export const DEFAULT_RECALL_TIMEOUT_MS = 200;

/** 结构化降级事件（只含事件类型与元信息，不含任何用户内容 / 原话 / 密钥）。
 *  形状与 mcp-server 的 McpDegradedEvent 对齐：{ event:'memory_degraded', op, reason }
 *  （本适配器无 tool 概念，故省略 mcp 侧的可选 tool 字段）。 */
export interface MemoWeftDegradedEvent {
  event: 'memory_degraded';
  /** 记忆层操作类别（recall 读 / ingest 写）。 */
  op: 'recall' | 'ingest';
  /** 降级原因（ingest 目前只会 error）。 */
  reason: 'timeout' | 'error';
}

/** 注入式 logger：宿主可注入以观测降级；缺省不注入 = 静默降级。 */
export type MemoWeftLogger = (event: MemoWeftDegradedEvent) => void;

/** recall 超时的具名错误（供区分 timeout 与其它 error 以填 logger 的 reason）。 */
export class RecallTimeoutError extends Error {
  readonly timeoutMs: number;
  constructor(timeoutMs: number) {
    super(`memoweft-adapter: recall timed out after ${timeoutMs}ms`);
    this.name = 'RecallTimeoutError';
    this.timeoutMs = timeoutMs;
  }
}

/**
 * 用 Promise.race 给一个 promise 套超时：ms 内未 settle → 以 RecallTimeoutError 拒绝。
 * 定时器 unref（有则），不因超时器拖住事件循环；无论谁先赢都清定时器。
 */
export function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new RecallTimeoutError(ms)), ms);
    (timer as { unref?: () => void }).unref?.();
    p.then(
      (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      (e) => {
        clearTimeout(timer);
        reject(e);
      },
    );
  });
}
