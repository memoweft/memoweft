/**
 * DISTILL_PROMPT —— 事件化的 system 提示词（distill 写路径 · §15.3 集中版本化）。
 *
 * 把用户的几句话总结成一段带情境的「事件」描述；只总结用户表达 + 情境，不含助手回话、
 * 不加 AI 推测评价（禁止系统自证 · 记≠改画像）。
 *
 * 版本变更日志：
 *   - v1：基线。
 *   - v2（2026-07-13 · D-0018 来源感知固化）：材料按来源标注（[用户说]/[行为观察]/[工具返回]），
 *     提示词从"只总结用户表达"泛化为"保留来源区分"——行为观察/工具返回不写成"用户说"，
 *     好让下游 consolidate 正确定 formedBy（observed/tool 不被误当 stated）。3a 纪律措辞（不出现助手话/
 *     不加推测）一字不改（铁律 3）。
 *
 * 改动纪律（§15.3 / D-0009）：改内容必须 bump version、重跑 bench/eval-consolidation.mjs 全量、
 *   commit 正文附前后分数对比。否则 tests/prompts/registry.test.ts 的哈希快照会变红。
 */
import type { VersionedPrompt } from '../prompts/types.ts';

export const DISTILL_PROMPT: VersionedPrompt = {
  id: 'distill',
  version: 'v2',
  text: {
    zh: [
      '你把关于用户的几条材料总结成一段带情境的"事件"描述。材料按行给出，每行带来源标注：',
      '[用户说]=用户亲口；[行为观察]=观察到的行为（不是用户说的）；[工具返回]=工具/外部返回的客观数据。',
      '规则：',
      '1. 按时间顺序把材料串成一段情境描述；【保留来源区分】——行为观察 / 工具返回不要写成"用户说"，据实叙述（如"观察到用户凌晨仍在打游戏"）。',
      '2. 不要加入你的推测、评价或建议；不要出现"助手"的话。',
      '3. 一段话，简洁、具体，点出关键信息（在做什么、什么状态、提到什么）。',
      '4. 只输出这段总结文本，不要解释。',
    ].join('\n'),
    en: [
      'You summarize a few pieces of material about the user into a single situated "event" description. Material is given line by line, each tagged with its source:',
      '[user said]=the user\'s own words; [observed behavior]=an observed behavior (not something the user said); [tool result]=objective data returned by a tool/external source.',
      'Rules:',
      '1. String the material into a situated description in chronological order; [preserve the source distinction]—do not render observed behaviors / tool results as things the "user said"; state them as they are (e.g., "observed the user still gaming at 3am").',
      '2. Do not add your own guesses, judgments, or advice; do not include any "assistant" remarks.',
      '3. One paragraph, concise and concrete, highlighting the key information (what they are doing, what state they are in, what they mention).',
      '4. Output only this summary text, with no explanation.',
    ].join('\n'),
  },
};
