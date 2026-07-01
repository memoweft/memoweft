/**
 * 嵌入器（地图 cell 7 / 11：召回靠向量语义；嵌入器云端优先、可替换）。
 * 用内置 fetch 打 OpenAI 兼容 /embeddings，不装 SDK。换本地只改这一处。
 *
 * 配置从 .env 读 MEMOWEFT_EMBED_*（兼容旧名 DLA_EMBED_*）；未配则 loadEmbedConfig 返回 null（召回降级为空，不报错）。
 */

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
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${this.config.apiKey}`,
      },
      body: JSON.stringify({ model: this.config.model, input: texts }),
    });
    if (!res.ok) {
      const t = await res.text().catch(() => '');
      throw new Error(`嵌入请求失败 ${res.status}: ${t.slice(0, 300)}`);
    }
    const data = (await res.json()) as { data?: Array<{ embedding?: number[] }> };
    const out = (data.data ?? []).map((d) => d.embedding ?? []);
    if (out.length !== texts.length) {
      throw new Error(`嵌入返回数量不符：期望 ${texts.length}，得到 ${out.length}`);
    }
    return out;
  }
}
