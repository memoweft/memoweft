/**
 * 大模型调用封装（地图 cell 11：换模型只动这里；参考 reference/migrated-baseline/llm/client.ts）。
 * 用内置 fetch 打 OpenAI 兼容 /chat/completions，不装 SDK（依赖取向：小而可换）。
 *
 * "问什么"（prompt）不在这里——留在 pipeline/action.ts。本文件只负责"发消息、拿文本、计数"。
 */

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

/** 大模型客户端抽象——调用方只依赖此接口，不关心背后是哪家模型。 */
export interface LLMClient {
  chat(messages: ChatMessage[]): Promise<string>;
  /** 至今累计调用次数（用于统计本轮调了几次）。 */
  readonly callCount: number;
}

export interface LLMConfig {
  baseUrl: string;
  apiKey: string;
  model: string;
}

function tryLoadEnv(): void {
  try {
    process.loadEnvFile();
  } catch {
    /* 没有 .env 或已由 --env-file 加载，忽略 */
  }
}

/**
 * 读单个 env 键，双前缀兼容：优先 `MEMOWEFT_<name>`，回退旧名 `DLA_<name>`。
 * 改名保守策略：新旧前缀都认，用户现有只含 DLA_* 的 .env 零改动继续工作。
 */
function readEnvWithFallback(name: string): string {
  return process.env[`MEMOWEFT_${name}`] ?? process.env[`DLA_${name}`] ?? '';
}

/**
 * 从环境变量组装配置；缺关键项则抛错（早失败优于静默错调）。
 * 双前缀兼容：每个键先读 `MEMOWEFT_*` 主名、回退旧名 `DLA_*`（见 readEnvWithFallback）。
 * @param prefix 前缀语义（不含品牌），默认 `LLM`（对话模型）；写路径小模型传 `WRITE_LLM`（治慢·可切换模型第一块）。
 *   历史兼容：也接受带旧品牌的 `DLA_LLM` / `DLA_WRITE_LLM`（自动剥去 `DLA_` 再走双前缀）。
 */
export function loadLLMConfig(prefix = 'LLM'): LLMConfig {
  tryLoadEnv();
  // 兼容旧调用方仍传 'DLA_LLM' / 'DLA_WRITE_LLM'：剥去 DLA_ 前缀，统一走双前缀读取。
  const base = prefix.startsWith('DLA_') ? prefix.slice(4) : prefix;
  const baseUrl = readEnvWithFallback(`${base}_BASE_URL`);
  const apiKey = readEnvWithFallback(`${base}_API_KEY`);
  const model = readEnvWithFallback(`${base}_MODEL`);
  if (!baseUrl || !apiKey || !model) {
    throw new Error(
      `LLM 配置缺失：请在 .env 设置 MEMOWEFT_${base}_BASE_URL / _API_KEY / _MODEL（或兼容旧名 DLA_${base}_*）`,
    );
  }
  return { baseUrl, apiKey, model };
}

/** OpenAI 兼容客户端——内置 fetch 直打 /chat/completions。 */
export class OpenAICompatClient implements LLMClient {
  private readonly config: LLMConfig;
  private _callCount = 0;

  constructor(cfg?: LLMConfig) {
    this.config = cfg ?? loadLLMConfig();
  }

  get callCount(): number {
    return this._callCount;
  }

  async chat(messages: ChatMessage[]): Promise<string> {
    this._callCount++;
    const url = `${this.config.baseUrl.replace(/\/$/, '')}/chat/completions`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${this.config.apiKey}`,
      },
      body: JSON.stringify({ model: this.config.model, messages, temperature: 0.3 }),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`LLM 请求失败 ${res.status}: ${text.slice(0, 500)}`);
    }
    const data = (await res.json()) as {
      choices?: Array<{ message?: { content?: string } }>;
    };
    const content = data.choices?.[0]?.message?.content;
    if (typeof content !== 'string') {
      throw new Error(`LLM 返回格式异常：${JSON.stringify(data).slice(0, 500)}`);
    }
    return content;
  }
}
