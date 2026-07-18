/**
 * 大模型调用封装：换模型只动这里。
 * 用内置 fetch 打 OpenAI 兼容 /chat/completions，不装 SDK（依赖取向：小而可换）。
 *
 * "问什么"（prompt）不在这里——留在 pipeline/action.ts。本文件只负责"发消息、拿文本、计数"。
 */
import { resolveLang } from '../config.ts';

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

/**
 * 模型部署位置（隐私分流维度·模型路由）：
 *   'cloud' = 云端模型（内建云写模型 prompt 只能选 allowCloudRead=true 的证据）；
 *   'local' = 本地模型（内建本地写模型 prompt 可选 allowLocalRead=true 的证据，含 observed 默认不进入云写 prompt 的那些）。
 * 谁云谁本地由宿主/用户【显式声明】（向导选 / env `*_TIER`）——库不按 baseUrl 猜（守"库不替宿主做安全策略"）。
 * 缺省视为 'cloud'（最保守：不误把敏感证据当本地放行）。见 evidence/privacy.ts filterReadableByTier。
 */
export type ModelTier = 'cloud' | 'local';

/**
 * LLM token 用量累计（观测/计费·用量统计「宿主能算钱」）：单调递增、绑 client 实例，复刻 callCount 形态。
 * 很多本地 / OpenAI 兼容端点【不回 usage】（llama.cpp / ollama / vLLM 某些配置）——读到才加、读不到跳过，
 * 故 `callsWithUsage` ≤ callCount。宿主要算"每次均耗"应拿 totalTokens / callsWithUsage，别拿 total / callCount
 * （会被没回 usage 的调用稀释而偏低）。只做纯计数供宿主乘单价，库不内置价目表、绝不流入置信度自算。
 */
export interface UsageStats {
  readonly promptTokens: number;
  readonly completionTokens: number;
  readonly totalTokens: number;
  /** 有几次调用真拿到了 usage（≤ callCount）；供宿主算有据的均值。 */
  readonly callsWithUsage: number;
}

/** 大模型客户端抽象——调用方只依赖此接口，不关心背后是哪家模型。 */
export interface LLMClient {
  chat(messages: ChatMessage[]): Promise<string>;
  /** 至今累计调用次数（用于统计本轮调了几次）。 */
  readonly callCount: number;
  /** 部署位置（可选·模型路由）：缺省 = 'cloud'（写路径隐私关据此决定按哪个授权位筛）。
   *  宿主自注入的 client 不带此字段也照跑（缺省当 cloud，非破坏）。tier 绑在 client 实例上，
   *  故 pool 缺配回退成对话模型时自然继承对话模型的 tier——杜绝"标 local 实跑云端"。 */
  readonly tier?: ModelTier;
  /** token 用量累计（可选·用量统计·观测/计费）：宿主自注入的 client 不带也照跑（缺省 undefined，同 tier?）。
   *  只做纯计数供宿主算钱，绝不流入置信度自算（同 temperature/tier 的洁癖）。见 UsageStats。 */
  readonly usage?: UsageStats;
}

export interface LLMConfig {
  baseUrl: string;
  apiKey: string;
  model: string;
  /** 生成温度（可选）：不配 = chat() 缺省 0.3（零行为变更）。按 prefix 自动分：
   *  `MEMOWEFT_LLM_TEMPERATURE`（对话）/ `MEMOWEFT_WRITE_LLM_TEMPERATURE`（写路径），双前缀兼容 `DLA_*`。
   *  仅进生成请求体，绝不流入置信度自算（confidence 由 MemoWeft 按规则算）。 */
  temperature?: number;
  /** 部署位置（可选·模型路由）：`MEMOWEFT_<prefix>_TIER=local|cloud`（双前缀兼容 `DLA_*`）。
   *  缺省 / 非法值 → undefined（下游按 'cloud' 处理，最保守）。仅决定写路径隐私关按哪个授权位筛，
   *  不进生成请求体、不流入置信度。 */
  tier?: ModelTier;
}

/**
 * 剥掉 reasoning 模型的思考段：只删【成对闭合】的 `<think>…</think>`（大小写不敏感、跨行）。
 * 无闭合 `</think>` 的一概不动——防把真答案误剥（写路径靠这段之后的 JSON 解析出结构）。
 * 主守在此（chat 层剥一次，chat/write 全用途受益）；jsonRepair 的括号配平是兜底。
 */
export function stripReasoning(s: string): string {
  return s.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
}

/**
 * 从响应消息里取模型的话。**推理模型要兜底读 `reasoning_content`**——这是与 stripReasoning 互补的另一半：
 * 那个管「思考段混在 content 里」，这个管「答案整个跑到 reasoning_content 里、content 留空」。
 *
 * 部分 OpenAI-compatible 推理模型会把完整回答放进 `reasoning_content`，同时让 `content` 为空，
 * 即使 `finish_reason=stop` 且 token 统计正常。典型响应形态：
 *   {"content":"", "reasoning_content":"{\"thought\":\"…\",\"done\":{\"summary\":\"…\"}}"}
 * 只读 content 时 `typeof '' === 'string'` 通过校验 → chat() 静默返回空串 → 上游 JSON 解析失败 →
 * consolidate 的四类全空（`?? {}`）→ 整批证据没有形成解析或认知。
 *
 * 取值顺序：content 有实质内容就用它（标准模型、绝大多数情况）；只有 content 空/缺才回落
 * reasoning_content —— 免得给正常模型平白掺进思考段（那是 stripReasoning 的活）。
 */
export function readReplyText(message?: {
  content?: string;
  reasoning_content?: string;
}): string | undefined {
  const content = message?.content;
  if (typeof content === 'string' && content.trim()) return content;
  const reasoning = message?.reasoning_content;
  if (typeof reasoning === 'string' && reasoning.trim()) return reasoning;
  // 两者皆空：保持 content 原形，交由既有的格式校验/上游处理，不在这里改判。
  return typeof content === 'string' ? content : undefined;
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
 * @param prefix 前缀语义（不含品牌），默认 `LLM`（对话模型）；写路径模型传 `WRITE_LLM`。
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
      resolveLang() === 'zh'
        ? `LLM 配置缺失：请在 .env 设置 MEMOWEFT_${base}_BASE_URL / _API_KEY / _MODEL（或兼容旧名 DLA_${base}_*）`
        : `Missing LLM config: set MEMOWEFT_${base}_BASE_URL / _API_KEY / _MODEL in .env (legacy DLA_${base}_* still supported)`,
    );
  }
  // temperature 可选：空 / 非数字 → undefined（chat() 里回落 0.3，零行为变更）。0 是合法值（写路径可要更稳）。
  const tempRaw = readEnvWithFallback(`${base}_TEMPERATURE`);
  const tempNum = Number(tempRaw);
  const temperature = tempRaw !== '' && Number.isFinite(tempNum) ? tempNum : undefined;
  // tier 可选（模型路由）：只认精确 'local' / 'cloud'（大小写不敏感）；其余（空 / 拼错 / 未知）→ undefined。
  //   保守：拼错绝不误当 'local'（否则敏感证据会被当本地放行）。下游 filterReadableByTier 用 `?? 'cloud'` 兜。
  const tierRaw = readEnvWithFallback(`${base}_TIER`).trim().toLowerCase();
  const tier: ModelTier | undefined =
    tierRaw === 'local' ? 'local' : tierRaw === 'cloud' ? 'cloud' : undefined;
  return { baseUrl, apiKey, model, temperature, tier };
}

/** OpenAI 兼容客户端——内置 fetch 直打 /chat/completions。 */
export class OpenAICompatClient implements LLMClient {
  private readonly config: LLMConfig;
  private _callCount = 0;
  private _usage: UsageStats = {
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
    callsWithUsage: 0,
  };

  constructor(cfg?: LLMConfig) {
    this.config = cfg ?? loadLLMConfig();
  }

  get callCount(): number {
    return this._callCount;
  }

  /** 部署位置（模型路由）：透传配置里的 tier；未配 = undefined（下游按 'cloud' 处理）。 */
  get tier(): ModelTier | undefined {
    return this.config.tier;
  }

  /** token 用量累计（用量统计）：返回快照拷贝，防外部改内部计数。 */
  get usage(): UsageStats {
    return { ...this._usage };
  }

  async chat(messages: ChatMessage[]): Promise<string> {
    this._callCount++;
    const url = `${this.config.baseUrl.replace(/\/$/, '')}/chat/completions`;
    // 超时中断：端点挂起时别无限占住 per-subject 锁（本地慢模型高发）。
    // 毫秒从 env 读、宽松默认 120000（本地 consolidate~47s/attribute~30s，别误杀）；双前缀兼容旧名。
    const timeoutMs = Number(readEnvWithFallback('LLM_TIMEOUT_MS')) || 120000;
    let res: Response;
    try {
      res = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${this.config.apiKey}`,
        },
        body: JSON.stringify({
          model: this.config.model,
          messages,
          temperature: this.config.temperature ?? 0.3,
        }),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (err) {
      // 超时（TimeoutError）给清楚 message，让其走既有降级链（错误往上抛）。
      if (err instanceof Error && err.name === 'TimeoutError') {
        throw new Error(
          resolveLang() === 'zh'
            ? `LLM 请求超时（超过 ${timeoutMs}ms）`
            : `LLM request timed out (exceeded ${timeoutMs}ms)`,
        );
      }
      throw err;
    }
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(
        resolveLang() === 'zh'
          ? `LLM 请求失败 ${res.status}: ${text.slice(0, 500)}`
          : `LLM request failed ${res.status}: ${text.slice(0, 500)}`,
      );
    }
    const data = (await res.json()) as {
      choices?: Array<{ message?: { content?: string; reasoning_content?: string } }>;
      usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
    };
    // token 用量（用量统计·观测/计费）：读到才加、读不到静默跳过（本地 / 兼容端点常不回 usage，绝不因此崩）。
    // 累加放在 content 校验【之前】：token 已真实消耗，即便下面因格式异常抛错也该记账。
    const rawUsage = data.usage;
    if (rawUsage) {
      const p = typeof rawUsage.prompt_tokens === 'number' ? rawUsage.prompt_tokens : 0;
      const c = typeof rawUsage.completion_tokens === 'number' ? rawUsage.completion_tokens : 0;
      const t = typeof rawUsage.total_tokens === 'number' ? rawUsage.total_tokens : p + c;
      this._usage = {
        promptTokens: this._usage.promptTokens + p,
        completionTokens: this._usage.completionTokens + c,
        totalTokens: this._usage.totalTokens + t,
        callsWithUsage: this._usage.callsWithUsage + 1,
      };
    }
    // reasoning 兼容·其一：答案整个跑进 reasoning_content、content 留空时回落读它（见 readReplyText）。
    const content = readReplyText(data.choices?.[0]?.message);
    if (typeof content !== 'string') {
      throw new Error(
        resolveLang() === 'zh'
          ? `LLM 返回格式异常：${JSON.stringify(data).slice(0, 500)}`
          : `Unexpected LLM response format: ${JSON.stringify(data).slice(0, 500)}`,
      );
    }
    // reasoning 兼容·其二：剥掉混在 content 里的 <think>…</think> 思考段（只剥闭合对）。
    return stripReasoning(content);
  }
}
