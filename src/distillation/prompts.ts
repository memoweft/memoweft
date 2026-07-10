/**
 * DISTILL_PROMPT —— 事件化的 system 提示词（distill 写路径 · §15.3 集中版本化）。
 *
 * 把用户的几句话总结成一段带情境的「事件」描述；只总结用户表达 + 情境，不含助手回话、
 * 不加 AI 推测评价（禁止系统自证 · 记≠改画像）。
 *
 * 版本变更日志：
 *   - v1：基线。
 *
 * 改动纪律（§15.3 / D-0009）：改内容必须 bump version、重跑 bench/eval-consolidation.mjs 全量、
 *   commit 正文附前后分数对比。否则 tests/prompts/registry.test.ts 的哈希快照会变红。
 */
import type { VersionedPrompt } from '../prompts/types.ts';

export const DISTILL_PROMPT: VersionedPrompt = {
  id: 'distill',
  version: 'v1',
  text: {
    zh: [
      '你把用户的几句话总结成一段带情境的"事件"描述。',
      '规则：',
      '1. 只总结用户表达的内容和情境，按时间顺序串起来。',
      '2. 不要加入你的推测、评价或建议；不要出现"助手"的话。',
      '3. 一段话，简洁、具体，点出关键信息（在做什么、什么状态、提到什么）。',
      '4. 只输出这段总结文本，不要解释。',
    ].join('\n'),
    en: [
      'You summarize a few of the user\'s remarks into a single situated "event" description.',
      'Rules:',
      '1. Summarize only what the user expressed and its context, strung together in chronological order.',
      '2. Do not add your own guesses, judgments, or advice; do not include any "assistant" remarks.',
      '3. One paragraph, concise and concrete, highlighting the key information (what they are doing, what state they are in, what they mention).',
      '4. Output only this summary text, with no explanation.',
    ].join('\n'),
  },
};
