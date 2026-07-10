/**
 * TRENDS_PROMPT —— 跨会话趋势聚合的 system 提示词（aggregateTrends 后台 · §15.3 集中版本化）。
 *
 * 把反复出现的瞬时状态片段聚成一条【持续趋势】认知；频率由规则先筛，LLM 只负责归纳命名。
 *
 * 版本变更日志：
 *   - v1：基线。
 *
 * 改动纪律（§15.3 / D-0009）：改内容必须 bump version、重跑 bench/eval-consolidation.mjs 全量、
 *   commit 正文附前后分数对比。否则 tests/prompts/registry.test.ts 的哈希快照会变红。
 */
import type { VersionedPrompt } from '../prompts/types.ts';

export const TRENDS_PROMPT: VersionedPrompt = {
  id: 'trends',
  version: 'v1',
  text: {
    zh: [
      '给你用户近期【反复出现的状态片段】（每条带证据 id）。判断它们有没有汇成某种【持续趋势】。',
      '铁律：',
      '- 只有当多条状态确实指向同一个持续模式时才给（如多次烦/累/没睡好 → "最近持续情绪低落/压力大"）。',
      '- 一句话描述这个趋势；注明依据了哪些证据 id；凑不出明确趋势就给空数组。',
      '- 别把"一次性的情绪"说成趋势；趋势是【一段时间反复】。',
      '严格按示例字段名输出一个 JSON 对象，不要解释：',
      '{"trends":[{"content":"用户最近这段时间持续情绪低落","based_on_evidence_ids":["ev-1","ev-2","ev-3"]}]}',
    ].join('\n'),
    en: [
      "You are given the user's recent [recurring state fragments] (each with an evidence id). Decide whether they add up to some [sustained trend].",
      'Iron rules:',
      '- Only give one when multiple states genuinely point to the same sustained pattern (e.g., repeated irritable/tired/slept-badly → "persistently low mood / under stress lately").',
      '- Describe the trend in one sentence; note which evidence ids it relies on; give an empty array if no clear trend can be formed.',
      '- Do not call a "one-off emotion" a trend; a trend is [recurring over a period of time].',
      'Output a single JSON object strictly using the example field names; no explanation:',
      '{"trends":[{"content":"The user has been persistently low in mood lately","based_on_evidence_ids":["ev-1","ev-2","ev-3"]}]}',
    ].join('\n'),
  },
};
