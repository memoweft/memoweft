"""High-confidence source rules; goal semantics belong to the interpreter.

原话与来源不改写。只拦明确的来源归属、直接否定目标动词、他人目标主语；
条件、日期、玩笑、疑问等不由内核猜语义，后续真实模型考试衡量。
"""
from __future__ import annotations

import re

# Syntax, not activity keywords: negation must govern intend/plan/want itself.
_NEGATED_GOAL = re.compile(
    r"(?:我(?:们)?|本人|咱|俺)(?:并|也|根本|真的)?(?:不(?:再)?|从未|从没|从来没(?:有)?|没(?:有)?)(?:打算|想(?:要)?|计划|准备)"
    r"|(?:我(?:们)?|本人)(?:不会|不|从不)把[^，,;；。.!?！？]+作为(?:长期)?目标"
    r"|\b(?:I|we)\s+(?:(?:don't|do not|never|didn't|did not)\s+(?:intend|plan|want)\s+to|(?:am|are)\s+not\s+(?:planning|intending|wanting)\s+to|(?:have|had)\s+never\s+(?:intended|planned|wanted)\s+to)"
    r"|\b(?:I'm|we're)\s+not\s+(?:planning|intending|wanting)\s+to"
    r"|\b(?:I|we)\s+(?:won't|will not|never)\s+pursue\s+(?:my|our|the)\s+(?:long[- ]term\s+)?goal", re.I,
)
_AFFIRMATIVE_GOAL = re.compile(
    r"^(?:(?:我(?:们)?|本人|咱|俺)的(?:长期|人生|未来)?(?:目标|计划)(?:是|为)|(?:我(?:们)?|本人)(?:计划|打算|准备|想(?:要)?)(?!不)|(?:今年|明年)?(?:我)?(?:一定要|决心|决定要))"
    r"|^(?:my|our)\s+(?:(?:long[- ]term|life|future)\s+)?(?:goal|plan|ambition)\s+(?:is|remains)\s+(?!not\b)"
    r"|^(?:I|we)\s+(?:intend|plan|want|aim|hope)\s+to\b", re.I,
)
_CLAUSES = re.compile(r"[，,;；。.!?！？\n]|\b(?:but|and|however)\b|但是|不过|而是|而且", re.I)
_ROLE_ZH = r"(?:女儿|儿子|朋友|同事|室友|母亲|父亲|妈妈|爸爸|姐姐|妹妹|哥哥|弟弟|妻子|丈夫|伴侣|老师|学生)"
_ROLE_EN = r"(?:daughter|son|friend|colleague|roommate|mother|father|sister|brother|wife|husband|partner|teacher|student)"
# Explicit possessive goal subjects are never the account owner's goal.
_OTHER_GOAL = re.compile(
    rf"^(?:(?:我(?:的)?|我们的?){_ROLE_ZH}(?:的)?(?:长期|人生)?(?:目标|计划|打算|想(?:要)?|准备)"
    rf"|(?:my|our)\s+[\w-]+(?:['’]s)\s+(?:long[- ]term\s+)?(?:goals?|plans?|ambitions?)\b"
    rf"|(?:my|our)\s+{_ROLE_EN}\s+(?:wants|plans|intends|aims|hopes)\b)", re.I,
)
_REPORT_ZH = re.compile(r"^(?P<speaker>[^，,;；。.!?！？：:]{1,40}?)(?:说|表示|建议|写道)(?=$|[:：，,\s“\"‘「]|我|他|她)")
_REPORT_EN = re.compile(r"^(?P<speaker>[^,;.!?]{1,60}?)\s+(?:said|says|suggested|wrote|say)\b", re.I)
# Token boundaries distinguish a quoted sentence from I'm/I've.
_QUOTE = re.compile(r"\"[^\"\n]*\"|“[^”]*”|「[^」]*」|‘[^’]*’|(?<!\w)'[^'\n]+'(?!\w)", re.S)
_BOUNDARY = re.compile(r"[。！？!?;；\n]|(?<!\d)\.(?!\d)")
_SENTENCE_BOUNDARY = re.compile(r"[。！？!?\n]|(?<!\d)\.(?!\d)")


def _foreign_report(text: str) -> bool:
    text = text.strip().rstrip("，,:：")
    zh, en = _REPORT_ZH.search(text), _REPORT_EN.search(text)
    if zh:
        speaker = zh['speaker'].strip()
        return ('我' not in speaker and '本人' not in speaker
                or bool(re.fullmatch(rf"我(?:的)?{_ROLE_ZH}", speaker))
                or speaker in {'助手', '助理'})
    if en:
        return re.search(r"\b(?:I|we)\b", en['speaker'], re.I) is None
    return False


def owner_assertion_error(raw: str, start: int, end: int) -> str | None:
    """Validate attribution and direct goal negation only, without rewriting.

    明确引用别人的话/助手提议不能成为本人目标；明确他人所有格也不能。
    否定只作用于目标动词，且同句没有另一个肯定目标从句时才拦；有肯定
    目标从句必须放行。条件、今天开始、问句、玩笑等语义交给模型。
    Context prevents selecting inside somebody else's quotation to erase its
    speaker; no context is copied into support. Existing typed Evidence checks
    still require a real, permission-eligible user turn, never assistant text.
    """
    boundaries = [m.end() for m in _BOUNDARY.finditer(raw)]
    left = max((p for p in boundaries if p <= start), default=0)
    right = next((p for p in boundaries if p >= end), len(raw))
    frame = raw[left:right].replace('’', "'")
    foreign_quotes = [quote for quote in _QUOTE.finditer(raw) if _foreign_report(raw[:quote.start()])]
    for quote in foreign_quotes:
        if quote.start() < end and start < quote.end():
            return 'owner_source_quotation'
    # A selected user declaration *after* a closed foreign quote has its own
    # speaker; the other person's preceding quote is validation context only.
    own_after_quote = any(
        left <= quote.start() and quote.end() <= start
        and not _foreign_report(raw[quote.end():start].lstrip('，,;； \n'))
        for quote in foreign_quotes
    )
    if _foreign_report(frame) and not own_after_quote:
        return 'owner_source_reported_speech'
    # Possessive must be the clause's subject, not an object in "my goal is
    # to support my daughter's goal". Check only clauses the support selects.
    clause_start = 0
    for clause_end, following in [*( (m.start(), m.end()) for m in _CLAUSES.finditer(frame)), (len(frame), len(frame))]:
        if left + clause_start < end and start < left + clause_end and _OTHER_GOAL.match(frame[clause_start:clause_end].strip()):
            return 'owner_source_other_goal_subject'
        clause_start = following
    # A semicolon can join the negative setup and a separate affirmative goal;
    # keep that complete sentence in the negation check, even for clause support.
    sentence_ends = [m.end() for m in _SENTENCE_BOUNDARY.finditer(raw)]
    goal_left = max((p for p in sentence_ends if p <= start), default=0)
    goal_right = next((p for p in sentence_ends if p >= end), len(raw))
    goal_frame = raw[goal_left:goal_right].replace('’', "'")
    if _NEGATED_GOAL.search(frame) and not any(
        _AFFIRMATIVE_GOAL.search(clause.strip()) for clause in _CLAUSES.split(goal_frame)
    ):
        return 'owner_source_negated_intention'
    return None
