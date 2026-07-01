/**
 * 回话（地图 cell 4 ⑦）：带最近几轮上下文 + 注入召回到的相关认知，调一次模型。
 *
 * 阶段 1b：把召回的画像条目（带把握度 / 可信状态）放进 system，让模型"懂这个用户"。
 * 纪律：把握度透明给模型（cell 8 规则 7）——低置信的明确标出，别当定论。
 * 边界（cell 9）：MemoWeft 只给理解；语气 / 角色由宿主定，这里用最朴素的 prompt。
 */
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import type { Turn } from './workingMemory.ts';

const SYSTEM_PROMPT = '你基于最近的对话，自然、简洁、真诚地回应用户。';

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

function knowledgeBlock(relevant: RelevantCognition[]): string {
  if (relevant.length === 0) return '';
  const lines = relevant
    .map((c) => `- ${c.content}（把握度 ${c.confidence}/1000，${c.credStatus}）`)
    .join('\n');
  return (
    '\n\n你已了解关于这个用户的一些情况（带把握度；低置信的只是假设，别当定论、别生硬复述）：\n' +
    lines
  );
}

export async function reply(
  userMsg: string,
  recent: Turn[],
  relevant: RelevantCognition[],
  llm: LLMClient,
): Promise<ReplyResult> {
  const messages: ChatMessage[] = [
    { role: 'system', content: SYSTEM_PROMPT + knowledgeBlock(relevant) },
    ...recent.map((t): ChatMessage => ({ role: t.role, content: t.content })),
    { role: 'user', content: userMsg },
  ];
  const before = llm.callCount;
  const text = await llm.chat(messages);
  return { text, llmCalls: llm.callCount - before };
}
