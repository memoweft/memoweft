from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import pytest

from memoweft.integrations.hermes.batch_adapter import (
    _SYSTEM_PROMPT,
    _SYSTEM_PROMPT_EN,
    entity_id_for,
)
from memoweft.store.interaction_context import SqliteInteractionContextStore
from memoweft.types import InteractionContext, VisibleTurn

from test_hermes_batch_adapter_v5 import _batch, _model, _run, _set_evidence
from test_hermes_world_worker import MutableClock, _insert_job, _job


def _item(kind: str, proposition: str, quote: str, *, entity: str) -> dict[str, object]:
    return {
        "action": "form",
        "target": "owner_self",
        "statement_kind": kind,
        "formed_by": "stated",
        "proposition": proposition,
        "entity": {"canonical_name": entity, "kind": "person"},
        "supports": [{"evidence_id": "evidence-1", "quote": quote}],
    }


def test_v8_prompt_matches_segment_and_third_party_preference_contract() -> None:
    assert "第三方**身份类稳定属性**" not in _SYSTEM_PROMPT
    assert "第三方稳定属性或偏好" in _SYSTEM_PROMPT
    assert "proposition只是必填占位" in _SYSTEM_PROMPT
    assert "同一Evidence前段" in _SYSTEM_PROMPT
    assert "identity-class stable attributes" not in _SYSTEM_PROMPT_EN
    assert "third-party attributes or preferences" in _SYSTEM_PROMPT_EN
    assert "proposition is only a required placeholder" in _SYSTEM_PROMPT_EN
    assert "earlier segment in the same Evidence" in _SYSTEM_PROMPT_EN


def test_quote_derives_exact_span_and_stated_proposition_for_person_naming(
    tmp_path: Path,
) -> None:
    path = tmp_path / "quote-naming.sqlite3"
    raw = "我有个特别好的朋友叫我们就叫他彦吧，他最近帮了我调试程序"
    output = _item("naming", "用户有个特别好的朋友叫彦", raw, entity="彦")
    output["supports"] = [{"evidence_id": "evidence-1", "segment_id": "s0"}]
    _run(
        path,
        MutableClock(),
        [_model(_batch(output))],
        ("evidence-1",),
        lambda db_path: _set_evidence(db_path, "evidence-1", raw),
    )

    assert _job(path)["state"] == "applied"
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT canonical_name FROM entity WHERE id = ?",
            (entity_id_for("owner", "彦"),),
        ).fetchone()[0] == "彦"
        cognition = db.execute(
            "SELECT content FROM cognition WHERE content_type = 'naming'"
        ).fetchone()[0]
        first_segment = raw[: raw.index("，") + 1]
        assert cognition == "用户" + first_segment[1:]
        span = json.loads(
            db.execute(
                "SELECT payload_json FROM evidence_ledger "
                "WHERE content LIKE '%cognition_id%' ORDER BY rowid LIMIT 1"
            ).fetchone()[0]
        )
        assert (span["start"], span["end"]) == (0, len(first_segment))


@pytest.mark.parametrize(
    ("raw", "model_name", "expected_name"),
    (
        ("我有个朋友，我们就叫他阿洛吧。", "阿洛吧", "阿洛"),
        ("我朋友明确说名字是“阿洛吧”。", "阿洛吧", "阿洛吧"),
    ),
)
def test_spoken_name_particle_normalization_requires_unquoted_naming_context(
    tmp_path: Path, raw: str, model_name: str, expected_name: str,
) -> None:
    path = tmp_path / (expected_name + ".sqlite3")
    output = _item("naming", "ignored", raw, entity=model_name)
    output["supports"] = [
        {"evidence_id": "evidence-1", "segment_id": "s1" if "，" in raw else "s0"}
    ]
    _run(
        path, MutableClock(), [_model(_batch(output))], ("evidence-1",),
        lambda db_path: _set_evidence(db_path, "evidence-1", raw),
    )
    assert _job(path)["state"] == "applied"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT canonical_name FROM entity").fetchone()[0] == expected_name


def test_quote_rejects_ambiguous_occurrence(tmp_path: Path) -> None:
    path = tmp_path / "ambiguous-quote.sqlite3"
    raw = "阿洛喜欢玉米，也会买玉米"
    output = _item("preference", "阿洛喜欢玉米", "玉米", entity="阿洛")
    _run(
        path,
        MutableClock(),
        [_model(_batch(output))],
        ("evidence-1",),
        lambda db_path: _set_evidence(db_path, "evidence-1", raw),
    )
    row = _job(path)
    assert row["state"] == "no_change"
    assert json.loads(str(row["world_result_json"]))["reason"] == "support_quote_ambiguous"


def test_third_party_preference_targets_person_not_owner(tmp_path: Path) -> None:
    path = tmp_path / "third-party-preference.sqlite3"
    raw = "我的朋友叫Alex，他很喜欢看天文杂志"
    naming = _item("naming", "概括不可信", raw, entity="Alex")
    naming["supports"] = [{"evidence_id": "evidence-1", "segment_id": "s0"}]
    preference = _item("preference", "概括不可信", raw, entity="Alex")
    preference["supports"] = [{"evidence_id": "evidence-1", "segment_id": "s1"}]
    _run(
        path,
        MutableClock(),
        [_model(_batch(naming, preference))],
        ("evidence-1",),
        lambda db_path: _set_evidence(db_path, "evidence-1", raw),
    )
    assert _job(path)["state"] == "applied"
    with sqlite3.connect(path) as db:
        target = db.execute(
            "SELECT target_entity_id FROM cognition_target ct JOIN cognition c "
            "ON c.id = ct.cognition_id WHERE c.content_type = 'preference'"
        ).fetchone()[0]
        assert target == entity_id_for("owner", "Alex")


@pytest.mark.parametrize(
    ("mode", "expected_state"),
    (("prior", "applied"), ("current", "applied"), ("ambiguous", "no_change")),
)
def test_pronoun_reference_uses_only_prior_context_before_evidence_time(
    tmp_path: Path, mode: str, expected_state: str,
) -> None:
    path = tmp_path / "pronoun-context.sqlite3"
    ambiguous = mode == "ambiguous"
    raw = (
        "最近阿洛不帮忙了，他现在不喜欢看星图"
        if mode == "current"
        else "他现在很喜欢看星图"
    )

    def setup(db_path: Path) -> None:
        _set_evidence(db_path, "evidence-1", raw)
        _insert_job(
            db_path, MutableClock(), job_id="job-prior", evidence_ids=("evidence-2",)
        )
        with sqlite3.connect(db_path) as db:
            db.execute(
                "UPDATE evidence SET raw_content = '我的朋友叫阿洛', summary = '我的朋友叫阿洛' "
                "WHERE id = 'evidence-2'"
            )
            db.execute(
                "UPDATE memory_world_job SET state = 'no_change', completed_at = "
                "'2020-01-01T00:00:00.000Z' WHERE job_id = 'job-prior'"
            )
            db.execute(
                "INSERT INTO entity (id, world_id, kind, canonical_name, created_at, updated_at, aliases_json) "
                "VALUES (?, 'owner', 'person', '阿洛', '2020-01-01T00:00:00.000Z', "
                "'2020-01-01T00:00:00.000Z', '[]')",
                (entity_id_for("owner", "阿洛"),),
            )
            if ambiguous:
                db.execute(
                    "INSERT INTO entity (id, world_id, kind, canonical_name, created_at, updated_at, aliases_json) "
                    "VALUES (?, 'owner', 'person', '彦', '2020-01-01T00:00:00.000Z', "
                    "'2020-01-01T00:00:00.000Z', '[]')",
                    (entity_id_for("owner", "彦"),),
                )
            db.execute(
                "INSERT INTO cognition (id, subject_id, content, content_type, formed_by, "
                "confidence, cred_status, created_at, updated_at) VALUES "
                "('naming-alo', 'owner', '我的朋友叫阿洛', 'naming', 'stated', 600, "
                "'limited', '2020-01-01T00:00:00.000Z', '2020-01-01T00:00:00.000Z')"
            )
            db.execute(
                "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) "
                "VALUES ('naming-alo', 'evidence-2', 'support')"
            )
            db.execute(
                "INSERT INTO cognition_target (cognition_id, target_entity_id, perspective_entity_id) "
                "VALUES ('naming-alo', ?, NULL)",
                (entity_id_for("owner", "阿洛"),),
            )
            if ambiguous:
                db.execute(
                    "INSERT INTO cognition (id, subject_id, content, content_type, formed_by, "
                    "confidence, cred_status, created_at, updated_at) VALUES "
                    "('naming-yan', 'owner', '也认识彦', 'naming', 'stated', 600, "
                    "'limited', '2020-01-01T00:00:00.000Z', '2020-01-01T00:00:00.000Z')"
                )
                db.execute(
                    "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) "
                    "VALUES ('naming-yan', 'evidence-2', 'support')"
                )
                db.execute(
                    "INSERT INTO cognition_target (cognition_id, target_entity_id, perspective_entity_id) "
                    "VALUES ('naming-yan', ?, NULL)",
                    (entity_id_for("owner", "彦"),),
                )
            store = SqliteInteractionContextStore(db)
            store.insert(
                InteractionContext(
                    "prior", "owner", "session-parent", "boundary-job-prior",
                    [
                        VisibleTurn(
                            "user",
                            "我的朋友叫阿洛，也认识彦"
                            if ambiguous
                            else ("今天聊别的" if mode == "current" else "我的朋友叫阿洛"),
                        ),
                        VisibleTurn("assistant", "我建议给他买一本杂志"),
                    ], "prior-hash",
                    "2020-01-01T00:00:00.000Z",
                )
            )
            store.insert(
                InteractionContext(
                    "future", "owner", "session-parent", "future-episode",
                    [VisibleTurn("user", "后来又认识了彦")], "future-hash",
                    "2099-01-01T00:00:00.000Z",
                )
            )

    output = _item(
        "preference", "改写", raw,
        entity="阿洛吧" if mode == "prior" else "阿洛",
    )
    if mode == "current":
        output["supports"] = [{"evidence_id": "evidence-1", "segment_id": "s1"}]
    if ambiguous:
        output["entity_reference"] = {"mention": "他"}
    _run(path, MutableClock(), [_model(_batch(output))], ("evidence-1",), setup)
    row = _job(path)
    assert row["state"] == expected_state
    if ambiguous:
        assert json.loads(str(row["world_result_json"]))["reason"] == "ambiguous_entity_reference"
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM evidence WHERE raw_content LIKE '%建议给他%'"
        ).fetchone()[0] == 0


def test_third_party_preference_correction_keeps_the_same_person_target(
    tmp_path: Path,
) -> None:
    path = tmp_path / "third-party-preference-correction.sqlite3"
    first = "Alex喜欢看星图"
    formed = _item("preference", "ignored", first, entity="Alex")
    formed["supports"] = [{"evidence_id": "evidence-1", "segment_id": "s0"}]
    _run(
        path, MutableClock(), [_model(_batch(formed))], ("evidence-1",),
        lambda db_path: _set_evidence(db_path, "evidence-1", first),
    )
    with sqlite3.connect(path) as db:
        prior_id = db.execute(
            "SELECT id FROM cognition WHERE content_type = 'preference' AND invalid_at IS NULL"
        ).fetchone()[0]

    corrected_text = "Alex现在不喜欢看星图了"
    corrected = _item("preference", "ignored", corrected_text, entity="Alex")
    corrected["action"] = "correct"
    corrected["corrects_cognition_id"] = prior_id
    corrected["supports"] = [{"evidence_id": "evidence-2", "segment_id": "s0"}]
    _run(
        path, MutableClock(), [_model(_batch(corrected))], ("evidence-2",),
        lambda db_path: _set_evidence(db_path, "evidence-2", corrected_text),
        job_id="job-2",
    )
    assert _job(path, job_id="job-2")["state"] == "applied"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT invalid_at FROM cognition WHERE id = ?", (prior_id,)).fetchone()[0]
        target = db.execute(
            "SELECT ct.target_entity_id FROM cognition_target ct JOIN cognition c "
            "ON c.id = ct.cognition_id WHERE c.invalid_at IS NULL AND c.content_type = 'preference'"
        ).fetchone()[0]
        assert target == entity_id_for("owner", "Alex")


def test_unverified_optional_event_participant_is_dropped_without_losing_valid_peer(
    tmp_path: Path,
) -> None:
    path = tmp_path / "optional-event-participant.sqlite3"
    raw = "我已经寄出了，Alex也很喜欢看天文杂志"
    event = {
        "action": "form",
        "target": "owner_self",
        "statement_kind": "event",
        "formed_by": "stated",
        "proposition": "用户已经寄给Alex了",
        "participants": [{"canonical_name": "Alex", "kind": "person"}],
        "supports": [{"evidence_id": "evidence-1", "segment_id": "s0"}],
    }
    preference = _item("preference", "ignored", raw, entity="Alex")
    preference["supports"] = [{"evidence_id": "evidence-1", "segment_id": "s1"}]
    _run(
        path, MutableClock(), [_model(_batch(event, preference))], ("evidence-1",),
        lambda db_path: _set_evidence(db_path, "evidence-1", raw),
    )

    assert _job(path)["state"] == "applied"
    with sqlite3.connect(path) as db:
        stored_event = db.execute(
            "SELECT content, participants_json FROM world_event"
        ).fetchone()
        assert stored_event[0] == "用户已经寄出了，"
        assert json.loads(stored_event[1]) == []
        assert db.execute(
            "SELECT COUNT(*) FROM cognition WHERE content_type = 'preference' "
            "AND invalid_at IS NULL"
        ).fetchone()[0] == 1


def test_segment_event_does_not_borrow_participant_from_another_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cross-source-event-participant.sqlite3"
    event = {
        "action": "form", "target": "owner_self", "statement_kind": "event",
        "formed_by": "stated", "proposition": "用户已经寄出了",
        "participants": [{"canonical_name": "Alex", "kind": "person"}],
        "supports": [{"evidence_id": "evidence-1", "segment_id": "s0"}],
    }

    def setup(db_path: Path) -> None:
        _set_evidence(db_path, "evidence-1", "我已经寄出了")
        _set_evidence(db_path, "evidence-2", "Alex正在看书")

    _run(
        path, MutableClock(), [_model(_batch(event))],
        ("evidence-1", "evidence-2"), setup,
    )
    row = _job(path)
    assert row["state"] == "no_change"
    assert json.loads(str(row["world_result_json"]))["reason"] == "invalid_event_participants"
