"""Deterministic distillation & retrieval of AI self-commitments, recommendations, and agreements.

Zero GPU, deterministic rules run on CPU (<1ms).
"""
from __future__ import annotations

import re
import sqlite3
from typing import Mapping, Sequence

from ...store.interaction_commitment import (
    CommitmentKind,
    InteractionCommitment,
    SqliteInteractionCommitmentStore,
)

# Regex patterns for Chinese & English distillation
_RECOMMEND_PATTERNS = [
    re.compile(r"(?:我(?:个人)?(?:建议|推荐)|我的建议是|建议(?:你|您)?|可以考虑(?:采用|选用|使用)?)[，,：:\s]*([^\n，。！？!?；;]{2,25})"),
    re.compile(r"(?:I recommend|my recommendation is|suggest(?:ing)? using|could consider)\s+([^\n,\.!\?;]{2,35})", re.I),
]

_COMMIT_PATTERNS = [
    re.compile(r"(?:我(?:会|将在|答应你|答应为您)|后续我会|下次我会|我会(?:帮你|为你))[，,：:\s]*([^\n，。！？!?；;]{2,25})"),
    re.compile(r"(?:I will remind you|I promise to|I'll follow up on)\s+([^\n,\.!\?;]{2,35})", re.I),
]

_AGREE_PATTERNS = [
    re.compile(r"(?:双方达成一致|达成共识|一致决定|决定采用|就按)[，,：:\s]*([^\n，。！？!?；;]{2,25})"),
    re.compile(r"(?:we agreed to|decided to adopt|consensus reached to)\s+([^\n,\.!\?;]{2,35})", re.I),
]

MAX_PROPOSITION_LEN = 20


def _clean_proposition(prefix: str, raw_stem: str) -> str:
    cleaned = raw_stem.strip().rstrip("，,。！？!?；; ")
    combined = f"{prefix}{cleaned}"
    if len(combined) > MAX_PROPOSITION_LEN:
        combined = combined[:MAX_PROPOSITION_LEN]
    return combined


def distill_commitments_from_messages(
    messages: Sequence[Mapping[str, object]],
) -> list[tuple[CommitmentKind, str, str]]:
    """Extract (kind, clean_content, raw_quote) from turns."""
    results: list[tuple[CommitmentKind, str, str]] = []
    seen: set[str] = set()

    for msg in messages:
        role = msg.get("role")
        if role != "assistant":
            continue
        content = str(msg.get("content") or "").strip()
        if not content:
            continue

        # Check recommendations
        for pat in _RECOMMEND_PATTERNS:
            for m in pat.finditer(content):
                stem = m.group(1).strip()
                prop = _clean_proposition("AI建议", stem)
                if prop not in seen and len(stem) >= 2:
                    seen.add(prop)
                    results.append(("recommendation", prop, m.group(0).strip()))

        # Check commitments
        for pat in _COMMIT_PATTERNS:
            for m in pat.finditer(content):
                stem = m.group(1).strip()
                prop = _clean_proposition("AI承诺", stem)
                if prop not in seen and len(stem) >= 2:
                    seen.add(prop)
                    results.append(("commitment", prop, m.group(0).strip()))

        # Check agreements
        for pat in _AGREE_PATTERNS:
            for m in pat.finditer(content):
                stem = m.group(1).strip()
                prop = _clean_proposition("双方决定", stem)
                if prop not in seen and len(stem) >= 2:
                    seen.add(prop)
                    results.append(("agreement", prop, m.group(0).strip()))

    return results


def record_commitments_for_episode(
    db: sqlite3.Connection,
    *,
    subject_id: str,
    conversation_id: str,
    episode_id: str,
    messages: Sequence[Mapping[str, object]],
) -> list[InteractionCommitment]:
    store = SqliteInteractionCommitmentStore(db)
    recorded: list[InteractionCommitment] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        raw_message_id = message.get("message_id")
        if raw_message_id is None:
            raw_message_id = message.get("platform_message_id")
        assistant_message_id = (
            str(raw_message_id) if raw_message_id is not None else None
        )
        for kind, content, raw_quote in distill_commitments_from_messages([message]):
            item = store.record(
                subject_id=subject_id,
                conversation_id=conversation_id,
                episode_id=episode_id,
                kind=kind,
                content=content,
                raw_quote=raw_quote,
                assistant_message_id=assistant_message_id,
            )
            recorded.append(item)
    return recorded


def query_matching_commitments(
    db: sqlite3.Connection,
    *,
    subject_id: str,
    query: str,
    conversation_id: str = "",
) -> list[InteractionCommitment]:
    store = SqliteInteractionCommitmentStore(db)
    all_active = store.query(subject_id, status="active")
    if not all_active:
        return []

    q_lower = query.casefold()
    is_general_inquiry = any(word in q_lower for word in (
        "建议", "推荐", "承诺", "答应", "共识", "约定", "决定", "之前说的", "你说过", "提议",
        "recommend", "promise", "agree", "suggest"
    ))

    matched: list[InteractionCommitment] = []
    for item in all_active:
        c_lower = item.content.casefold()
        q_stem = item.raw_quote.casefold()
        if is_general_inquiry or any(char in q_lower for char in c_lower if char not in "AI建议承诺双方决定"):
            overlap = set(c_lower) & set(q_lower)
            if len(overlap) >= 2 or is_general_inquiry:
                matched.append(item)

    return matched[:5]
