"""Clause-level source whitelist for owner goals; never rewrite Evidence."""
from __future__ import annotations

import re

# 1. 先把用户原话按句号、分号、逗号后接主语等切成分句（中英文都要）。
# 2. 一个分句**只有同时满足**以下条件，才能支持「本人长期目标/计划」：
#    - 主语是说话人本人：分句以「我 / 我的目标 / 我的计划 / 我打算 / 我想 / 我要 / 我准备」或 `I / My goal / My plan / I plan / I intend / I want / I'm going to / I aim` 开头（允许前面有时间状语，例如「三年内我要…」`next year I intend …`）；
#    - 该分句内没有否定词修饰目标动词（不/没/从未/不再/不打算/don't/never/not/no longer…）；
#    - 该分句内没有他人所有格或他人主语（「我女儿的 / 我父母的 / 我朋友想」`my daughter's / my parents' / my friend wants`；任何「我的 + 称谓/人名 + 的」或 `my + noun + 's/s'`）；
#    - 不是问句。
# 3. 模型建议形成目标、但没有任何分句满足白名单的：**不形成长期目标**，原话照常作为普通记忆来源保存（不丢信息）。白名单之外的说法识别不出来，接受为召回限制，写进 PR「已知限制」。
# 4. 「如果/if」条件不影响判断（条件目标仍是目标）；目标从句里的动词（say/tell 等）不能被当作转述他人的标记——只有分句**主语**是别人、或整句是引号引述时才算他人发言。
# 5. 同一分句内，只要否定词（不/没/没有/从未/并不/不再/别、`not/n't/never/no longer`）出现在目标动词**之前**，不论中间隔了多少修饰语（真的/really/actually/特别…），都算否定。但否定若只修饰另一个分句（「我不打算中断训练，我的长期目标是…」）不影响后一个分句。
# 6. 白名单要求**目标动词的直接主语**是「我/I」（或「我的目标/计划是…」这种本人所有格作主语）。如果在「我」之后、目标动词之前出现了另一个主语（他人所有格、称谓、人名、他/她/他们、`my parents / my friend / he / she / they / 人名`），例如「我知道我朋友想…」`I know my parents plan …`，目标动词的主语是那个人，不算本人目标。
# 7. 中文「但、但是、可是、不过、然而、而、只是、而且、并且」在逗号后或句首引出分句，与英文 `but/however/though/and` 对称。除规则 9 的后续本人收回外，否定、他人主语、问句和排除标记只检查与支持范围相交的目标分句，不扫描整条消息；别的分句既不能否决，也不能替被选分句提供目标。
# 8. 过往想法不是目标：想过、考虑过、准备过、曾经想、本来想、原本打算；
#    thought about / considered / used to want / was going to / had planned。
#    只排除用作目标谓词的这些明确形式，不扫描目标内容来猜测过去语义。
# 9. 同一条支持原话里，目标分句之后本人又出现否定意向（不打算、不想、不准备、
#    不会去、算了；not going to / don't plan / don't want / never mind），视为收回。
#    后续本人收回保留为原话反证，裁选前半句不能抹掉它；不分析否定的对象。
#    已知限制：「我的目标是当护士，我不打算放弃」也拦，原话作为普通来源保存。
# 近期时间词：今晚、今天、明天、待会、等会、马上、这周末；`tonight`、`today`、`tomorrow`、`this afternoon`、`this evening`、`later today`、`right now`。
# 明确玩笑/收回：开玩笑的、开个玩笑、说着玩的、逗你的；`just kidding`、`jk`、`kidding`。
# 两组排除标记分句内出现就不算长期目标；不推断反讽、反事实或活动含义。
# Existing Evidence validation independently requires a real user turn and
# permission. These checks neither copy nor alter any utterance or source span.

# This is only a routing cue: goals share the existing preference/attribute
# types. It is NOT an assertion that a goal is real. Every routed proposal
# must pass the source whitelist; ordinary preferences/states keep their type.
# A terminal 计划 or determiner + plan is a noun reference, not an intention
# predicate. Preserve the ordinary state path without inferring completion or
# abandonment from activity keywords. Possessive plan + copula still routes.
_GOAL = re.compile(
    r"目标(?=是|为|会|应|不)|计划(?=[^，,。;；!?！？\s])|打算|准备|想|我希望|我要|把[^，,。;；]+作为(?:长期)?目标"
    r"|\b(?:(?:goals?|plans?)\s+(?:is|isn't|are|was|were|would|should))\b"
    r"|\b(?<!my )(?<!the )(?<!our )(?<!this )(?<!that )(?<!a )(?<!an )plans?\b"
    r"|\b(?:planning|planned|intend(?:s|ed|ing)?|want(?:s|ed|ing)?|hope(?:s|d)?|pursue(?:s|d)?|aim(?:s|ed|ing)?|ambition)\b|\bgoing\s+to\b", re.I,
)
_PAST_INTENTION = re.compile(
    r"想过|考虑过|准备过|曾经想|本来想|原本打算"
    r"|\b(?:thought\s+about|considered|used\s+to\s+want|was\s+going\s+to|had\s+planned)\b", re.I,
)
# Quotes are masked in validation context only. Apostrophes inside words are
# possessives/contractions, not quote delimiters. Book titles are not clauses.
_QUOTE = re.compile(r'"[^"\n]*"|“[^”]*”|「[^」]*」|‘[^’]*’|(?<!\w)\'[^\'\n]+\'(?!\w)|《[^》]*》')
_SENTENCE = re.compile(r"[。.!?！？;；\n]+")
# Do not detach ', just kidding' or a date from its goal. A comma creates a
# clause only before a fresh subject (optionally preceded by a time adjunct).
_TIME_ZH = r"(?:(?:[一二三四五六七八九十百两\d]+年(?:内|后)|明年|今年|未来[一二三四五六七八九十百两\d]+年(?:内)?))"
_TIME_EN = r"(?:(?:next|this)\s+year|today|tomorrow|in\s+\w+\s+years?|within\s+\w+\s+years?)"
_SUBJECT = rf"(?:(?:{_TIME_ZH})?我|(?:{_TIME_EN}\s+)?(?:I\b|my\b)|he\b|she\b|they\b|他|她)"
_CONNECTOR_ZH = r"(?:但是|可是|不过|然而|只是|而且|并且|但|而)"
_CONNECTOR_EN = r"(?:but|however|though|and)"
# Connectors after commas start clauses even when the following subject is
# another person. Keep non-clausal commas (e.g. ', just kidding') intact.
_COMMA = re.compile(
    rf"[，,]\s*(?:{_CONNECTOR_ZH}|{_CONNECTOR_EN}\b)\s*"
    rf"|[，,]\s*(?={_SUBJECT})|\b{_CONNECTOR_EN}\s+(?={_SUBJECT})", re.I,
)
_CONNECTOR_PREFIX = re.compile(rf"^(?:{_CONNECTOR_ZH}|{_CONNECTOR_EN}\b)\s*", re.I)
_OWNER = re.compile(
    rf"^(?:{_TIME_ZH})?我"
    rf"|^(?:{_TIME_EN}\s+)?(?:I\b|my\s+(?:(?:long[- ]term|life|future)\s+)?(?:[\w-]+\s+)?(?:goal|plan)\b)", re.I,
)
_OTHER_POSSESSIVE = re.compile(
    r"我的(?!长期目标|长期计划|目标|计划)[^，,。;；！？!?：:\s的]+的"
    r"|我(?!的)[^，,。;；！？!?：:\s的]+的(?:目标|计划)"
    r"|\bmy\s+[\w-]+(?:'s|s')(?=\s|$)", re.I,
)
# Negation is ordered within one clause, never bounded by an adverb count.
# 别 is a negation token; the modifier 特别 must not be split into that token.
_NEGATION = re.compile(r"从未|并不|不再|没(?:有)?|不|(?<!特)别|\bnot\b|n't\b|\bnever\b|\bno\s+longer\b", re.I)
# A possessive goal subject uses a copula before its content; include that
# head (up to the first infinitive), so 'is really not to ...' stays negative
# without treating 'learn to say ...' as reported speech.
_COPULA_HEAD = re.compile(
    r"(?:目标|计划)[^，,。;；!?！？]*?是"
    r"|\b(?:goals?|plans?)\s+(?:isn't|is|are|was|were|would\s+be)\b.*?\bto\b", re.I,
)
_SELF = re.compile(
    r"我(?:的(?:长期|人生|未来)?(?:目标|计划))?"
    r"|\bI\b|\bmy\s+(?:(?:long[- ]term|life|future)\s+)?(?:[\w-]+\s+)?(?:goal|plan)\b"
    r"(?=\s+(?:is\b|isn't\b|are\b|was\b|were\b|would\b|should\b))", re.I,
)
# Positive subject-to-predicate grammar, not a list of other people's names
# or reporting verbs. An intervening finite verb/noun phrase is not an owner
# adjunct. In 'I think I really plan ...', the nearest explicit I is valid;
# in 'I think Rowan really plans ...', think/Rowan cannot pass this bridge.
# Unrecognized affirmative adjuncts remain the documented whitelist limit.
_ADJUNCT_EN = (
    r"'m|'ve|'ll|'d|am|will|would|shall|should|can|could|may|might|do|did|have|had|be|been|being"
    r"|now|still|also|always|usually|sometimes|already|again|very|rather|quite|indeed|just|only|[a-z]+ly"
)
_ADJUNCT_ZH = rf"{_TIME_ZH}|现在|最近|将|会|已经|还|正在|在|一直|曾|真的|特别|认真|地|很|非常|确实|真|也|正|仍然|始终|明确|早就"
_OWNER_ADJUNCT_EN = re.compile(rf"(?:(?>\s*(?:{_ADJUNCT_EN})\b\s*))*", re.I)
_OWNER_ADJUNCT_ZH = re.compile(rf"(?:(?>{_ADJUNCT_ZH}))*")
# Negative-intention heads retain the same direct-subject check. These are
# grammatical particles/determiners, not a new activity or reporting blacklist.
_RETRACTION_ADJUNCT_EN = re.compile(rf"(?:(?>\s*(?:{_ADJUNCT_EN}|even|ever)\b\s*))*", re.I)
_RETRACTION_ADJUNCT_ZH = re.compile(rf"(?:(?>{_ADJUNCT_ZH}|其实|从来|这个|那个|任何|是|本来|原本))*")
_RETRACTION_MARKER = re.compile(r"^(?:(?:我|I\b)\s*)?(?:算了|never\s+mind\b)", re.I)
_WILL_GO = re.compile(r"会去")
_EXCLUSION = re.compile(
    r"今晚|今天|明天|待会|等会|马上|这周末|开玩笑的|开个玩笑|说着玩的|逗你的"
    r"|\b(?:tonight|today|tomorrow|this afternoon|this evening|later today|right now|just kidding|jk|kidding)\b", re.I,
)
# Anchored grammatical subject + speech predicate only; never scan for
# say/tell inside a goal such as 'My goal is to learn to say hello'.
_OTHER_SPEAKER = re.compile(
    r"^(?:(?!(?:I|my)\b)(?:[\w'-]+\s+){1,2}(?:said|says|suggested|wrote)\b"
    r"|(?!我)[^，,。;；！？!?：:\s]{1,12}?(?:说|表示|建议|写道))", re.I,
)


def _owner_subject(clause: str, target: re.Match[str], *, negated: bool = False) -> re.Match[str] | None:
    """Require the target's nearest explicit subject, not merely initial I."""
    if _OWNER.match(clause) is None:
        return None
    subjects = [subject for subject in _SELF.finditer(clause) if subject.start() <= target.start()]
    if not subjects:
        return None
    subject = subjects[-1]
    if subject.end() > target.start():
        return subject
    bridge = clause[subject.end():target.start()].strip()
    zh = subject[0].startswith('我')
    if negated:
        if not zh:
            bridge = bridge.lower().replace("won't", 'will not').replace("can't", 'can not')
        bridge = _NEGATION.sub(' ', bridge)
        if zh:
            bridge = re.sub(r'\s+', '', bridge)
        adjuncts = _RETRACTION_ADJUNCT_ZH if zh else _RETRACTION_ADJUNCT_EN
    else:
        adjuncts = _OWNER_ADJUNCT_ZH if zh else _OWNER_ADJUNCT_EN
    return subject if adjuncts.fullmatch(bridge) is not None else None


def _supported_owner_goal(clause: str) -> bool:
    """Validate the direct owner subject and a current intention predicate."""
    target = _GOAL.search(clause)
    if target is None or any(p.start() <= target.start() < p.end() for p in _PAST_INTENTION.finditer(clause)):
        return False
    subject = _owner_subject(clause, target)
    if subject is None or _NEGATION.search(clause[:target.start()]):
        return False
    if subject.end() > target.start():
        copula = _COPULA_HEAD.match(clause, target.start())
        if copula is not None and _NEGATION.search(copula[0]):
            return False
    # The subject region is validated. Preserve the existing exclusion for
    # explicit possessives in content without spanning from owner 我 over its verb.
    return _OTHER_POSSESSIVE.search(clause[target.start():]) is None


def _owner_retraction(clause: str) -> bool:
    # Bare 算了 / never mind is the user's explicit, subject-omitted retraction.
    # Quoted speech has already been masked, retaining foreign attribution.
    if _RETRACTION_MARKER.match(clause):
        return True
    targets = [t for t in (_GOAL.search(clause), _WILL_GO.search(clause)) if t is not None]
    if not targets:
        return False
    target = min(targets, key=lambda match: match.start())
    subject = _owner_subject(clause, target, negated=True)
    if subject is None:
        return False
    if _NEGATION.search(clause[:target.start()]):
        return True
    copula = _COPULA_HEAD.match(clause, target.start()) if subject.end() > target.start() else None
    return copula is not None and _NEGATION.search(copula[0]) is not None


def _source_clauses(context: str) -> list[tuple[int, int, str]]:
    """Validation-only clauses; preserve all original offsets and support."""
    result: list[tuple[int, int, str]] = []
    boundaries = [0, *(m.end() for m in _SENTENCE.finditer(context)), len(context)]
    for left, right in zip(boundaries, boundaries[1:]):
        sentence = context[left:right]
        offset = 0
        for cut in [*_COMMA.finditer(sentence), None]:
            stop = cut.start() if cut else len(sentence)
            clause = _CONNECTOR_PREFIX.sub('', sentence[offset:stop].strip().lstrip('，,;； '), count=1)
            if clause:
                result.append((left + offset, left + stop, clause))
            offset = cut.end() if cut else len(sentence)
    return result


def owner_assertion_error(raw: str, start: int, end: int, *, proposed_goal: bool = False) -> str | None:
    """Check intersecting goal clauses, retaining offsets and speaker context.

    The four local checks use that clause; rule 9 additionally retains later
    self-retraction in the same utterance. The source remains validation
    context only: an unselected neighbour cannot rescue an invalid support,
    and a foreign quotation cannot lose its speaker by clipping its span.
    """
    normalized = raw.replace('’', "'").replace('‘', "'")
    quotes = list(_QUOTE.finditer(normalized))
    # A clipped quoted goal cannot erase the foreign speaker or quotation.
    if any(q.start() <= start and end <= q.end() for q in quotes):
        return 'owner_source_quotation'
    masked = list(normalized)
    for quote in quotes:
        masked[quote.start():quote.end()] = ' ' * (quote.end() - quote.start())
    context = ''.join(masked)
    clauses = _source_clauses(context)
    is_goal = proposed_goal or bool(_GOAL.search(normalized[start:end]))
    # Rule 9 is the sole cross-clause exception: use the complete same-user
    # supporting utterance, including later counterevidence outside a clipped
    # quote. Never inspect another Evidence/turn or infer a matching goal topic.
    last_retraction = max((left for left, _right, clause in clauses if _owner_retraction(clause)), default=-1) if is_goal else -1
    for left, right, clause in clauses:
        if right <= start or end <= left:
            continue
        if not is_goal:
            if _OTHER_SPEAKER.match(clause):
                return 'owner_source_other_speaker'
            continue
        if (_supported_owner_goal(clause)
                and not _EXCLUSION.search(normalized[left:right])
                and not re.search(r'[?？]', clause)
                and last_retraction < right):
            return None
    return 'owner_source_goal_whitelist' if is_goal else None
