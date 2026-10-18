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
# 近期时间词：今晚、今天、明天、待会、等会、马上、这周末；`tonight`、`today`、`tomorrow`、`this afternoon`、`this evening`、`later today`、`right now`。
# 明确玩笑/收回：开玩笑的、开个玩笑、说着玩的、逗你的；`just kidding`、`jk`、`kidding`。
# 两组排除标记分句内出现就不算长期目标；不推断反讽、反事实或活动含义。
# Existing Evidence validation independently requires a real user turn and
# permission. These checks neither copy nor alter any utterance or source span.

# This is only a routing cue: goals share the existing preference/attribute
# types. It is NOT an assertion that a goal is real. Every routed proposal
# must pass the source whitelist; ordinary preferences/states keep their type.
_GOAL = re.compile(
    r"目标(?=是|为|会|应|不)|计划|打算|准备|想|我希望|我要|把[^，,。;；]+作为(?:长期)?目标"
    r"|\b(?:my\s+(?:long[- ]term\s+)?goal\s+of|goals?\s+(?:is|isn't|are|was|were|would|should))\b"
    r"|\b(?:plans?|planning|planned|intend(?:s|ed|ing)?|want(?:s|ed|ing)?|hope(?:s|d)?|aim(?:s|ed|ing)?|ambition)\b|\bgoing\s+to\b", re.I,
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
_COMMA = re.compile(rf"[，,]\s*(?=(?:(?:and|but|however)\s+)?{_SUBJECT})|\b(?:but|and|however)\s+(?={_SUBJECT})", re.I)
_OWNER = re.compile(
    rf"^(?:{_TIME_ZH})?我(?:的(?:长期|人生|未来)?(?:目标|计划)|打算|想|要|准备|计划|希望|现在|最近|将|会|并|不|从|没|已经|还|在|是|决定|一直|曾)"
    rf"|^(?:{_TIME_EN}\s+)?(?:I\b|my\s+(?:(?:long[- ]term|life|future)\s+)?(?:[\w-]+\s+)?(?:goal|plan)\b)", re.I,
)
_OTHER_POSSESSIVE = re.compile(
    r"我的(?!长期目标|长期计划|目标|计划)[^，,。;；！？!?：:\s的]+的"
    r"|我(?!的)[^，,。;；！？!?：:\s的]+的(?:目标|计划)"
    r"|\bmy\s+[\w-]+(?:'s|s')(?=\s|$)", re.I,
)
_NEGATION = re.compile(
    r"(?:不(?:再)?|没(?:有)?|从未|从没|从来没(?:有)?)(?:打算|想(?:要)?|计划|准备|希望|要|会|把)"
    r"|(?:目标|计划)(?:并)?不(?:再)?是"
    r"|\b(?:don't|do\s+not|never|didn't|did\s+not|no\s+longer|not|won't)\s+"
    r"(?:plan(?:ned|ning)?|want(?:ed|ing)?|aim(?:ed|ing)?|pursue|hope(?:d)?|intend(?:ed|ing)?)\b"
    r"|\b(?:not|no\s+longer|never)\s+(?:planning|intending|wanting|going\s+to)\b"
    r"|\b(?:goal|plan)\s+(?:is\s+(?:not|no\s+longer|never)|isn't)\b", re.I,
)
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


def owner_assertion_error(raw: str, start: int, end: int, *, proposed_goal: bool = False) -> str | None:
    """Check full clauses, retaining original offsets and speaker context."""
    normalized = raw.replace('’', "'").replace('‘', "'")
    quotes = list(_QUOTE.finditer(normalized))
    # A clipped quoted goal cannot erase the foreign speaker or quotation.
    if any(q.start() <= start and end <= q.end() for q in quotes):
        return 'owner_source_quotation'
    masked = list(normalized)
    for quote in quotes:
        masked[quote.start():quote.end()] = ' ' * (quote.end() - quote.start())
    context = ''.join(masked)
    boundaries = [0, *(m.end() for m in _SENTENCE.finditer(context)), len(context)]
    is_goal = proposed_goal or bool(_GOAL.search(normalized[start:end]))
    for left, right in zip(boundaries, boundaries[1:]):
        if right <= start or end <= left:
            continue
        sentence = context[left:right]
        offset = 0
        for cut in [*_COMMA.finditer(sentence), None]:
            stop = cut.start() if cut else len(sentence)
            clause = sentence[offset:stop].strip().lstrip('，,;； ')
            clause = re.sub(r'^(?:and|but|however)\s+', '', clause, flags=re.I)
            clause_left, clause_right = left + offset, left + stop
            offset = cut.end() if cut else len(sentence)
            if clause_right <= start or end <= clause_left:
                continue
            if not is_goal:
                if _OTHER_SPEAKER.match(clause):
                    return 'owner_source_other_speaker'
                continue
            if (_OWNER.match(clause) and _GOAL.search(clause)
                    and not _OTHER_POSSESSIVE.search(clause)
                    and not _NEGATION.search(clause)
                    and not _EXCLUSION.search(normalized[clause_left:clause_right])
                    and not re.search(r'[?？]', clause)):
                return None
    return 'owner_source_goal_whitelist' if is_goal else None
