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

_CORRECTION = re.compile(r"说错|写错|报大|多报|更正|纠正|不对|改成|改为|改到|作废|以.{0,24}为准|原来.*停用|\b(?:correction|instead|actually)\b", re.I)
_GENERIC = _GENERIC_PREDICATE_BIGRAMS | {"以后", "之后", "现在", "最近", "一直", "以后按", "刚才", "原来", "还是", "应该", "以后给", "以后请"}
_QUANTITY = re.compile(r"\d+(?:\.\d+)?\s*(毫升|升|毫克|克|千克|公斤|毫米|厘米|米|公里|元|美元|秒|分钟|小时|点|时|ml\b|kg\b|cm\b|km\b)", re.I)
_DIMENSIONS = {"毫升": "volume", "升": "volume", "ml": "volume", "毫克": "mass", "克": "mass", "千克": "mass", "公斤": "mass", "kg": "mass", "毫米": "length", "厘米": "length", "米": "length", "公里": "length", "cm": "length", "km": "length", "点": "clock", "时": "clock", "秒": "duration", "分钟": "duration", "小时": "duration"}
_ANAPHOR = re.compile(r"前面那个|上一句|刚才说的|那个(?:数|时间|价格)|\b(?:that number|that time|previous price)\b", re.I)


def _dimensions(text: str) -> set[str]:
    return {_DIMENSIONS.get(unit.lower(), unit.lower()) for unit in _QUANTITY.findall(text)}


def correction_candidates(rows: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    """Only adjacent, readable source quotes; never pick one of competing topics."""
    row = rows[index]
    if len(row["text"]) > 240 or not _CORRECTION.search(row["text"]):
        return []
    current_at = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
    prior = [r for r in rows[:index] if 0 <= (current_at - datetime.fromisoformat(
        str(r["created_at"]).replace("Z", "+00:00"))).total_seconds() <= 300
        and row["turn_index"] - r["turn_index"] <= 4]
    same_session = [r for r in prior if r["session_id"] == row["session_id"]]
    dimensions = _dimensions(row["text"])
    if same_session and not dimensions:
        return same_session[-1:]
    quantities = {re.sub(r"\s+", "", m.group()).lower() for m in _QUANTITY.finditer(row["text"])}
    candidates = [r for r in prior if not _CORRECTION.search(r["text"]) and (
        bool(dimensions & _dimensions(r["text"])) and (r["session_id"] == row["session_id"] or bool(_ANAPHOR.search(row["text"])) or
            bool(quantities & {re.sub(r"\s+", "", m.group()).lower() for m in _QUANTITY.finditer(r["text"])})) if dimensions else
        bool(_reference_tokens(r["text"]) & _reference_tokens(row["text"])))]
    return list({r["text"]: r for r in candidates}.values())


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
    for turn_index, job in enumerate(reversed(jobs)):
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
                         "created_at": str(job["created_at"]), "state": str(job["state"]), "reason": outcome.get("reason"),
                         "turn_index": turn_index})
    selected: list[dict[str, Any]] = []
    grouped: set[str] = set()
    chars = 0
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        if row["id"] in grouped:
            continue
        if row["state"] not in {"pending", "processing", "retry", "no_change", "dead"}:
            continue
        if row["state"] == "no_change" and row["reason"] in {
            "task_scoped_instruction", "pure_inquiry_no_declarative_facts", "no_world_mutation",
        }:
            continue
        candidates = correction_candidates(rows, index)
        if not _related(query, row["text"]) and not any(_related(query, r["text"]) for r in candidates):
            continue
        item = {key: row[key] for key in ("id", "text", "session_id", "created_at")}
        if len(candidates) == 1:
            context = candidates[0]
            item["preceding_text"] = context["text"]
            item["preceding_evidence_id"] = context["id"]
            item["correction_status"] = "certain"
        elif candidates:
            item["correction_status"] = "ambiguous"
            item["preceding_candidates"] = [{"id": r["id"], "text": r["text"]} for r in candidates]
        size = len(row["text"]) + sum(len(r["text"]) for r in candidates)
        # If the whole correction group cannot fit, do not later emit its old
        # source alone and thereby affirm a value whose correction was dropped.
        grouped.update(r["id"] for r in candidates)
        if chars + size > 800:
            continue  # Never truncate a quote into a different assertion.
        selected.append(item)
        chars += size
        if len(selected) == 4:
            break
    return list(reversed(selected))
