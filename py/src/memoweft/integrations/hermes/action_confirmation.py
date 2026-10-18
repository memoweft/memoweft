"""Closed grammar for action-directed assent and its selected proposal."""
from __future__ import annotations

import re


_ACTION_ASSENT = re.compile(
    r"好(?:的|呀|啊|嘞)?|(?:好|好的|行|可以)?(?:就这么(?:做|办)|就这样定了)"
    r"|(?:(?:yes|yeah|yep|yup|sure|ok|okay))?(?:dothat|goahead)", re.I,
)
_EN_HEAD = re.compile(
    r"(?:(?:I|we)\s+(?:can|could|will|shall)\s+|please\s+)?"
    r"(?:remind|save|show|keep|order)\b\s+\S", re.I,
)
_ZH_HEAD = re.compile(
    r"(?:(?:我|我们|助手)(?:可以|能|会|来)?|请)?"
    r"(?:提醒|保存|展示|显示|保持|点|给(?:你|您|用户)看)\S",
)
_EN_CONDITION = re.compile(r"^(?:when|whenever|if|next time)\b[^,，.!?。！？;；\n]+[,，]\s*", re.I)
_ZH_CONDITION = re.compile(r"^(?:以后|今后|下次|将来)[^，,。！？!?;；\n]+?时[，,]?\s*")
# Existing recall-copy contracts allow a verbatim quoted-source annotation
# after a proposal. This attribution is not another independent owner claim.
_QUOTED_NOTE = re.compile(r"\s+The quoted (?:note|text|message|source) says\s+", re.I)
_QUOTE = re.compile(r'"[^"\n]*"|“[^”]*”|「[^」]*」|‘[^’]*’')


def is_action_assent(text: str) -> bool:
    compact = re.sub(r'[\s，。！？!?,.:;；、~～「」『』]+', '', text)
    return _ACTION_ASSENT.fullmatch(compact) is not None


def is_selected_action_proposal(claim: str) -> bool:
    """Require an action head in THIS selection, not elsewhere in its turn.

    Closed heads: assistant can/could/will/shall + remind/save/show/keep/order;
    direct imperatives of those verbs; Chinese corresponding explicit heads.
    A when/if or 以后…时 condition may precede the head. Mere occurrences of
    action words, reported/quoted offers, and factual prefixes do not qualify.
    Reject other independent clauses rather than laundering a factual guess
    alongside an action. Unrecognized wording stays outside this finite rule.
    """
    text = _QUOTED_NOTE.split(claim.strip(), maxsplit=1)[0].rstrip('。.!！?？ ')
    # Mask quotations only for clause-boundary validation; the stored source
    # and proposed action remain verbatim, including any quoted arguments.
    masked = _QUOTE.sub(lambda match: ' ' * len(match[0]), text)
    if re.search(r'[.!?。！？;；\n]', masked):
        return False
    condition = _EN_CONDITION.match(text) or _ZH_CONDITION.match(text)
    action = text[condition.end():] if condition else text
    masked_action = _QUOTE.sub(lambda match: ' ' * len(match[0]), action)
    if re.search(r'[,，]', masked_action):
        return False
    return (_EN_HEAD.match(action) or _ZH_HEAD.match(action)) is not None
