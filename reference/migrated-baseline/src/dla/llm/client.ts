/**
 * 大模型调用封装 —— 统一管理两次调用（解析 + 回话）。
 * 对应决策：D-001（语义/回话归模型）/ D-017（换模型只改这里的隔离点）/ D-021（最小依赖，用内置 fetch）。
 * 阶段：TASK-02 起需要（③⑦ 经此调模型）。
 *
 * 设计意图（D-017）：把"用哪个模型、怎么调"收敛于此。换模型只动本文件，
 * 链路（runner/eventMaker/action）与真相（Event）都不变。
 *
 * "问什么"（解析 prompt / 回话 prompt）不在这里——那留在 eventMaker.ts / action.ts。
 * 本文件只负责"把 messages 发给模型、拿回文本"，并统计调用次数（验收3）。
 */

/** 一条对话消息（OpenAI chat 格式）。 */
export interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

/** 大模型客户端抽象——主链路只依赖此接口，不关心背后是哪家模型（D-017）。 */
export interface LLMClient {
  /** 发一组消息，拿回模型生成的文本。 */
  chat(messages: ChatMessage[]): Promise<string>;
  /** 至今累计的调用次数（验收3：证明一轮里被调两次，未被合并）。 */
  readonly callCount: number;
}

/** 从 .env / 环境变量读取的连接配置。 */
export interface LLMConfig {
  baseUrl: string;
  apiKey: string;
  model: string;
}

/** 尝试加载项目根的 .env（Node v20.6+ 内置，无需 dotenv 依赖）。失败静默忽略。 */
function tryLoadEnv(): void {
  try {
    process.loadEnvFile();
  } catch {
    /* 没有 .env 或已由 --env-file 加载，忽略 */
  }
}

/** 从环境变量组装配置；缺关键项则抛错（让调用方早失败，而非静默错调）。 */
export function loadLLMConfig(): LLMConfig {
  tryLoadEnv();
  const baseUrl = process.env.DLA_LLM_BASE_URL ?? '';
  const apiKey = process.env.DLA_LLM_API_KEY ?? '';
  const model = process.env.DLA_LLM_MODEL ?? '';
  if (!baseUrl || !apiKey || !model) {
    throw new Error(
      'LLM 配置缺失：请在 .env 设置 DLA_LLM_BASE_URL / DLA_LLM_API_KEY / DLA_LLM_MODEL',
    );
  }
  return { baseUrl, apiKey, model };
}

/**
 * OpenAI 兼容客户端 —— 用内置 fetch 直接打 /chat/completions（D-021：不装 openai SDK）。
 * 适配 OpenAI 官方及兼容该格式的中转/自建服务（本项目用小米 MiMo）。
 */
export class OpenAICompatClient implements LLMClient {
  private readonly config: LLMConfig;
  private _callCount = 0;

  constructor(config?: LLMConfig) {
    this.config = config ?? loadLLMConfig();
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
      body: JSON.stringify({
        model: this.config.model,
        messages,
        temperature: 0.3,
      }),
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
