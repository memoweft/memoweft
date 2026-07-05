/**
 * JSON 解析加固（写路径喂 LLM 回来的结构化输出用）。
 *
 * 背景：写路径（consolidate 等）要模型吐一个 JSON 对象。真实模型常出岔子——
 *   裹 ```json 代码块、前后带解释、半截 JSON、字段名乱写。原来的 `indexOf('{')..lastIndexOf('}')`
 *   够 dogfood，但接不同模型后不稳：失败就静默返回空、没日志、不重试。
 *
 * 本模块给三件套（越往下越"重"）：
 *   - extractJsonObject：从一段可能带围栏/解释的文本里，抠出最外层 `{...}` 文本。
 *   - parseJsonObject：抠出来 + JSON.parse，只认【对象】（数组/标量算不合法），失败返回 null。
 *   - parseJsonObjectWithRepair：调模型 → 解析；失败【落日志 + 最多重试一次】（追加"只输出 JSON"提示）；仍失败返回 null。
 *
 * 纪律：不做重 schema 校验——字段级容错留在各调用处（如 consolidate 的 pickCognition）。这里只保证"能拿到一个对象"。
 */
import type { LLMClient, ChatMessage } from './client.ts';
import { resolveLang, type Lang } from '../config.ts';

/** 去掉 ```json … ``` / ``` … ``` 代码块围栏，返回里面的内容（没有围栏则原样返回）。 */
function stripCodeFences(s: string): string {
  const fenced = s.match(/```(?:json)?\s*([\s\S]*?)```/i);
  return fenced ? fenced[1]! : s;
}

/** 从一段可能带解释 / 代码块的文本里抠出最外层 JSON 对象文本；抠不到返回 null。 */
export function extractJsonObject(raw: string): string | null {
  const s = stripCodeFences(raw).trim();
  const start = s.indexOf('{');
  if (start === -1) return null;
  // 从首个 { 起做括号配平扫描，取【第一个平衡闭合】的对象——比贪婪 lastIndexOf('}') 更抗
  // reasoning 思考残留 / 尾随文本污染；跳过字符串内的花括号。无平衡闭合 → null。
  let depth = 0;
  let inStr = false;
  let esc = false;
  for (let i = start; i < s.length; i++) {
    const ch = s[i]!;
    if (inStr) {
      if (esc) esc = false;
      else if (ch === '\\') esc = true;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') inStr = true;
    else if (ch === '{') depth++;
    else if (ch === '}' && --depth === 0) return s.slice(start, i + 1);
  }
  return null;
}

/** 抠出 JSON 对象文本并 parse；只认【对象】（数组 / 标量 / null 都算不合法），失败返回 null。 */
export function parseJsonObject<T = Record<string, unknown>>(raw: string): T | null {
  const text = extractJsonObject(raw);
  if (text === null) return null;
  try {
    const v: unknown = JSON.parse(text);
    return v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as T) : null;
  } catch {
    return null;
  }
}

export interface ParseWithRepairDeps {
  llm: LLMClient;
  messages: ChatMessage[];
  /**
   * 解析失败时落日志（默认 console.warn，带 `[memoweft/jsonRepair]` 前缀）。
   * 默认 sink【只记结构特征】（长度、是否含代码围栏 / 花括号），【不记模型原文】——
   * 模型输出基于用户原话、可能回显隐私（隐私优先）。宿主若想记更多原文，是宿主的选择：
   * 注入自己的 log 即可。
   */
  log?: (msg: string) => void;
  /** 重试提示语言（缺省=全局单例）：写路径调用方传各自 language，让"只输出 JSON"的纠偏提示跟随对话语言。 */
  lang?: Lang;
}

const JSON_ONLY_NUDGE: Record<Lang, string> = {
  zh: '你上一条回复不是合法的 JSON 对象。请【只】输出一个 JSON 对象，不要任何解释、不要 Markdown 代码块围栏。',
  en: 'Your previous reply was not a valid JSON object. Output [only] a single JSON object, with no explanation and no Markdown code fences.',
};

/**
 * 调模型 → 解析出一个 JSON 对象；失败则【落日志 + 最多重试一次】（追加"只输出 JSON"提示）；仍失败返回 null。
 * 调用方拿到 null 时按"本轮无产出"处理（与旧的"返回空对象"行为等价）。
 * 注意：重试会再调一次模型（llmCalls 会 +1），调用方若统计调用数，请在本函数前后取 callCount 差。
 */
export async function parseJsonObjectWithRepair<T = Record<string, unknown>>(
  deps: ParseWithRepairDeps,
): Promise<T | null> {
  const log = deps.log ?? ((m: string) => console.warn(`[memoweft/jsonRepair] ${m}`));
  const lang = deps.lang ?? resolveLang();

  const first = await deps.llm.chat(deps.messages);
  const parsed = parseJsonObject<T>(first);
  if (parsed !== null) return parsed;

  log(
    lang === 'zh'
      ? `首次输出非合法 JSON，重试一次。解析失败：长度=${first.length}、含代码围栏=${/```/.test(first)}、含花括号=${first.includes('{')}`
      : `First output was not valid JSON, retrying once. Parse failed: length=${first.length}, hasCodeFence=${/```/.test(first)}, hasBrace=${first.includes('{')}`,
  );
  const retryMessages: ChatMessage[] = [...deps.messages, { role: 'user', content: JSON_ONLY_NUDGE[lang] }];
  const second = await deps.llm.chat(retryMessages);
  const reparsed = parseJsonObject<T>(second);
  if (reparsed !== null) return reparsed;

  log(
    lang === 'zh'
      ? `重试后仍非合法 JSON，放弃本轮。解析失败：长度=${second.length}、含代码围栏=${/```/.test(second)}、含花括号=${second.includes('{')}`
      : `Still not valid JSON after retry, giving up this round. Parse failed: length=${second.length}, hasCodeFence=${/```/.test(second)}, hasBrace=${second.includes('{')}`,
  );
  return null;
}
