/**
 * 回话：带最近几轮上下文 + 注入召回到的相关认知，调一次模型。
 *
 * b：把召回的画像条目（带把握度 / 可信状态）放进 system，让模型"懂这个用户"。
 * 纪律：把握度透明给模型（public contract）——低置信的明确标出，别当定论。
 * 边界（public contract）：MemoWeft 只给理解；语气 / 角色由宿主定，这里用最朴素的 prompt。
 */
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import type { Turn } from './workingMemory.ts';
import { resolveLang, type Lang } from '../config.ts';
import { REPLY_PROMPT } from './prompts.ts';

/** 召回到、要注入回话的一条认知。 */
export interface RelevantCognition {
  content: string;
  confidence: number;
  credStatus: string;
}

export interface ReplyResult {
  text: string;
  llmCalls: number;
}

function knowledgeBlock(relevant: RelevantCognition[], lang: Lang): string {
  if (relevant.length === 0) return '';
  const lines = relevant
    .map((c) =>
      lang === 'zh'
        ? `- ${c.content}（把握度 ${c.confidence}/1000，${c.credStatus}）`
        : `- ${c.content} (confidence ${c.confidence}/1000, ${c.credStatus})`,
    )
    .join('\n');
  const head =
    lang === 'zh'
      ? '\n\n你已了解关于这个用户的一些情况（带把握度；低置信的只是假设，别当定论、别生硬复述）：\n'
      : '\n\nHere is some of what you already understand about this user (with confidence; low-confidence items are only guesses—do not treat them as established facts, and do not recite them stiffly):\n';
  return head + lines;
}

export async function reply(
  userMsg: string,
  recent: Turn[],
  relevant: RelevantCognition[],
  llm: LLMClient,
  systemPrompt?: string, // 宿主可注入人设/框定（public contract：语气·角色归宿主）；缺省=库内最朴素提示（按语言取）
): Promise<ReplyResult> {
  const lang = resolveLang();
  const sys = systemPrompt ?? REPLY_PROMPT.text[lang];
  const messages: ChatMessage[] = [
    { role: 'system', content: sys + knowledgeBlock(relevant, lang) },
    ...recent.map((t): ChatMessage => ({ role: t.role, content: t.content })),
    { role: 'user', content: userMsg },
  ];
  const before = llm.callCount;
  const text = await llm.chat(messages);
  return { text, llmCalls: llm.callCount - before };
}
