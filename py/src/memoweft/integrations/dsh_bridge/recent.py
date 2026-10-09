"""Read-only, bounded bridge from accepted user Evidence until formation settles.

This is deliberately separate from formal World recall: no synthesized claims,
no assistant prose, no new storage, and no promotion of Evidence to World items.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import sqlite3
from typing import Any

from ...clock import to_iso_z
from ...types import ModelTier
from ..hermes.batch_adapter import _has_declarative_facts
from ..hermes.recall import match_cognitions, _GENERIC_PREDICATE_BIGRAMS
from ..trust.currentness import evidence_state
from .interactions import _model_eligible, _topic_tokens

_CORRECTION = re.compile(r"说错|更正|纠正|不对|改成|改为|原来.*停用|\b(?:correction|instead|actually)\b", re.I)
_GENERIC = _GENERIC_PREDICATE_BIGRAMS | {"以后", "之后", "现在", "最近", "一直", "以后按", "刚才", "原来", "还是", "应该", "以后给", "以后请"}


def _reference_tokens(text: str) -> set[str]:
    # Numeric values such as ratios carry a correction's subject across sessions
    # even though the shared prose tokenizer intentionally drops short digits.
    return (_topic_tokens(text) - _GENERIC) | set(re.findall(r"\d+(?:[比:：/]\d+)+", text))


def _related(query: str, text: str) -> bool:
    if match_cognitions(query, [{"id": "raw", "content": text, "confidence": 600}]):
        return True
    # Short literal names/topics must survive a long surrounding sentence. Only
    # informative tokens participate; greetings have no matching topic.
    return bool((_topic_tokens(query) & _topic_tokens(text)) - _GENERIC)


def recent_evidence(
    db: sqlite3.Connection, subject_id: str, query: str, *, model_tier: ModelTier,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    cutoff = to_iso_z((now or datetime.now(timezone.utc)) - timedelta(hours=24))
    jobs = db.execute(
        "SELECT j.* FROM memory_world_job j WHERE j.subject_id = ? AND j.created_at >= ? "
        "AND EXISTS (SELECT 1 FROM interaction_context c WHERE c.episode_id = j.boundary_event_id "
        "AND c.subject_id = j.subject_id) ORDER BY j.created_at DESC, j.rowid DESC LIMIT 32",
        (subject_id, cutoff),
    ).fetchall()
    rows: list[dict[str, Any]] = []
    for job in reversed(jobs):
        if not _model_eligible(db, subject_id, str(job["boundary_event_id"]), model_tier):
            continue
        for evidence_id in json.loads(str(job["evidence_ids_json"])):
            evidence = db.execute("SELECT * FROM evidence WHERE id = ? AND subject_id = ?", (evidence_id, subject_id)).fetchone()
            if evidence is None or evidence_state(dict(evidence), surface="formation", model_tier=model_tier) is not None:
                continue
            text = str(evidence["raw_content"])
            if not _has_declarative_facts(text):
                continue
            outcome = json.loads(str(job["world_result_json"] or "{}"))
            rows.append({"id": str(evidence_id), "text": text, "session_id": str(job["parent_session_id"]),
                         "created_at": str(job["created_at"]), "state": str(job["state"]), "reason": outcome.get("reason")})
    selected: list[dict[str, Any]] = []
    chars = 0
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        if row["state"] not in {"pending", "processing", "retry", "no_change", "dead"}:
            continue
        if row["state"] == "no_change" and row["reason"] in {
            "task_scoped_instruction", "pure_inquiry_no_declarative_facts", "no_world_mutation",
        }:
            continue
        context = None
        if _CORRECTION.search(row["text"]):
            # A short correction can inherit its subject from its own previous
            # turn, or an explicitly repeated old value in another conversation.
            context = next((prior for prior in reversed(rows[:index])
                            if prior["session_id"] == row["session_id"]), None)
            if context is None:
                candidates = {prior["text"]: prior for prior in rows[:index]
                              if _reference_tokens(prior["text"]) & _reference_tokens(row["text"])}
                if len(candidates) == 1:
                    context = next(iter(candidates.values()))
        if not _related(query, row["text"]) and not (context and _related(query, context["text"])):
            continue
        item = {key: row[key] for key in ("id", "text", "session_id", "created_at")}
        if context:
            item["preceding_text"] = context["text"]
            item["preceding_evidence_id"] = context["id"]
        size = len(row["text"]) + (len(context["text"]) if context else 0)
        if chars + size > 800:
            continue  # Never truncate a quote into a different assertion.
        selected.append(item)
        chars += size
        if len(selected) == 4:
            break
    return list(reversed(selected))
