/**
 * 嵌入器（地图 cell 7 / 11：召回靠向量语义；嵌入器云端优先、可替换）。
 * 用内置 fetch 打 OpenAI 兼容 /embeddings，不装 SDK。换本地只改这一处。
 *
 * 配置从 .env 读 MEMOWEFT_EMBED_*（兼容旧名 DLA_EMBED_*）；未配则 loadEmbedConfig 返回 null（召回降级为空，不报错）。
 * 请求超时可经 MEMOWEFT_EMBED_TIMEOUT_MS 配置（兼容旧名 DLA_EMBED_TIMEOUT_MS），默认 60s；
 * 失败由上游容错（召回失败不挡回话、indexError 不回滚画像）。
 */
import { resolveLang } from '../config.ts';

export interface Embedder {
  /** 把一组文本编码成向量。 */
  embed(texts: string[]): Promise<number[][]>;
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

  constructor(cfg: EmbedConfig) {
    this.config = cfg;
  }

  async embed(texts: string[]): Promise<number[][]> {
    if (texts.length === 0) return [];
    const url = `${this.config.baseUrl.replace(/\/$/, '')}/embeddings`;
    // 超时中断：嵌入端点挂起时别让 fetch 裸奔无限等（上游 LLM client 已有同款 120s 超时）。
    // 毫秒从 env 读、默认 60000；双前缀兼容旧名（与本文件 loadEmbedConfig 口径一致）。
    const timeoutMs = Number(process.env.MEMOWEFT_EMBED_TIMEOUT_MS ?? process.env.DLA_EMBED_TIMEOUT_MS) || 60000;
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
    const data = (await res.json()) as { data?: Array<{ embedding?: number[] }> };
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
