/**
 * ATTRIBUTE_PROMPT —— 归因/可解释假设的 system 提示词（attribute 写路径 · §15.3 集中版本化）。
 *
 * 为一个【现象】找【可能的原因】，产出可解释假设：低置信、挂证据、可被推翻。
 *
 * 版本变更日志：
 *   - v1：基线。
 *
 * 改动纪律（§15.3 / D-0009）：改内容必须 bump version、重跑 bench/eval-consolidation.mjs 全量、
 *   commit 正文附前后分数对比。认知纪律措辞（「绝不下定论」——假设只低声说）是纯位置迁移、
 *   一字不改（铁律 3）。否则 tests/prompts/registry.test.ts 的哈希快照会变红。
 */
import type { VersionedPrompt } from '../prompts/types.ts';

export const ATTRIBUTE_PROMPT: VersionedPrompt = {
  id: 'attribute',
  version: 'v1',
  text: {
    zh: [
      '你在为一个【现象】寻找【可能的原因】，产出"可解释假设"。',
      '铁律：',
      '- 只给【可能的】原因，绝不下定论；宁可一条不给，也不要硬编、不要凑数。',
      '- 原因必须是【行为或客观观察】（例如"游戏开到凌晨3:30"），不要用【另一种主观感受/情绪】去解释现象',
      '  （不要写"因为烦所以渴""因为没睡好所以烦"这种把一个抱怨接到另一个抱怨上）。',
      '- 每条假设必须基于下面列出的【证据】，注明依据的证据 id；只引最相关的 1~2 条，没有合适的就不要给。',
      '- 一句话写清因果方向，例如"可能因为玩游戏太晚，导致没睡好"。',
      '- 至多给 1 条最站得住的假设；宁缺毋滥。',
      '严格按示例字段名输出一个 JSON 对象，没有就给空数组，不要解释：',
      '{"hypotheses":[{"content":"可能因为玩游戏太晚导致没睡好","based_on_evidence_ids":["ev-1"]}]}',
    ].join('\n'),
    en: [
      'You are looking for [possible causes] of a [phenomenon], producing "explanatory hypotheses".',
      'Iron rules:',
      '- Give only [possible] causes, never conclusions; better to give none than to fabricate or pad.',
      '- A cause must be a [behavior or objective observation] (e.g., "gaming until 3:30 a.m."); do not explain the phenomenon with [another subjective feeling/emotion]',
      '  (do not write things like "irritable therefore thirsty" or "slept badly therefore irritable" that chain one complaint onto another).',
      '- Every hypothesis must be based on the [evidence] listed below, citing the evidence ids it relies on; cite only the 1–2 most relevant, and give none if there is no suitable one.',
      '- State the causal direction in one sentence, e.g., "possibly slept badly because of gaming too late".',
      '- Give at most 1 best-supported hypothesis; quality over quantity.',
      'Output a single JSON object strictly using the example field names; give an empty array if none; no explanation:',
      '{"hypotheses":[{"content":"Possibly slept badly because of gaming too late","based_on_evidence_ids":["ev-1"]}]}',
    ].join('\n'),
  },
};
