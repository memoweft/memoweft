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
 *   - v4（2026-07-16 · v0.6 Phase 2 · D-0033/D-0034）：教三件事——① 读懂 ⟨AI 前一句⟩ 后缀（只读上下文、
 *     非用户原话、不可作证据），据它解出「是啊」这类孤儿回应在确认什么；② formed_by 加 **confirmed**
 *     （命题是 AI 提的、用户只点头认下 → confirmed 而非 stated；用户主动说出内容 → 仍是 stated）+ **窄范围**
 *     （长文档 + 含糊一句「好」不产认知；一次多命题只对明确点头的原子产）；③ 新增 **resolutions** 输出
 *     （每条原话一份语义解析 → semantic_resolution 表，见 consolidate.ts）。
 *     v3 的四类判断、闲聊守卫、support_evidence_ids、既有 formed_by 来源规则**全部一字不改**（铁律 3）；
 *     新内容一律追加，不改写旧句。resolved_content 是解释、不是证据——提示词里明写它不得进 support。
 *   - v5（2026-07-16 · v0.6 Phase 2 · 冲烟驱动 + 人类拍板）：补 **select（二选一）分支**。冲烟实测（CC-047）
 *     mimo 把「window or aisle?」+「The former.」标成 stated/600/limited，语料期望 confirmed —— 一查发现
 *     已批的派生表（docs/internal/v0.6-impact-report.md:88）**只议定了 affirm 与 negate，select 是灰区**。
 *     人类拍板 **select → confirmed**，判据是「这条信息的载体是谁的话」而非「AI 有没有预设答案」：「前者」
 *     两个字不承载任何内容，解析完全依赖 AI 那句（若上文被 240 字截断、或选项顺序记反，解出来就是反的），
 *     这种「理解依赖上下文」的不确定性正是 confirmed 低置信（280、封顶 480）的用途；凭两个字给 600/limited 偏高。
 *     同时修一个真 bug：v4 的点头清单只列了「后者」/"The latter"，而语料用的是「前者」/"The former."，
 *     模型可能压根没把它归进清单。
 *
 * 改动纪律（§15.3 / D-0009）：改内容必须 bump version、重跑 bench/eval-consolidation.mjs 全量、
 *   commit 正文附前后分数对比。认知纪律措辞（「只标冲突，不替换」「support_evidence_ids」）是纯位置
 *   迁移、一字不改（铁律 3）。否则 tests/prompts/registry.test.ts 的哈希快照会立刻变红。
 */
import type { VersionedPrompt } from '../prompts/types.ts';

export const CONSOLIDATE_PROMPT: VersionedPrompt = {
  id: 'consolidate',
  version: 'v5',
  text: {
    zh: [
      '你在维护对用户的认知画像。给你【现有画像】和【新材料】（事件 + 其下逐条原话，每条原话带 id 和来源标注：[用户说]=用户亲口 / [行为观察]=观察到的行为 / [工具返回]=工具返回的客观数据）。',
      '有的原话末尾带 ⟨AI 前一句…⟩ 后缀：那是上一轮 AI 说的话，【只是上下文，不是用户原话，不可作证据】。它的用处是让你看懂"是啊"这类只有几个字的回应到底在确认什么。',
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
      '  【附和】若某条原话带 ⟨AI 前一句⟩ 后缀、命题是 AI 提出的、用户只是点头认下（"是啊"/"对"/"嗯"）而没有主动说出内容 → formed_by=confirmed，【不是 stated】——那句话的内容是 AI 说的，不是用户亲口讲的。反之，用户自己把内容说出来了（哪怕 AI 前一句也提到过同一件事）→ 仍是 stated。',
      '  【附和·选择】若 AI 前一句给的是二选一/多选一（"A 还是 B?"），而用户只回一个指代（"前者"/"后者"/"第一个"/"A"）→ 同样是 formed_by=confirmed，不是 stated：那两个字本身不承载任何内容，选项和内容都在 AI 那句里、解析全靠它。先把指代解对（"前者"=AI 先说的那个，"后者"=AI 后说的那个），再按解出来的内容记。',
      '  【附和·与上面闲聊守卫的关系】守卫里说的"哈哈/好的/嗯"这类附和，指的是【空转附和】——不带 ⟨AI 前一句⟩ 后缀、或从后缀里解不出具体命题的。若带了后缀、且 AI 那句提的是单个具体命题，那声"嗯"就不是无实质信息：照【附和】产认知，不要输出空。',
      '  【附和·否认】若用户是【否认/纠正】AI 提的命题（"不是"/"没有"/"我不是"）→ 该记的是那个【否定命题】（AI 问"你是左撇子吧?"、用户答"不是" → 记"用户不是左撇子"），而这是用户自己的明确表达 → formed_by=stated，不是 confirmed（confirmed 只给"点头认下 AI 命题"这一种情形）。',
      '  【附和·含糊】若用户的点头本身是含糊的（"可能吧"/"大概"/"应该吧"）→ 优先不产认知；若要产，仍是 confirmed（命题还是 AI 提的，不因你没把握就变成你的推断），且 content 必须写成带不确定限定的暂定表述（如"用户可能不太会做饭（含糊认可、未明确）"），绝不可写成板上钉钉的结论。',
      '  【窄范围】只有当 AI 前一句提的是短的、具体的、单个命题、且用户明确点头时，才产 confirmed 认知。若 AI 前一句是一大段（长计划、一串条目）而用户只回一句含糊的"好"/"行吧" → 不产认知（无从确定他在认可其中哪一条）；AI 一次抛出多个命题 → 只对用户明确点头的那个原子产，含糊的不产。',
      'content_type ∈ fact|preference|goal|project|state|trait。',
      '另外输出 resolutions：【只给来源是 [用户说] 的原话】出语义解析（[行为观察]/[工具返回] 不是用户在说话，不要给；带 ⟨AI 前一句⟩ 的短回应尤其要给）——把指代解开，还原这句话真正断言了什么。字段：',
      '  evidence_id=该原话的 id（必须是上面给你的真实原话 id）；resolved_content=解开后它断言了什么（如 "是啊" + ⟨AI 前一句：你平时喝咖啡的吧?⟩ → "用户确认自己喝咖啡"）；',
      '  response_act=用户这句在做什么：affirm(点头认下)|negate(否认)|select(在给出的选项里选)|elaborate(补充说明)|ask(反问)|none|other；',
      '  prompt_act=AI 前一句在做什么：propose(提出关于用户的猜测)|ask(提问)|state(陈述)|none|other；',
      '  proposition_origin=命题是谁提出的：assistant_proposed(AI 提的、用户只是认下)|user_stated(用户自己说出来的)；',
      '  assertion_strength=断言有多强：explicit(明确)|weak(含糊，如"可能吧"/"大概")|none；',
      '  required_context=离开 AI 前一句就看不懂这句话时，写下所需的那点上下文；否则给 ""。',
      '  【resolved_content 是你的解释、不是证据】——它绝不能出现在任何 support_evidence_ids 里。',
      '严格按下面示例的字段名输出一个 JSON 对象，空的给 []，不要解释：',
      '{"new":[{"content":"用户喜欢咖啡","content_type":"preference","formed_by":"stated","support_evidence_ids":["ev-1"]}],' +
        '"reinforce":[{"cognition_id":"cog-x","support_evidence_ids":["ev-2"]}],' +
        '"correct":[{"cognition_id":"cog-tea","content":"用户现在不喝茶了","content_type":"preference","formed_by":"stated","support_evidence_ids":["ev-1"]}],' +
        '"conflict":[{"cognition_id":"cog-y","support_evidence_ids":["ev-3"]}],' +
        '"resolutions":[{"evidence_id":"ev-1","resolved_content":"用户确认自己喝咖啡","response_act":"affirm","prompt_act":"propose","proposition_origin":"assistant_proposed","assertion_strength":"explicit","required_context":"AI 前一句问『你平时喝咖啡的吧?』"}]}',
    ].join('\n'),
    en: [
      'You maintain a cognitive profile of the user. You are given the [Existing profile] and [New material] (events, each with its individual source utterances, every utterance carrying an id and a source tag: [user said]=the user\'s own words / [observed behavior]=an observed behavior / [tool result]=objective data returned by a tool).',
      'Some utterances carry a ⟨preceding AI turn…⟩ suffix: that is what the AI said in the previous turn — [context only, NOT the user\'s words, not usable as evidence]. Its purpose is to let you see what a few-word reply like "Yeah" is actually confirming.',
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
      '  [Affirmation] If an utterance carries a ⟨preceding AI turn⟩ suffix, the proposition came from the AI, and the user merely nodded along ("Yeah"/"Right"/"Uh-huh") without volunteering the content → formed_by=confirmed, [not stated]—that content was the AI\'s words, not something the user said themselves. Conversely, if the user did volunteer the content (even if the preceding AI turn happened to mention the same thing) → still stated.',
      '  [Affirmation · selection] If the preceding AI turn offered a choice ("A or B?") and the user replied with nothing but a pointer ("the former"/"the latter"/"the first one"/"A") → also formed_by=confirmed, not stated: those two words carry no content of their own—both the options and the content live in the AI\'s turn, and resolving them depends entirely on it. Resolve the pointer first ("the former" = whichever the AI named first, "the latter" = the one it named second), then record the resolved content.',
      '  [Affirmation · vs. the small-talk guard above] The "haha/ok/sure" fillers that guard names are [idle fillers]—ones carrying no ⟨preceding AI turn⟩ suffix, or from which no specific proposition can be recovered. When the suffix is there and the AI turn proposed a single specific thing, that "sure" is not "no substantive information": form the cognition per [Affirmation]; do not output empty.',
      '  [Affirmation · denial] If the user [denies/corrects] the AI\'s proposition ("No"/"Nope"/"I\'m not") → what gets recorded is the [negated proposition] (AI asks "You\'re left-handed, right?", user says "Nope" → record "The user is not left-handed"), and that is the user\'s own explicit assertion → formed_by=stated, not confirmed (confirmed is only for nodding along to a proposition the AI put forward).',
      '  [Affirmation · hedged] If the nod itself is hedged ("maybe"/"I guess"/"probably") → prefer forming nothing; if you do form one, it is still confirmed (the proposition remains the AI\'s—your lack of certainty does not turn it into your own inference), and its content must be worded as a tentative claim carrying a hedge (e.g., "The user may not be much of a cook (hedged acceptance, not explicit)"), never as a settled conclusion.',
      '  [Narrow scope] Only form a confirmed cognition when the preceding AI turn proposed a short, specific, single proposition AND the user clearly nodded to it. If the preceding AI turn was a long passage (a long plan, a list of items) and the user only replied with a vague "ok"/"sure" → form no cognition (there is no way to tell which item they endorsed); if the AI threw several propositions at once → form one only for the atom the user clearly nodded to, none for the vague ones.',
      'content_type ∈ fact|preference|goal|project|state|trait.',
      'Additionally output resolutions: [only for utterances tagged [user said]] ([observed behavior]/[tool result] are not the user speaking—emit none for those; short replies carrying a ⟨preceding AI turn⟩ especially need one)—resolve the references and recover what the utterance actually asserts. Fields:',
      '  evidence_id = that utterance\'s id (must be one of the real utterance ids given to you above); resolved_content = what it asserts once resolved (e.g., "Yeah" + ⟨preceding AI turn: You drink coffee, right?⟩ → "The user confirms they drink coffee");',
      '  response_act = what the user\'s utterance does: affirm(nods along)|negate(denies)|select(picks from the offered options)|elaborate(adds detail)|ask(asks back)|none|other;',
      '  prompt_act = what the preceding AI turn does: propose(offers a guess about the user)|ask(asks a question)|state(states something)|none|other;',
      '  proposition_origin = who introduced the proposition: assistant_proposed(the AI\'s, the user merely accepted it)|user_stated(the user said it themselves);',
      '  assertion_strength = how strong the assertion is: explicit|weak(hedged, e.g., "maybe"/"I guess")|none;',
      '  required_context = if the utterance is unintelligible without the preceding AI turn, write down the bit of context needed; otherwise "".',
      '  [resolved_content is your interpretation, not evidence]—it must never appear in any support_evidence_ids.',
      'Output a single JSON object strictly using the field names in the example below; use [] for empties; no explanation:',
      '{"new":[{"content":"The user likes coffee","content_type":"preference","formed_by":"stated","support_evidence_ids":["ev-1"]}],' +
        '"reinforce":[{"cognition_id":"cog-x","support_evidence_ids":["ev-2"]}],' +
        '"correct":[{"cognition_id":"cog-tea","content":"The user no longer drinks tea","content_type":"preference","formed_by":"stated","support_evidence_ids":["ev-1"]}],' +
        '"conflict":[{"cognition_id":"cog-y","support_evidence_ids":["ev-3"]}],' +
        '"resolutions":[{"evidence_id":"ev-1","resolved_content":"The user confirms they drink coffee","response_act":"affirm","prompt_act":"propose","proposition_origin":"assistant_proposed","assertion_strength":"explicit","required_context":"The preceding AI turn asked \'You drink coffee, right?\'"}]}',
    ].join('\n'),
  },
};
