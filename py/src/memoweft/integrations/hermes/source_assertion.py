"""Small bilingual source checks for owner goals and temporal state assertions.

These checks read user Evidence and its enclosing sentence, never the model's
proposition. They do not interpret arbitrary prose or invent a positive claim.
"""
from __future__ import annotations

import re

_INTENTION = re.compile(r'目标|计划|打算|准备|希望|愿望|以.+为目标|\b(?:goals?|plans?|planning|aim(?:ing)?|intend|intention|hope|wish|aspire)\b', re.I)
_SELF = re.compile(r'我|本人|咱|俺|\b(?:I|my|me|we|our)\b', re.I)
_QUESTION = re.compile(r'[?？]|是否|是不是|能否|可否|(?:吗|么|呢)[。！!]?\s*$|^(?:请问|难道)|^(?:should|shall|would|could|do|does|did|is|are|am|what|which|how)\b', re.I)
_NEGATED_INTENTION = re.compile(
    r'(?:不|没|没有)(?:再|这个)?(?:打算|计划|准备|目标)|不是(?:我|我们)的(?:目标|计划)'
    r'|\b(?:do|does|did)\s+not\s+(?:plan|intend|aim)\b'
    r'|\bnot\s+(?:planning|aiming|going to|(?:my|our) (?:goal|plan))\b|\bno\s+(?:plan|intention)\b', re.I,
)
_CONDITIONAL = re.compile(r'如果|要是|假如|\bif\b', re.I)
_HYPOTHETICAL = re.compile(r'要是|假如|如果[^。！？!?]*会|\bif\s+(?:I|we)\s+(?:were|had)\b', re.I)
_STATE = re.compile(r'住在|搬到|工作|伴侣|交往|\b(?:live|living|move|work|working|partner|partners|relationship)\b', re.I)
_SHORT_PLAN = re.compile(r'今晚|今夜|今天|明天|这一次|\b(?:tonight|today|tomorrow|this evening|this once)\b', re.I)
_NON_ASSERTION = re.compile(
    r'开玩笑|玩笑话|小说[里中]|还没同意|没有同意|^(?:请|帮我|替我)?翻译'
    r'|\b(?:just kidding|joking|in my novel|not agreed|not accepted)\b'
    r'|^(?:please\s+)?(?:draft|prepare|translate)\b', re.I,
)
_REPORTED = re.compile(r'^(?:(?!我|本人|咱|俺)[^，,。.!?！？]{1,30}(?:说|表示|提到|建议)|我的?(?:朋友|同事).{0,20}(?:说|表示|打算|计划))|^(?!(?:I|we)\b)[^,.!?]{1,40}\b(?:said|says|suggested)\b', re.I)
# ASCII single quotes need token boundaries: I'm/I've are not quotations.
_QUOTE = re.compile(r""""[^"\n]*"|“[^”]*”|「[^」]*」|‘[^’]*’|(?<!\w)'[^'\n]+'(?!\w)""", re.S)
_BOUNDARY = re.compile(r'[。！？!?\n]|(?<!\d)\.(?!\d)')


def owner_assertion_error(raw: str, start: int, end: int, *, transition: bool = False) -> str | None:
    """Reject non-assertions symmetrically in Chinese and English.

    问句/question、明确否定的意向/negated intention、假设/hypothetical、
    引文或虚构/quotation or fiction、未接受的建议/unaccepted advice 均不是本人
    当前目标；短期计划/one-off plans 也不能升级为长期偏好。普通否定偏好
    (不喜欢/dislike)和明确完成/放弃(completed/abandoned)仍是真实状态。
    Expand only the *validation frame*, never the selected support: clipping a
    quote or an if-clause cannot strip its speaker/modality. No source is copied.
    """
    boundaries = [m.end() for m in _BOUNDARY.finditer(raw)]
    left = max((p for p in boundaries if p <= start), default=0)
    right = next((p for p in boundaries if p >= end), len(raw))
    frame = raw[left:right].strip()
    if _QUESTION.search(frame):
        return 'owner_source_question'
    # Explicit counterfactual mood is enough even with no goal/state keywords;
    # ordinary conditional instructions retain their existing preference path.
    if _HYPOTHETICAL.search(frame):
        return 'owner_source_hypothetical'
    if any(m.start() < end and start < m.end()
           and (_SELF.search(m.group()) or _INTENTION.search(m.group()) or _STATE.search(m.group()))
           for m in _QUOTE.finditer(raw)):
        return 'owner_source_quotation'
    intention = bool(_INTENTION.search(frame))
    if intention or transition or _STATE.search(frame):
        if _CONDITIONAL.search(frame):
            return 'owner_source_hypothetical'
        if _NON_ASSERTION.search(frame):
            return 'owner_source_not_asserted'
        if _REPORTED.search(frame):
            return 'owner_source_reported_speech'
    if intention:
        if _NEGATED_INTENTION.search(frame):
            return 'owner_source_negated_intention'
        if not _SELF.search(frame):
            return 'owner_source_not_self'
        if _SHORT_PLAN.search(frame):
            return 'owner_source_one_off_plan'
    return None
