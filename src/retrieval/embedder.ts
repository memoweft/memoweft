/**
 * 嵌入器：召回依赖向量语义；嵌入器云端优先、可替换。
 * 用内置 fetch 打 OpenAI 兼容 /embeddings，不装 SDK。换本地只改这一处。
 *
 * 配置从 .env 读 MEMOWEFT_EMBED_*（兼容旧名 DLA_EMBED_*）；未配则 loadEmbedConfig 返回 null，Core 使用本地 FTS5 关键词召回。
 * 请求超时可经 MEMOWEFT_EMBED_TIMEOUT_MS 配置（兼容旧名 DLA_EMBED_TIMEOUT_MS），默认 60s；
 * 失败由上游容错（召回失败不挡回话、indexError 不回滚画像）。
 */
import { resolveLang } from '../config.ts';
import type { UsageStats } from '../llm/client.ts';

export interface Embedder {
  /** 把一组文本编码成向量。 */
  embed(texts: string[]): Promise<number[][]>;
  /** 累计嵌入调用次数（可选·用量统计·观测）：宿主自注入的 embedder 不带也照跑（缺省 undefined）。 */
  readonly callCount?: number;
  /** token 用量累计（可选·用量统计）：embedding 常是灌记忆的 token 大头，同 LLMClient.usage——读到才加、读不到跳过。 */
  readonly usage?: UsageStats;
}

export interface EmbedConfig {
  baseUrl: string;
  apiKey: string;
  model: string;
}

function tryLoadEnv(): void {
  try {
    process.loadEnvFile();
  } catch {
    /* 忽略 */
  }
}

/** 读 MEMOWEFT_EMBED_* 配置（兼容旧名 DLA_EMBED_*）；缺任一关键项 → 返回 null（调用方据此降级）。 */
export function loadEmbedConfig(): EmbedConfig | null {
  tryLoadEnv();
  const baseUrl = process.env.MEMOWEFT_EMBED_BASE_URL ?? process.env.DLA_EMBED_BASE_URL ?? '';
  const apiKey = process.env.MEMOWEFT_EMBED_API_KEY ?? process.env.DLA_EMBED_API_KEY ?? '';
  const model = process.env.MEMOWEFT_EMBED_MODEL ?? process.env.DLA_EMBED_MODEL ?? '';
  if (!baseUrl || !apiKey || !model) return null;
  return { baseUrl, apiKey, model };
}

export class OpenAICompatEmbedder implements Embedder {
  private readonly config: EmbedConfig;
  private _callCount = 0;
  private _usage: UsageStats = {
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
    callsWithUsage: 0,
  };

  constructor(cfg: EmbedConfig) {
    this.config = cfg;
  }

  /** 累计嵌入调用次数（用量统计·观测）：空输入直接返回、不计数（未打网络）。 */
  get callCount(): number {
    return this._callCount;
  }

  /** token 用量累计（用量统计）：返回快照拷贝，防外部改内部计数。 */
  get usage(): UsageStats {
    return { ...this._usage };
  }

  async embed(texts: string[]): Promise<number[][]> {
    if (texts.length === 0) return [];
    this._callCount++;
    const url = `${this.config.baseUrl.replace(/\/$/, '')}/embeddings`;
    // 嵌入请求使用有界超时，避免端点挂起导致 fetch 无限等待（上游 LLM client 同样设有 120s 超时）。
    // 毫秒从 env 读、默认 60000；双前缀兼容旧名（与本文件 loadEmbedConfig 口径一致）。
    const timeoutMs =
      Number(process.env.MEMOWEFT_EMBED_TIMEOUT_MS ?? process.env.DLA_EMBED_TIMEOUT_MS) || 60000;
    let res: Response;
    try {
      res = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${this.config.apiKey}`,
        },
        body: JSON.stringify({ model: this.config.model, input: texts }),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (err) {
      // 超时（TimeoutError）给清楚 message，让其走既有降级链（召回失败不挡回话、indexError 不回滚画像）。
      if (err instanceof Error && err.name === 'TimeoutError') {
        throw new Error(
          resolveLang() === 'zh'
            ? `嵌入请求超时（超过 ${timeoutMs}ms）`
            : `Embedding request timed out (exceeded ${timeoutMs}ms)`,
        );
      }
      throw err;
    }
    if (!res.ok) {
      const t = await res.text().catch(() => '');
      throw new Error(
        resolveLang() === 'zh'
          ? `嵌入请求失败 ${res.status}: ${t.slice(0, 300)}`
          : `Embedding request failed ${res.status}: ${t.slice(0, 300)}`,
      );
    }
    const data = (await res.json()) as {
      data?: Array<{ embedding?: number[] }>;
      // embeddings 响应的 usage 只有 prompt_tokens / total_tokens（无 completion——嵌入不生成）。
      usage?: { prompt_tokens?: number; total_tokens?: number };
    };
    // token 用量（用量统计·观测/计费）：读到才加、读不到静默跳过（同 chat 口径，绝不因缺 usage 崩）。
    const rawUsage = data.usage;
    if (rawUsage) {
      const p = typeof rawUsage.prompt_tokens === 'number' ? rawUsage.prompt_tokens : 0;
      const t = typeof rawUsage.total_tokens === 'number' ? rawUsage.total_tokens : p;
      this._usage = {
        promptTokens: this._usage.promptTokens + p,
        completionTokens: this._usage.completionTokens, // 嵌入无 completion，保持不变
        totalTokens: this._usage.totalTokens + t,
        callsWithUsage: this._usage.callsWithUsage + 1,
      };
    }
    const out = (data.data ?? []).map((d) => d.embedding ?? []);
    if (out.length !== texts.length) {
      throw new Error(
        resolveLang() === 'zh'
          ? `嵌入返回数量不符：期望 ${texts.length}，得到 ${out.length}`
          : `Embedding count mismatch: expected ${texts.length}, got ${out.length}`,
      );
    }
    return out;
  }
}
