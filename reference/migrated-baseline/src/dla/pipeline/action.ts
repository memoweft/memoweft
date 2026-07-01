/**
 * ⑦ Action 行动 —— 打包 ContextPack 交大模型，生成最终回应。
 * 对应决策：D-002/D-015（大模型第 2 次出场）/ D-013（ContextPack 受上下文约束）。
 * 阶段：TASK-02 实现【简单拼接】；第四阶段精细化（ContextPack 分层 0.3 + 回复边界 0.3.1）。
 */

import type { Event } from '../event/model.ts';
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import type { RawInput } from './perception.ts';
import type { Turn } from './workingMemory.ts';

/**
 * 【简单拼接版】把原话 + 本轮解析出的 summary + 召回（本阶段为空）拼成上下文，
 * 交大模型生成回应。ContextPack 分层留到第四阶段。
 */
function buildReplyPrompt(rawInput: RawInput, event: Event, recalled: Event[]): ChatMessage[] {
  const recalledText = recalled.length
    ? recalled.map((e) => `- ${e.summary}`).join('\n')
    : '（暂无相关历史）';

  return [
    {
      role: 'system',
      content:
        '你在与用户对话。根据用户这句话，以及 DLA 对它的理解和相关历史，自然地回应一句。' +
        '回应要简洁、贴合语境。',
    },
    {
      role: 'user',
      content:
        `用户说：「${rawInput.raw_content}」\n` +
        `DLA 对这句话的理解：${event.summary}（主题：${event.topic}，情绪：${event.sentiment}）\n` +
        `相关历史：\n${recalledText}\n\n` +
        '请回应用户。',
    },
  ];
}

/**
 * ⑦ 调大模型生成最终回应（LLM 第 2 次出场，D-002/D-015）。
 * 刻意与 ③ 分开两次调用——职责分离的代价，不为省调用合并（D-015 裁定二）。
 */
export async function action(
  rawInput: RawInput,
  event: Event,
  recalled: Event[],
  llm: LLMClient,
): Promise<string> {
  return llm.chat(buildReplyPrompt(rawInput, event, recalled));
}

// ───────────────────────────────────────────────────────────────────────────
// TASK-03（D-024）：窗口感知的回话 —— 回话顺带判断是否需召回（同一次调用）。
// ───────────────────────────────────────────────────────────────────────────

/** 「回话顺带判断」的结构化产出（D-024 红线：判断与回话是同一次思考）。 */
export interface ReplyJudgment {
  /** 当前意图是否牵扯窗口之外的更早记忆。 */
  needRecall: boolean;
  /** needRecall=true 时：把当前句结合窗口补全成的【完整意图】，作检索词。 */
  recallQuery: string | null;
  /** needRecall=false 时：给用户的回答（needRecall=true 时可能为空）。 */
  reply: string;
}

/** 把窗口轮次渲染成文本块。 */
function renderWindow(window: Turn[]): string {
  if (!window.length) return '（空，这是对话开始）';
  return window
    .map((t) => `${t.role === 'user' ? '用户' : '助手'}：${t.content}`)
    .join('\n');
}

/**
 * 窗口回话 prompt（D-024 关键）：一次调用里，模型要么直接回答，要么回报"需检索什么"。
 * @param recallDone 是否已经做过召回（第二次调用时为 true，告诉模型"记忆已给你，现在就答"）。
 */
function buildWindowReplyPrompt(
  window: Turn[],
  userInput: string,
  recalled: Event[],
  recallDone: boolean,
): ChatMessage[] {
  const recallBlock = recallDone
    ? `\n【已为你检索到的更早记忆】\n${
        recalled.length ? recalled.map((e) => `- ${e.summary}`).join('\n') : '（没有找到相关的更早记忆）'
      }\n`
    : '';

  const system = recallDone
    ? '你在和用户连续对话。下面给你对话窗口、用户最新一句，以及刚为你检索到的更早记忆。' +
      '请综合这些信息直接回答用户，不要再要求检索。只输出 JSON：' +
      '{"need_recall": false, "recall_query": "", "reply": "给用户的回答"}'
    : '你在和用户连续对话。下面是最近的对话窗口和用户最新一句。\n' +
      '请基于窗口理解用户【当前意图】并回应：\n' +
      '- 如果窗口里的信息已足够回答 → 直接回答用户（need_recall=false，把回答写进 reply）。\n' +
      '- 如果用户的意图牵扯到【窗口里没有的、更早以前的事】，你才需要更早的记忆 →\n' +
      '  不要编造，而是回报你需要检索什么（need_recall=true，把"当前句结合窗口补全成的完整意图"写进 recall_query）。\n' +
      '窗口里的内容是"在场"的，用来帮你听懂现在，不要把它当作"需要回忆的过去"。\n' +
      '只输出 JSON，不要多余文字、不要代码块标记：\n' +
      '{"need_recall": false, "recall_query": "", "reply": ""}';

  return [
    { role: 'system', content: system },
    {
      role: 'user',
      content: `【对话窗口】\n${renderWindow(window)}\n${recallBlock}【用户最新一句】\n${userInput}`,
    },
  ];
}

/** 从模型返回文本里抽出 JSON（容忍代码块/多余文字包裹）。 */
function extractJudgment(text: string): ReplyJudgment {
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start === -1 || end === -1 || end < start) {
    // 模型没按格式返回——降级为"直接把原文当回答、不召回"，保证链路不崩。
    return { needRecall: false, recallQuery: null, reply: text.trim() };
  }
  const parsed = JSON.parse(text.slice(start, end + 1)) as {
    need_recall?: boolean;
    recall_query?: string;
    reply?: string;
  };
  return {
    needRecall: parsed.need_recall === true,
    recallQuery: parsed.recall_query ? parsed.recall_query : null,
    reply: typeof parsed.reply === 'string' ? parsed.reply : '',
  };
}

/**
 * 窗口回话（D-024）：一次模型调用，同时完成"判断是否需召回"与"回话"。
 * @param recalled    已召回的更早 Event（首次调用传空 []；下沉召回后再调一次传入结果）。
 * @param recallDone  是否已做过召回（决定 prompt 语气：true 时要求直接作答）。
 */
export async function windowReply(
  window: Turn[],
  userInput: string,
  recalled: Event[],
  llm: LLMClient,
  recallDone: boolean = false,
): Promise<ReplyJudgment> {
  const raw = await llm.chat(buildWindowReplyPrompt(window, userInput, recalled, recallDone));
  return extractJudgment(raw);
}
