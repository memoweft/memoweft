/**
 * ATTRIBUTE_PROMPT —— 归因/可解释假设的 system 提示词（集中版本化）。
 *
 * 为一个【现象】找【可能的原因】，产出可解释假设：低置信、挂证据、可被推翻。
 *
 * 版本变更日志：
 *   - v1：基线。
 *   - v2：**证据 id 改发短标号**。只动「id 长什么样」——
 *     `buildMessages` 不再将 36 字符 UUID 写入 prompt，改发 `[e1]`（代码维护 标号↔真 id 映射、
 *     落库前翻回真 id），示例里的 `["ev-1"]` 相应改成 `["e1"]`。根因同 consolidate：
 *     模型模仿示例的 id 形态、间歇性把 UUID 截成前缀写回 → `candidateIds` 精确匹配落空 → 假设被
 *     静默丢弃。发标号 = 示例与真实形态一致，模型结构上写不错。**关键认知约束保持不变**：
 *     「只给可能的原因、绝不下定论」「原因须是行为/客观观察」「至多 1 条」等全部原样，只改 id 书写形态。
 *     归因提示词由哈希快照与单元测试共同覆盖。
 *
 * 改动纪律（提示词变更规则）：改内容必须 bump version；有 eval harness 的须重跑并附前后分数。
 *   认知约束要求假设明确保持非结论性，并由提示词快照保证措辞稳定。
 *   否则 tests/prompts/registry.test.ts 的哈希快照会变红。
 */
import type { VersionedPrompt } from '../prompts/types.ts';

export const ATTRIBUTE_PROMPT: VersionedPrompt = {
  id: 'attribute',
  version: 'v2',
  text: {
    zh: [
      '你在为一个【现象】寻找【可能的原因】，产出"可解释假设"。',
      '铁律：',
      '- 只给【可能的】原因，绝不下定论；宁可一条不给，也不要硬编、不要凑数。',
      '- 原因必须是【行为或客观观察】（例如"游戏开到凌晨3:30"），不要用【另一种主观感受/情绪】去解释现象',
      '  （不要写"因为烦所以渴""因为没睡好所以烦"这种把一个抱怨接到另一个抱怨上）。',
      '- 每条假设必须基于下面列出的【证据】，注明依据的证据标号（照抄方括号里的标号，如 "e1"）；只引最相关的 1~2 条，没有合适的就不要给。',
      '- 一句话写清因果方向，例如"可能因为玩游戏太晚，导致没睡好"。',
      '- 至多给 1 条最站得住的假设；宁缺毋滥。',
      '严格按示例字段名输出一个 JSON 对象，没有就给空数组，不要解释（示例里的 e1 就是证据标号的样子）：',
      '{"hypotheses":[{"content":"可能因为玩游戏太晚导致没睡好","based_on_evidence_ids":["e1"]}]}',
    ].join('\n'),
    en: [
      'You are looking for [possible causes] of a [phenomenon], producing "explanatory hypotheses".',
      'Iron rules:',
      '- Give only [possible] causes, never conclusions; better to give none than to fabricate or pad.',
      '- A cause must be a [behavior or objective observation] (e.g., "gaming until 3:30 a.m."); do not explain the phenomenon with [another subjective feeling/emotion]',
      '  (do not write things like "irritable therefore thirsty" or "slept badly therefore irritable" that chain one complaint onto another).',
      '- Every hypothesis must be based on the [evidence] listed below, citing the evidence tags it relies on (copy the tag inside the brackets verbatim, e.g. "e1"); cite only the 1–2 most relevant, and give none if there is no suitable one.',
      '- State the causal direction in one sentence, e.g., "possibly slept badly because of gaming too late".',
      '- Give at most 1 best-supported hypothesis; quality over quantity.',
      'Output a single JSON object strictly using the example field names; give an empty array if none; no explanation (the e1 below is what an evidence tag looks like):',
      '{"hypotheses":[{"content":"Possibly slept badly because of gaming too late","based_on_evidence_ids":["e1"]}]}',
    ].join('\n'),
  },
};
