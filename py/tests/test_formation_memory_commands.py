"""A memory-management command is not evidence that the remembered fact is false."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from memoweft.integrations.trust.revision import current_world_revision
from test_hermes_batch_adapter_v3d import _seed_cognition
from test_hermes_batch_adapter_v5 import _batch, _model, _run, _set_evidence, _v8_item
from test_hermes_world_worker import MutableClock, _job


TARGET = "cognition-existing-preference"


def _setup(path: Path, raw: str) -> None:
    _set_evidence(path, "evidence-1", raw)
    _seed_cognition(path, TARGET, "用户喜欢骑自行车")


def _assert_unchanged(path: Path, raw: str) -> None:
    job = _job(path)
    assert job["state"] == "no_change"
    assert json.loads(str(job["world_result_json"]))["normalizations"] == [
        {"item_index": 0, "rule": "excluded_memory_management_instruction"},
    ]
    assert "formation_rewrite" not in json.loads(str(job["model_result_json"]))
    with sqlite3.connect(path) as db:
        assert current_world_revision(db) == 0
        assert db.execute("SELECT count(*) FROM cognition").fetchone()[0] == 1
        assert db.execute("SELECT invalid_at FROM cognition WHERE id=?", (TARGET,)).fetchone()[0] is None
        assert db.execute("SELECT count(*) FROM retraction").fetchone()[0] == 0
        assert db.execute("SELECT raw_content, deleted_at FROM evidence WHERE id='evidence-1'").fetchone() == (raw, None)
        assert db.execute("SELECT raw_content, deleted_at FROM evidence WHERE id='seed-evidence'").fetchone() == ("seed evidence", None)


@pytest.mark.parametrize("raw,proposition", [
    # Frozen MiMo v8 responses from Windows/Android M7, with only opaque IDs replaced.
    ("请永久忘掉我最喜欢的零食是芝麻饼这条记忆，默认保留聊天原话。", "请永久忘掉我最喜欢的零食是芝麻饼这条记忆"),
    ("请忘掉我最喜欢的乐器是手碟这项偏好，默认保留原聊天。确认实际删除后再告诉我完成。", "请忘掉我最喜欢的乐器是手碟这项偏好，"),
])
def test_frozen_m7_retract_does_not_mutate_world(tmp_path: Path, raw: str, proposition: str) -> None:
    path = tmp_path / "world.sqlite3"
    item = {
        "action": "correct", "target": "owner_self", "statement_kind": "preference",
        "formed_by": "stated", "retract": True, "proposition": proposition,
        "supports": [{"evidence_id": "evidence-1", "segment_id": "s0"}],
        "corrects_cognition_id": TARGET,
    }
    _run(path, MutableClock(), [_model(_batch(item))], ("evidence-1",), lambda p: _setup(p, raw))
    _assert_unchanged(path, raw)


@pytest.mark.parametrize("raw", [
    "那条删掉吧", "别记我喜欢骑自行车了", "请把我喜欢骑自行车这条偏好删掉。",
    "请忘掉我喜欢骑自行车这条记忆。", "不要再记住我的这个偏好。",
    "Forget that I like cycling.", "Please delete that memory.",
    "Remove my cycling preference from memory.", "Stop remembering that I like cycling.",
    "请你从长期记忆里删掉我喜欢骑自行车这条偏好。",
    "Could you forget that I like cycling?", "请帮我不再记住骑车偏好。",
])
def test_management_request_cannot_retract_a_fact(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "world.sqlite3"
    item = _v8_item("preference", raw, (0, len(raw)), action="correct", retract=True, corrects_cognition_id=TARGET)
    item["supports"] = [{"evidence_id": "evidence-1", "quote": raw}]
    def setup(p: Path) -> None:
        _setup(p, raw)
        # Recovery must validate an already checkpointed model interpretation
        # too; the early lexical no-change shortcut is not run on this path.
        with sqlite3.connect(p) as db:
            db.execute("UPDATE memory_world_job SET model_result_json=? WHERE job_id='job-1'",
                       (json.dumps(_model(_batch(item))),))
    _run(path, MutableClock(), [], ("evidence-1",), setup)
    _assert_unchanged(path, raw)


@pytest.mark.parametrize("action", ["form", "correct"])
def test_clipped_command_object_cannot_be_used_as_fact(tmp_path: Path, action: str) -> None:
    path = tmp_path / "world.sqlite3"
    raw = "请忘掉我喜欢骑自行车这条记忆。"
    claim = "我喜欢骑自行车"
    start = raw.index(claim)
    item = _v8_item("preference", claim, (start, start + len(claim)), action=action)
    item["supports"] = [{"evidence_id": "evidence-1", "quote": claim}]
    if action == "correct":
        item.update(retract=True, corrects_cognition_id=TARGET)
    _run(path, MutableClock(), [_model(_batch(item))], ("evidence-1",), lambda p: _setup(p, raw))
    _assert_unchanged(path, raw)


def test_independent_fact_in_same_message_survives_excluded_retract(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite3"
    raw = "请忘掉骑车偏好，我喜欢下围棋。"
    command, claim = raw.split("，")
    retract = _v8_item("preference", command, (0, len(command)), action="correct", retract=True, corrects_cognition_id=TARGET)
    start = len(command) + 1
    stable = _v8_item("preference", "用户喜欢下围棋", (start, len(raw)))
    _run(path, MutableClock(), [_model(_batch(retract, stable))], ("evidence-1",), lambda p: _setup(p, raw))
    assert _job(path)["state"] == "applied"
    with sqlite3.connect(path) as db:
        assert current_world_revision(db) == 1
        assert db.execute("SELECT invalid_at FROM cognition WHERE id=?", (TARGET,)).fetchone()[0] is None
        assert db.execute("SELECT count(*) FROM retraction").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM cognition WHERE content LIKE '%围棋%'").fetchone()[0] == 1
        assert db.execute("SELECT raw_content FROM evidence WHERE id='evidence-1'").fetchone()[0] == raw


@pytest.mark.parametrize("raw", ["我从来不喜欢骑自行车。", "喜欢骑自行车的是朋友，不是我。", "I never liked cycling."])
def test_actual_fact_denial_still_retracts(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "world.sqlite3"
    item = _v8_item("preference", raw, (0, len(raw)), action="correct", retract=True, corrects_cognition_id=TARGET)
    item["supports"] = [{"evidence_id": "evidence-1", "quote": raw}]
    _run(path, MutableClock(), [_model(_batch(item))], ("evidence-1",), lambda p: _setup(p, raw))
    assert _job(path)["state"] == "applied"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT invalid_at FROM cognition WHERE id=?", (TARGET,)).fetchone()[0] is not None
        assert db.execute("SELECT count(*) FROM retraction").fetchone()[0] == 1
        assert db.execute("SELECT deleted_at FROM evidence WHERE id='seed-evidence'").fetchone()[0] is None


def test_quoted_management_words_in_real_preference_are_not_a_command(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite3"
    raw = "我喜欢歌名《忘掉我》的写法。"
    item = _v8_item("preference", "用户喜欢歌名《忘掉我》的写法", (0, len(raw)))
    _run(path, MutableClock(), [_model(_batch(item))], ("evidence-1",), lambda p: _set_evidence(p, "evidence-1", raw))
    assert _job(path)["state"] == "applied"


@pytest.mark.parametrize("raw,name", [
    ("I am reading the book 'Forget Me'.", "Forget Me"),
    ("我正在读《忘掉我》。", "忘掉我"),
])
def test_quoted_book_title_is_a_valid_independent_name(tmp_path: Path, raw: str, name: str) -> None:
    path = tmp_path / "world.sqlite3"
    start = raw.index(name)
    item = _v8_item("naming", name, (start, start + len(name)),
                    entity={"canonical_name": name, "kind": "thing"})
    _run(path, MutableClock(), [_model(_batch(item))], ("evidence-1",), lambda p: _set_evidence(p, "evidence-1", raw))
    assert _job(path)["state"] == "applied"
    with sqlite3.connect(path) as db:
        assert current_world_revision(db) == 1
        assert db.execute("SELECT canonical_name FROM entity").fetchall() == [(name,)]


@pytest.mark.parametrize("raw,name", [
    ("Please forget the book 'Forget Me'.", "Forget Me"),
    ("朋友拿“请忘掉我喜欢骑自行车这条记忆”作为指令示例。", "骑自行车"),
])
def test_naming_cannot_bypass_actual_or_quoted_memory_command(tmp_path: Path, raw: str, name: str) -> None:
    path = tmp_path / "world.sqlite3"
    start = raw.index(name)
    item = _v8_item("naming", name, (start, start + len(name)),
                    entity={"canonical_name": name, "kind": "thing"})
    _run(path, MutableClock(), [_model(_batch(item))], ("evidence-1",), lambda p: _set_evidence(p, "evidence-1", raw))
    assert _job(path)["state"] == "no_change"
    assert json.loads(str(_job(path)["world_result_json"]))["normalizations"] == [
        {"item_index": 0, "rule": "excluded_memory_management_instruction"},
    ]
    with sqlite3.connect(path) as db:
        assert current_world_revision(db) == 0
        assert db.execute("SELECT count(*) FROM entity").fetchone()[0] == 0
        assert db.execute("SELECT raw_content FROM evidence").fetchone()[0] == raw


def test_clipped_quoted_command_is_not_factual_denial(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite3"
    quoted = "请忘掉我喜欢骑自行车这条记忆"
    raw = "朋友拿“" + quoted + "”作为指令示例。"
    item = _v8_item("preference", quoted, (raw.index(quoted), raw.index(quoted) + len(quoted)),
                    action="correct", retract=True, corrects_cognition_id=TARGET)
    _run(path, MutableClock(), [_model(_batch(item))], ("evidence-1",), lambda p: _setup(p, raw))
    _assert_unchanged(path, raw)


def test_clipped_object_inside_quoted_command_cannot_retract(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite3"
    raw = "朋友举例说“请忘掉我喜欢骑自行车这条记忆”。"
    claim = "我喜欢骑自行车"
    item = _v8_item("preference", claim, (raw.index(claim), raw.index(claim) + len(claim)),
                    action="correct", retract=True, corrects_cognition_id=TARGET)
    item["supports"] = [{"evidence_id": "evidence-1", "quote": claim}]
    _run(path, MutableClock(), [_model(_batch(item))], ("evidence-1",), lambda p: _setup(p, raw))
    _assert_unchanged(path, raw)


def test_fact_denial_next_to_management_request_still_retracts(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite3"
    raw = "那条不属实，请删掉那条记忆。"
    item = _v8_item("preference", "那条不属实", (0, 5), action="correct", retract=True, corrects_cognition_id=TARGET)
    _run(path, MutableClock(), [_model(_batch(item))], ("evidence-1",), lambda p: _setup(p, raw))
    assert _job(path)["state"] == "applied"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM retraction").fetchone()[0] == 1


def test_ordinary_remember_request_still_forms_preference(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite3"
    raw = "请记住我喜欢骑自行车。"
    item = _v8_item("preference", "用户喜欢骑自行车", (0, len(raw)))
    item["supports"] = [{"evidence_id": "evidence-1", "sentence_id": "t0"}]
    _run(path, MutableClock(), [_model(_batch(item))], ("evidence-1",), lambda p: _set_evidence(p, "evidence-1", raw))
    assert _job(path)["state"] == "applied"


@pytest.mark.parametrize("raw", [
    "Please delete temporary files every night.",
    "Please remove trailing spaces from every file.",
    "Please delete the memory dump files every night.",
    "请删除每天生成的临时文件。",
    "请删除每天生成的记忆训练文件。",
])
def test_ongoing_file_instruction_is_not_memory_management(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "world.sqlite3"
    item = _v8_item("preference", raw, (0, len(raw)))
    item["supports"] = [{"evidence_id": "evidence-1", "sentence_id": "t0"}]
    _run(path, MutableClock(), [_model(_batch(item))], ("evidence-1",), lambda p: _set_evidence(p, "evidence-1", raw))
    assert _job(path)["state"] == "applied"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM cognition").fetchone()[0] == 1


def test_mixed_support_requires_rewrite_without_silent_trimming(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite3"
    raw = "请忘掉骑车偏好。我喜欢下围棋。"
    mixed = _v8_item("preference", raw, (0, len(raw)), action="correct", retract=True, corrects_cognition_id=TARGET)
    start = raw.index("我喜欢")
    stable = _v8_item("preference", "用户喜欢下围棋", (start, len(raw)))
    _run(path, MutableClock(), [_model(_batch(mixed)), _model(_batch(stable))], ("evidence-1",), lambda p: _setup(p, raw))
    row = _job(path)
    assert row["state"] == "applied"
    checkpoint = json.loads(str(row["model_result_json"]))
    assert checkpoint["formation_rewrite"]["error"]["code"] == "mixed_memory_command_and_fact"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT invalid_at FROM cognition WHERE id=?", (TARGET,)).fetchone()[0] is None
        assert db.execute("SELECT count(*) FROM retraction").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM cognition WHERE content LIKE '%围棋%'").fetchone()[0] == 1
