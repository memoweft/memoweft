/**
 * CONSOLIDATE_PROMPT —— 画像增量整理的 system 提示词（consolidate 写路径 · §15.3 集中版本化）。
 *
 * 判断【新材料】对【现有画像】意味着什么，输出四类：new / reinforce / correct / conflict；
 * 每条认知须给证据级溯源 support_evidence_ids（引不出确切原话就不给）。
 *
 * 版本变更日志：
 *   - v1：基线（四类判断 + 证据级溯源）。
 *   - v2（2026-07-10 · Phase 2.3）：v1 基线上加「闲聊无信息 → 四类全空」守卫，治 chitchat
 *     过度记忆（基线 chitchat 结构仅 21/35 → 33/35）；只丢无实质寒暄，情绪/事实/偏好照常记，
 *     不削弱其它纪律。
 *   - v3（2026-07-13 · D-0018 来源感知固化）：原话带来源标注（[用户说]/[行为观察]/[工具返回]），
 *     formed_by 规则据来源定——[行为观察]/[工具返回] 不是用户亲口，绝不可标 stated（observed→observed）。
 *     加固来源强度纪律；「只标冲突，不替换」「support_evidence_ids」等纪律措辞一字不改（铁律 3）。
 *
 * 改动纪律（§15.3 / D-0009）：改内容必须 bump version、重跑 bench/eval-consolidation.mjs 全量、
 *   commit 正文附前后分数对比。认知纪律措辞（「只标冲突，不替换」「support_evidence_ids」）是纯位置
 *   迁移、一字不改（铁律 3）。否则 tests/prompts/registry.test.ts 的哈希快照会立刻变红。
 */
import type { VersionedPrompt } from '../prompts/types.ts';

export const CONSOLIDATE_PROMPT: VersionedPrompt = {
  id: 'consolidate',
  version: 'v3',
  text: {
    zh: [
      '你在维护对用户的认知画像。给你【现有画像】和【新材料】（事件 + 其下逐条原话，每条原话带 id 和来源标注：[用户说]=用户亲口 / [行为观察]=观察到的行为 / [工具返回]=工具返回的客观数据）。',
      '判断新材料对画像意味着什么，输出四类：',
      '- new：新材料里有、现有画像没有的新认知。',
      '- reinforce：新原话印证了某条现有认知（给 cognition_id + 支撑的原话 id）。',
      '- correct：用户【明确纠正/否定】了某条现有认知（给被纠正的 cognition_id + 纠正后的新内容）。',
      '  注意：画像里 (hypothesis) 类型是【待验证的假设】；若用户的新话否定/澄清了某条假设，也归入 correct——',
      '  给被否定的 cognition_id，新内容写用户澄清出来的【事实】（content_type 用 fact/preference 等，不要再写 hypothesis）。',
      '- conflict：新原话与某条现有认知矛盾，但【不是用户明确纠正】（如行为观察 vs 旧偏好）→ 只标冲突，不替换。',
      '【重要】只为关于用户、值得长期记住的信息形成认知。若新材料只是寒暄或无实质信息（问候如"你好/在吗"、天气闲聊、"哈哈/好的/嗯"这类附和、与用户无关的即时评论），四类全部输出 []，不要硬凑认知。' +
        '注意：真实的情绪状态、事实、偏好、目标仍要照常记（别把它们当闲聊丢掉）。',
      '【关键】每条认知必须给 support_evidence_ids = 真正支撑它的【那几条原话 id】；',
      '  只挑真正相关的，别把同一事件里无关的原话也算上；引不出确切原话就【不要给这条】。',
      'formed_by：按每条支撑原话的来源标注定——[用户说] 且用户明确表达=stated；[行为观察] 不是用户亲口=observed；[工具返回] 是外部客观数据、绝不可标 stated；你自己推断出来的=inferred（如从"怎么找女朋友"推"单身"）。性格/特质多为 inferred 且保守。',
      'content_type ∈ fact|preference|goal|project|state|trait。',
      '严格按下面示例的字段名输出一个 JSON 对象，空的给 []，不要解释：',
      '{"new":[{"content":"用户喜欢咖啡","content_type":"preference","formed_by":"stated","support_evidence_ids":["ev-1"]}],' +
        '"reinforce":[{"cognition_id":"cog-x","support_evidence_ids":["ev-2"]}],' +
        '"correct":[{"cognition_id":"cog-tea","content":"用户现在不喝茶了","content_type":"preference","formed_by":"stated","support_evidence_ids":["ev-1"]}],' +
        '"conflict":[{"cognition_id":"cog-y","support_evidence_ids":["ev-3"]}]}',
    ].join('\n'),
    en: [
      'You maintain a cognitive profile of the user. You are given the [Existing profile] and [New material] (events, each with its individual source utterances, every utterance carrying an id and a source tag: [user said]=the user\'s own words / [observed behavior]=an observed behavior / [tool result]=objective data returned by a tool).',
      'Decide what the new material means for the profile, and output four categories:',
      '- new: a new cognition present in the new material but not in the existing profile.',
      '- reinforce: a new utterance corroborates an existing cognition (give the cognition_id + the supporting utterance ids).',
      '- correct: the user has [explicitly corrected/negated] an existing cognition (give the corrected cognition_id + the new content after correction).',
      "  Note: a (hypothesis)-type item in the profile is an [unverified guess]; if the user's new words negate/clarify such a hypothesis, that also goes under correct—",
      '  give the negated cognition_id, and write the new content as the [fact] the user clarified (use content_type fact/preference etc., not hypothesis again).',
      '- conflict: a new utterance contradicts an existing cognition but is [not an explicit user correction] (e.g., an observed behavior vs. a stated old preference) → only flag the conflict, do not replace.',
      '[Important] Only form cognitions for information worth remembering about the user. If the new material is mere small talk with no substantive information (greetings like "hi/are you there", weather chit-chat, fillers like "haha/ok/sure", off-topic remarks unrelated to the user), output [] for all four categories—do not force it. ' +
        'Note: genuine emotional states, facts, preferences, and goals must still be recorded as usual (do not discard those as small talk).',
      '[Key] Every cognition must give support_evidence_ids = the [specific utterance ids] that genuinely support it;',
      '  pick only the truly relevant ones, do not count unrelated utterances from the same event; if you cannot cite a definite utterance, [do not emit that item].',
      'formed_by: decide by the source tag of each supporting utterance—[user said] and explicitly expressed = stated; [observed behavior] is not the user\'s own words = observed; [tool result] is external objective data and must never be stated; what you inferred yourself = inferred (e.g., inferring "single" from "how do I find a girlfriend"). Personality/traits are mostly inferred and should be conservative.',
      'content_type ∈ fact|preference|goal|project|state|trait.',
      'Output a single JSON object strictly using the field names in the example below; use [] for empties; no explanation:',
      '{"new":[{"content":"The user likes coffee","content_type":"preference","formed_by":"stated","support_evidence_ids":["ev-1"]}],' +
        '"reinforce":[{"cognition_id":"cog-x","support_evidence_ids":["ev-2"]}],' +
        '"correct":[{"cognition_id":"cog-tea","content":"The user no longer drinks tea","content_type":"preference","formed_by":"stated","support_evidence_ids":["ev-1"]}],' +
        '"conflict":[{"cognition_id":"cog-y","support_evidence_ids":["ev-3"]}]}',
    ].join('\n'),
  },
};
