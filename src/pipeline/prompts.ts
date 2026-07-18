/**
 * REPLY_PROMPT —— 回话的库内最朴素 system 提示词（reply 编排 ·  集中版本化）。
 *
 * 库只给最朴素回应提示；语气/角色/人设由宿主经 reply(systemPrompt) 覆盖（public contract：边界归宿主）。
 *
 * 版本变更日志：
 *   - v1：基线。
 *
 * 改动纪律（提示词变更规则）：改内容必须 bump version、重跑 bench/eval-consolidation.mjs 全量、
 *   commit 正文附前后分数对比。否则 tests/prompts/registry.test.ts 的哈希快照会变红。
 */
import type { VersionedPrompt } from '../prompts/types.ts';

export const REPLY_PROMPT: VersionedPrompt = {
  id: 'reply',
  version: 'v1',
  text: {
    zh: '你基于最近的对话，自然、简洁、真诚地回应用户。',
    en: 'Respond to the user naturally, concisely, and sincerely, based on the recent conversation.',
  },
};
