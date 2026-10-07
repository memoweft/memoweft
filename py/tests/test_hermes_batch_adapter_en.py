"""English rule-localization (Owner §4.12 option B): compiler normalization,
prompt routing, confirmed line, span repair.  Chinese behavior is pinned
unchanged by the full hermes suite.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, cast

from memoweft.integrations.hermes.batch_adapter import (
    HermesBatchAdapterProcessor,
    _SYSTEM_PROMPT,
    _SYSTEM_PROMPT_EN,
    _confirm_normalize,
    _stated_normalize,
    _stated_normalize_no_subject_prepend,
)
from memoweft.integrations.hermes.world_worker import WorldJobWorker

from test_hermes_world_worker import (
    MutableClock,
    _initialize_database,
    _insert_job,
    _job,
    _policy,
)

_RAW = "I like jasmine tea"


def _capture_route(
    script: list[Any], capture: list[list[dict[str, str]]] | None
) -> Callable[..., dict[str, object]]:
    def route(
        messages: list[dict[str, str]], session_id: str
    ) -> dict[str, object]:
        if capture is not None:
            capture.append(messages)
        return cast(dict[str, object], script.pop(0))

    return route


def _set_evidence(
    db_path: Path, evidence_id: str, raw: str, context: str | None = None
) -> None:
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        db.execute(
            "UPDATE evidence SET raw_content = ?, preceding_ai_context = ? "
            "WHERE id = ?",
            (raw, context, evidence_id),
        )
        db.execute(
            "UPDATE boundary_evidence_content SET raw_content_hash = ? "
            "WHERE evidence_id = ?",
            (hashlib.sha256(raw.encode("utf-8")).hexdigest(), evidence_id),
        )
    finally:
        db.close()


def _run(
    db_path: Path,
    clock: MutableClock,
    script: list[Any],
    evidence_ids: tuple[str, ...] = ("evidence-1",),
    setup: Callable[[Path], None] | None = None,
    lang: str | None = None,
    capture: list[list[dict[str, str]]] | None = None,
) -> None:
    _initialize_database(db_path)
    _insert_job(db_path, clock, evidence_ids=evidence_ids)
    if setup is not None:
        setup(db_path)
    processor = HermesBatchAdapterProcessor(
        str(db_path), _capture_route(script, capture), clock=clock, lang=lang
    )
    worker = WorldJobWorker(
        db_path, processor=processor, policy=_policy(), clock=clock
    )
    assert worker.run_until_quiescent() == 1


def _v8(*items: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 8, "result": "cognitions", "cognitions": list(items)}


def _form(
    proposition: str,
    *spans: tuple[int, int],
    kind: str = "preference",
    formed_by: str = "stated",
    evidence_id: str = "evidence-1",
    assistant_claim: str | None = None,
    entity: dict[str, object] | None = None,
    target_entity: dict[str, object] | None = None,
    relation_type: str | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {
        "action": "form",
        "target": "owner_self",
        "statement_kind": kind,
        "formed_by": formed_by,
        "proposition": proposition,
        "supports": [
            {"evidence_id": evidence_id, "start": s, "end": e} for s, e in spans
        ],
    }
    if assistant_claim is not None:
        item["assistant_claim"] = assistant_claim
    if entity is not None:
        item["entity"] = entity
    if target_entity is not None:
        item["target_entity"] = target_entity
    if relation_type is not None:
        item["relation_type"] = relation_type
    return item


# ── compiler normalization ──────────────────────────────────────────────────


def test_english_stated_normalization() -> None:
    # English contract: verbatim — the user's exact words anchor the memory.
    assert _stated_normalize("I like jasmine tea") == "I like jasmine tea"
    assert _stated_normalize("We prefer tea") == "We prefer tea"
    assert _stated_normalize("my cat is called Erwu") == "my cat is called Erwu"
    assert _stated_normalize("like jasmine tea") == "like jasmine tea"
    # Chinese path is byte-identical to the legacy contract.
    assert _stated_normalize("我喜欢喝茉莉花茶") == "用户喜欢喝茉莉花茶"
    assert _stated_normalize("喜欢喝茉莉花茶") == "用户喜欢喝茉莉花茶"


def test_english_no_subject_prepend_normalization() -> None:
    assert (
        _stated_normalize_no_subject_prepend("my cat is called Erwu")
        == "my cat is called Erwu"
    )
    assert _stated_normalize_no_subject_prepend("Erwu is my cat") == "Erwu is my cat"


def test_english_confirm_normalization() -> None:
    assert _confirm_normalize("You like jasmine tea?") == "You like jasmine tea"
    assert _confirm_normalize("Your car is a Xpeng") == "Your car is a Xpeng"


# ── end-to-end formation in English ─────────────────────────────────────────


def test_english_stated_preference_forms(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script: list[Any] = [
        {
            "content": json.dumps(
                _v8(_form("I like jasmine tea", (0, len(_RAW))))
            ),
            "model": "deepseek-v4-flash",
        }
    ]
    _run(db_path, clock, script, setup=lambda p: _set_evidence(p, "evidence-1", _RAW))
    row = _job(db_path)
    assert row["state"] == "applied"
    db = sqlite3.connect(db_path)
    try:
        content, confidence = db.execute(
            "SELECT content, confidence FROM cognition WHERE subject_id = 'owner'"
        ).fetchone()
        assert content == "I like jasmine tea"
        assert confidence == 600
    finally:
        db.close()


def test_english_naming_no_subject_prepend(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw = "My cat is called Erwu"
    script: list[Any] = [
        {
            "content": json.dumps(
                _v8(
                    _form(
                        "My cat is called Erwu",
                        (0, len(raw)),
                        kind="naming",
                        entity={"canonical_name": "Erwu", "kind": "animal"},
                    )
                )
            ),
            "model": "deepseek-v4-flash",
        }
    ]
    _run(db_path, clock, script, setup=lambda p: _set_evidence(p, "evidence-1", raw))
    row = _job(db_path)
    assert row["state"] == "applied"
    db = sqlite3.connect(db_path)
    try:
        assert db.execute(
            "SELECT content_type FROM cognition WHERE subject_id = 'owner'"
        ).fetchone()[0] == "naming"
        assert db.execute(
            "SELECT canonical_name FROM entity WHERE world_id = 'owner'"
        ).fetchone()[0] == "Erwu"
    finally:
        db.close()


def test_english_confirmed_forms_at_280(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw = "Yes"
    script: list[Any] = [
        {
            "content": json.dumps(
                _v8(
                    _form(
                        "You like jasmine tea",
                        (0, len(raw)),
                        formed_by="confirmed",
                        assistant_claim="You like jasmine tea?",
                    )
                )
            ),
            "model": "deepseek-v4-flash",
        }
    ]
    _run(
        db_path,
        clock,
        script,
        setup=lambda p: _set_evidence(
            p, "evidence-1", raw, context="You like jasmine tea?"
        ),
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    db = sqlite3.connect(db_path)
    try:
        formed_by, confidence = db.execute(
            "SELECT formed_by, confidence FROM cognition WHERE subject_id = 'owner'"
        ).fetchone()
        assert formed_by == "confirmed"
        assert confidence == 280
    finally:
        db.close()


def test_english_span_repair_recovers_off_by_one(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script: list[Any] = [
        {
            "content": json.dumps(
                _v8(_form("I like jasmine tea", (0, len(_RAW) - 1)))
            ),
            "model": "deepseek-v4-flash",
        }
    ]
    _run(db_path, clock, script, setup=lambda p: _set_evidence(p, "evidence-1", _RAW))
    row = _job(db_path)
    assert row["state"] == "applied"


# ── prompt language routing ────────────────────────────────────────────────


def _noop_script() -> list[Any]:
    return [
        {
            "content": json.dumps(_v8(_form("I like jasmine tea", (0, len(_RAW))))),
            "model": "deepseek-v4-flash",
        }
    ]


def test_english_boundary_gets_english_prompt(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    capture: list[list[dict[str, str]]] = []
    _run(
        db_path,
        clock,
        _noop_script(),
        setup=lambda p: _set_evidence(p, "evidence-1", _RAW),
        capture=capture,
    )
    assert capture and capture[0][0]["content"] == _SYSTEM_PROMPT_EN


def test_chinese_boundary_keeps_legacy_prompt(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    capture: list[list[dict[str, str]]] = []
    script: list[Any] = [
        {
            "content": json.dumps(
                _v8(_form("用户喜欢喝茉莉花茶", (0, 8)))
            ),
            "model": "deepseek-v4-flash",
        }
    ]
    _run(
        db_path,
        clock,
        script,
        setup=lambda p: _set_evidence(p, "evidence-1", "我喜欢喝茉莉花茶"),
        capture=capture,
    )
    assert capture and capture[0][0]["content"] == _SYSTEM_PROMPT


def test_explicit_lang_pin_overrides_auto_detection(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    capture: list[list[dict[str, str]]] = []
    _run(
        db_path,
        clock,
        _noop_script(),
        setup=lambda p: _set_evidence(p, "evidence-1", _RAW),
        lang="zh",
        capture=capture,
    )
    assert capture and capture[0][0]["content"] == _SYSTEM_PROMPT


def test_invalid_lang_is_rejected(tmp_path: Path) -> None:
    import pytest

    db_path = tmp_path / "memoweft.sqlite3"
    with pytest.raises(ValueError):
        HermesBatchAdapterProcessor(str(db_path), _capture_route([], None), lang="fr")
