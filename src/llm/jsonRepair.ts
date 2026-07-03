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

/** 去掉 ```json … ``` / ``` … ``` 代码块围栏，返回里面的内容（没有围栏则原样返回）。 */
function stripCodeFences(s: string): string {
  const fenced = s.match(/```(?:json)?\s*([\s\S]*?)```/i);
  return fenced ? fenced[1]! : s;
}

/** 从一段可能带解释 / 代码块的文本里抠出最外层 JSON 对象文本；抠不到返回 null。 */
export function extractJsonObject(raw: string): string | null {
  const s = stripCodeFences(raw).trim();
  const start = s.indexOf('{');
  const end = s.lastIndexOf('}');
  if (start === -1 || end === -1 || end < start) return null;
  return s.slice(start, end + 1);
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
}

const JSON_ONLY_NUDGE =
  '你上一条回复不是合法的 JSON 对象。请【只】输出一个 JSON 对象，不要任何解释、不要 Markdown 代码块围栏。';

/**
 * 调模型 → 解析出一个 JSON 对象；失败则【落日志 + 最多重试一次】（追加"只输出 JSON"提示）；仍失败返回 null。
 * 调用方拿到 null 时按"本轮无产出"处理（与旧的"返回空对象"行为等价）。
 * 注意：重试会再调一次模型（llmCalls 会 +1），调用方若统计调用数，请在本函数前后取 callCount 差。
 */
export async function parseJsonObjectWithRepair<T = Record<string, unknown>>(
  deps: ParseWithRepairDeps,
): Promise<T | null> {
  const log = deps.log ?? ((m: string) => console.warn(`[memoweft/jsonRepair] ${m}`));

  const first = await deps.llm.chat(deps.messages);
  const parsed = parseJsonObject<T>(first);
  if (parsed !== null) return parsed;

  log(
    `首次输出非合法 JSON，重试一次。解析失败：长度=${first.length}、含代码围栏=${/```/.test(first)}、含花括号=${first.includes('{')}`,
  );
  const retryMessages: ChatMessage[] = [...deps.messages, { role: 'user', content: JSON_ONLY_NUDGE }];
  const second = await deps.llm.chat(retryMessages);
  const reparsed = parseJsonObject<T>(second);
  if (reparsed !== null) return reparsed;

  log(
    `重试后仍非合法 JSON，放弃本轮。解析失败：长度=${second.length}、含代码围栏=${/```/.test(second)}、含花括号=${second.includes('{')}`,
  );
  return null;
}
