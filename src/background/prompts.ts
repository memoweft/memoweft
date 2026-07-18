/**
 * TRENDS_PROMPT —— 跨会话趋势聚合的 system 提示词（aggregateTrends 后台 ·  集中版本化）。
 *
 * 把反复出现的瞬时状态片段聚成一条【持续趋势】认知；频率由规则先筛，LLM 只负责归纳命名。
 *
 * 版本变更日志：
 *   - v1：基线。
 *   - v2：**证据 id 改发短标号**。只动「id 长什么样」——
 *     `buildMessages` 不再发 36 字符 UUID，改发 `[e1]`（代码维护 标号↔真 id 映射、落库前翻回真 id），
 *     示例里的 `["ev-1","ev-2","ev-3"]` 相应改成 `["e1","e2","e3"]`。根因同 consolidate：
 *     模型模仿示例的 id 形态、间歇性把 UUID 截成前缀写回 → `windowEvidence` 精确匹配落空 → 趋势被
 *     静默丢弃。**关键认知约束保持不变**：「多条状态指向同一持续模式才给」「别把一次性情绪
 *     说成趋势」等全部原样，只改 id 书写形态。趋势提示词由哈希快照与单元测试共同覆盖。
 *
 * 改动纪律（提示词变更规则）：改内容必须 bump version；有 eval harness 的须重跑并附前后分数。
 *   否则 tests/prompts/registry.test.ts 的哈希快照会变红。
 */
import type { VersionedPrompt } from '../prompts/types.ts';

export const TRENDS_PROMPT: VersionedPrompt = {
  id: 'trends',
  version: 'v2',
  text: {
    zh: [
      '给你用户近期【反复出现的状态片段】（每条带标号，如 [e1]）。判断它们有没有汇成某种【持续趋势】。',
      '铁律：',
      '- 只有当多条状态确实指向同一个持续模式时才给（如多次烦/累/没睡好 → "最近持续情绪低落/压力大"）。',
      '- 一句话描述这个趋势；注明依据了哪些证据标号（照抄方括号里的，如 "e1"）；凑不出明确趋势就给空数组。',
      '- 别把"一次性的情绪"说成趋势；趋势是【一段时间反复】。',
      '严格按示例字段名输出一个 JSON 对象，不要解释（示例里的 e1/e2/e3 就是标号的样子）：',
      '{"trends":[{"content":"用户最近这段时间持续情绪低落","based_on_evidence_ids":["e1","e2","e3"]}]}',
    ].join('\n'),
    en: [
      "You are given the user's recent [recurring state fragments] (each with a tag, e.g. [e1]). Decide whether they add up to some [sustained trend].",
      'Iron rules:',
      '- Only give one when multiple states genuinely point to the same sustained pattern (e.g., repeated irritable/tired/slept-badly → "persistently low mood / under stress lately").',
      '- Describe the trend in one sentence; note which evidence tags it relies on (copy the tag inside the brackets verbatim, e.g. "e1"); give an empty array if no clear trend can be formed.',
      '- Do not call a "one-off emotion" a trend; a trend is [recurring over a period of time].',
      'Output a single JSON object strictly using the example field names; no explanation (the e1/e2/e3 below are what tags look like):',
      '{"trends":[{"content":"The user has been persistently low in mood lately","based_on_evidence_ids":["e1","e2","e3"]}]}',
    ].join('\n'),
  },
};
