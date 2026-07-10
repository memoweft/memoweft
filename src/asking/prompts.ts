/**
 * asking 提示词 —— 带证据主动询问 + 冲突复看的措辞 system 提示词（§15.3 集中版本化）。
 *
 * PROPOSE_ASK_PROMPT：对低置信【假设】亮证据、向用户求证（proposeAsk）。
 * REVISIT_CONFLICTS_PROMPT：对冲突中的认知并排亮正反两面、请用户澄清（revisitConflicts）。
 * 两者都只产「该问什么」，是否开口/最终措辞归宿主（cell 9）；提问不入证据库（规则 4）。
 *
 * 版本变更日志：
 *   - v1：基线。
 *
 * 改动纪律（§15.3 / D-0009）：改内容必须 bump version、重跑 bench/eval-consolidation.mjs 全量、
 *   commit 正文附前后分数对比。否则 tests/prompts/registry.test.ts 的哈希快照会变红。
 */
import type { VersionedPrompt } from '../prompts/types.ts';

export const PROPOSE_ASK_PROMPT: VersionedPrompt = {
  id: 'proposeAsk',
  version: 'v1',
  text: {
    zh: [
      '你在帮助理解用户。下面是一条关于用户的【低置信假设】和支撑它的【证据】。',
      '请生成一句简短、真诚、不武断的提问，亮出证据、向用户求证这条假设是否成立。',
      '只输出这一句问话本身，不要解释、不要引号。',
    ].join('\n'),
    en: [
      'You are helping to understand the user. Below is a [low-confidence hypothesis] about the user and the [evidence] supporting it.',
      'Generate one short, sincere, non-assertive question that presents the evidence and asks the user to confirm whether this hypothesis holds.',
      'Output only this one question itself, with no explanation and no quotation marks.',
    ].join('\n'),
  },
};

export const REVISIT_CONFLICTS_PROMPT: VersionedPrompt = {
  id: 'revisitConflicts',
  version: 'v1',
  text: {
    zh: [
      '下面是一条关于用户的认知，它【同时有支撑和反对的证据】，处于矛盾状态。',
      '请生成一句简短、真诚、不预设立场的提问，把两边都点一下、请用户澄清到底是哪样。',
      '只输出这一句问话本身，不要解释、不要引号。',
    ].join('\n'),
    en: [
      'Below is a cognition about the user that [has both supporting and opposing evidence] and is in a conflicted state.',
      'Generate one short, sincere, non-presumptive question that touches on both sides and asks the user to clarify which is actually the case.',
      'Output only this one question itself, with no explanation and no quotation marks.',
    ].join('\n'),
  },
};
