/**
 * MCP server degradation behavior.
 *
 * Contract:
 *   - recall 超时：默认 200ms，可配（createMcpServer 选项 recallTimeoutMs）；超时即视为失败。
 *   - 重试：读路径（recall / list_* / graph）不重试，直接降级；写路径（ingest）失败重试一次再放弃。
 *   - 降级 = 读工具返回空结果、写工具返回未落库标记，均 isError:false、对话不中断；经注入 logger 记一条。
 *
 * 降级 vs 真错（契约边界）：只有 core.* 记忆层内部故障 / 超时才降级；
 *   参数非法（zod inputSchema 校验在 handler 之前）等"调用方的错"仍以协议错误上浮，不被吞。
 *
 * 认知纪律 + 隐私：logger【只】记结构化降级事件（event / tool / op / reason），
 *   绝不记用户内容 / 原话 / 密钥。
 */

/** Default recall timeout in milliseconds. */
export const DEFAULT_RECALL_TIMEOUT_MS = 200;

/** 结构化降级事件（只含事件类型与元信息，不含任何用户内容 / 原话 / 密钥）。 */
export interface McpDegradedEvent {
  event: 'memory_degraded';
  /** 触发降级的 tool 名（如 'memoweft_recall'）。 */
  tool: string;
  /** 记忆层操作类别。 */
  op: 'recall' | 'read' | 'ingest';
  /** 降级原因。 */
  reason: 'timeout' | 'error';
}

/** 注入式 logger：宿主可注入以观测降级；缺省不注入 = 静默降级。 */
export type McpServerLogger = (event: McpDegradedEvent) => void;

/** recall 超时的具名错误（供区分 timeout 与其它 error 以填 logger 的 reason）。 */
export class RecallTimeoutError extends Error {
  readonly timeoutMs: number;
  constructor(timeoutMs: number) {
    super(`memoweft-mcp: recall timed out after ${timeoutMs}ms`);
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

/**
 * 写路径重试一次：首次失败即再试一次（契约 ，稳定 originId 保证重试幂等）；仍失败则把错误抛给调用方。
 */
export async function retryOnce<T>(run: () => Promise<T>): Promise<T> {
  try {
    return await run();
  } catch {
    return await run();
  }
}
