"""Evidence-backed owner names must reach unchanged first-person World facts.

The English birth-date regression is the minimal public HaluMem reproduction
from memoweft-halumem-eval b32e942, reports/reproduction-fixtures.json. These
tests run the real formation compiler with fixed responses, never a model.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Literal

import pytest

from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.integrations.trust.command_service import CommandService
from test_hermes_batch_adapter_v2 import _form, _run, _set_evidence
from test_hermes_batch_adapter_v3 import _naming
from test_hermes_batch_adapter_v3 import _relationship
from test_hermes_batch_adapter_v3c import _batch, _model, _v5_item
from test_hermes_batch_adapter_v5 import _batch as _batch_v8, _model as _model_v8, _v8_item
from test_hermes_world_worker import MutableClock, _job
from test_semantic_recall import ForcedProvider


def _apply(path: Path, raw: str, item: dict[str, object], evidence_id: str) -> str:
    _run(path, MutableClock(), [_model(_batch(item))], (evidence_id,),
         lambda p: _set_evidence(p, evidence_id, raw), job_id=evidence_id)
    result = _job(path, evidence_id)
    assert result["state"] == "applied", result["world_result_json"]
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT cognition_id FROM cognition_evidence WHERE evidence_id=?",
                         (evidence_id,)).fetchone()
        return str(row[0]) if row else ""


def _apply_v8(path: Path, raw: str, item: dict[str, object], evidence_id: str) -> str:
    _run(path, MutableClock(), [_model_v8(_batch_v8(item))], (evidence_id,),
         lambda p: _set_evidence(p, evidence_id, raw), job_id=evidence_id)
    result = _job(path, evidence_id)
    assert result["state"] == "applied", result["world_result_json"]
    with sqlite3.connect(path) as db:
        return str(db.execute("SELECT cognition_id FROM cognition_evidence WHERE evidence_id=?",
                              (evidence_id,)).fetchone()[0])


def _owner(path: Path, language: str) -> tuple[str, str, str]:
    if language == "en":
        name, declaration = "Martin Mark", "My name is Martin Mark, and I am a Male."
        birthday = "I was born on 1996-08-02, and I am now 29 years old."
        proposition, query = declaration, "What is Martin Mark's birth date?"
        birth_proposition = birthday
    else:
        name, declaration = "林晓", "我叫林晓。"
        birthday = "我出生于1996年8月2日。"
        proposition, query = "用户叫林晓。", "林晓的出生日期是什么？"
        birth_proposition = "用户出生于1996年8月2日。"
    _apply(path, declaration, _naming(proposition, name, (0, len(declaration)),
           evidence_id="name"), "name")
    birthday_id = _apply(path, birthday, _form(birth_proposition, (0, len(birthday)),
                         kind="attribute", evidence_id="birthday"), "birthday")
    return query, birthday_id, birth_proposition


def _alias_world(path: Path, language: str, neutral_first: bool, *, self_naming: bool = True) -> tuple[str, str, str, str]:
    name, alias, neutral, declaration, birth, merge, name_query, alias_query = (
        "Martin Mark", "Marty", "Named Marty.", "My name is Martin Mark.",
        "I was born on 1996-08-02.", "Marty is Martin Mark.",
        "What is Martin Mark's birth date?", "What is Marty's birth date?",
    ) if language == "en" else (
        "林晓", "小晓", "取名叫小晓。", "我叫林晓。", "我出生于1996年8月2日。",
        "小晓就是林晓。", "林晓的出生日期是什么？", "小晓的出生日期是什么？",
    )
    if not self_naming:
        declaration = "Her name is Martin Mark." if language == "en" else "她叫林晓。"
    name_prop = "用户" + declaration[1:] if language == "zh" and self_naming else declaration
    birth_prop = birth if language == "en" else "用户" + birth[1:]

    def form_neutral() -> None:
        _apply(path, neutral, _naming(neutral, alias, (0, len(neutral)), evidence_id="neutral"), "neutral")

    if neutral_first:
        form_neutral()
    name_id = _apply(path, declaration, _naming(name_prop, name, (0, len(declaration)), evidence_id="name"), "name")
    birthday_id = _apply(path, birth, _form(birth_prop, (0, len(birth)), kind="attribute", evidence_id="birthday"), "birthday")
    if not neutral_first:
        form_neutral()
    with closing(sqlite3.connect(path)) as db:
        before = recall_world_snapshot(db, "owner", name_query)
        assert before is not None and (("cognition", birthday_id) in before.selected_item_ids) == self_naming
    _apply(path, merge, _v5_item("alias", merge, (0, len(merge)), evidence_id="alias",
           entity={"canonical_name": alias, "kind": "person"},
           alias_of={"canonical_name": name, "kind": "person"}), "alias")
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT id FROM entity WHERE canonical_name=? AND invalid_at IS NULL",
                          (alias if neutral_first else name,)).fetchone() is not None
    return name_query, alias_query, birthday_id, name_id


def _selected(path: Path, query: str, item_id: str, tier: Literal["local", "cloud"] = "local") -> bool:
    with closing(sqlite3.connect(path)) as db:
        changes = db.total_changes
        snapshot = recall_world_snapshot(db, "owner", query, model_tier=tier)
        assert snapshot is not None and snapshot == recall_world_snapshot(db, "owner", query, model_tier=tier)
        assert db.total_changes == changes
        return ("cognition", item_id) in snapshot.selected_item_ids


def _command(path: Path, operation: str, kind: str, item_id: str, payload: dict[str, object] | None = None) -> None:
    with closing(sqlite3.connect(path)) as db:
        revision = db.execute("SELECT revision FROM memory_state").fetchone()[0]
    receipt = CommandService(path, subject_id="owner", host_id="hermes:test").submit_command({
        "schema_version": 1, "command_id": "name-test-" + operation + "-" + item_id,
        "subject_id": "owner", "actor": "owner", "expected_world_revision": revision,
        "operation": operation, "target_kind": kind, "target_id": item_id,
        "payload": payload or {}, "submitted_at": "2026-10-16T00:00:00.000Z",
    })
    assert receipt["result_state"] == "applied", receipt


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("neutral_first", [False, True])
def test_alias_formation_order_preserves_sourced_self_name(tmp_path: Path, language: str, neutral_first: bool) -> None:
    path = tmp_path / "world.sqlite3"
    name_query, alias_query, birthday_id, _ = _alias_world(path, language, neutral_first)
    assert _selected(path, name_query, birthday_id)
    assert _selected(path, alias_query, birthday_id)


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("neutral_first", [False, True])
@pytest.mark.parametrize("tier", ["local", "cloud"])
@pytest.mark.parametrize("source", ["name", "alias", "birthday"])
def test_alias_order_keeps_each_source_destination_permission(
    tmp_path: Path, language: str, neutral_first: bool, tier: Literal["local", "cloud"], source: str,
) -> None:
    path = tmp_path / "world.sqlite3"
    name_query, alias_query, birthday_id, _ = _alias_world(path, language, neutral_first)
    assert _selected(path, name_query, birthday_id, tier)
    assert _selected(path, alias_query, birthday_id, tier)
    _command(path, "update_evidence_permissions", "evidence", source, {"allow_" + tier + "_read": False})
    # With a self-name canonical, its direct identity does not depend on an
    # independently revoked alias. The alias query always requires that link.
    denied_query = alias_query if source == "alias" and not neutral_first else name_query
    assert not _selected(path, denied_query, birthday_id, tier)
    if source == "alias" and not neutral_first:
        assert _selected(path, name_query, birthday_id, tier)
    other: Literal["local", "cloud"] = "cloud" if tier == "local" else "local"
    assert _selected(path, name_query, birthday_id, other)
    assert _selected(path, alias_query, birthday_id, other)


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("neutral_first", [False, True])
@pytest.mark.parametrize("operation,target", [
    ("archive_world_item", "name-cognition"), ("mute_world_item", "name-cognition"),
    ("retract_world_item", "name-cognition"), ("delete_world_item", "name-cognition"),
    ("forget_evidence", "name"), ("delete_evidence", "name"),
    ("forget_evidence", "alias"), ("delete_evidence", "alias"),
])
def test_alias_order_does_not_resurrect_withdrawn_identity_sources(
    tmp_path: Path, language: str, neutral_first: bool, operation: str, target: str,
) -> None:
    path = tmp_path / "world.sqlite3"
    name_query, alias_query, birthday_id, name_id = _alias_world(path, language, neutral_first)
    assert _selected(path, name_query, birthday_id)
    _command(path, operation, "cognition" if target == "name-cognition" else "evidence",
             name_id if target == "name-cognition" else target)
    denied_query = alias_query if target == "alias" and not neutral_first else name_query
    for tier in ("local", "cloud"):
        assert not _selected(path, denied_query, birthday_id, tier)
        assert _selected(path, "1996-08-02" if language == "en" else "1996年8月2日", birthday_id, tier)


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("neutral_first", [False, True])
def test_alias_order_never_promotes_third_party_naming(tmp_path: Path, language: str, neutral_first: bool) -> None:
    path = tmp_path / "world.sqlite3"
    name_query, alias_query, birthday_id, _ = _alias_world(path, language, neutral_first, self_naming=False)
    assert not _selected(path, name_query, birthday_id)
    assert not _selected(path, alias_query, birthday_id)


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("neutral_first", [False, True])
def test_alias_order_retains_same_name_ambiguity(tmp_path: Path, language: str, neutral_first: bool) -> None:
    path = tmp_path / "world.sqlite3"
    name_query, alias_query, birthday_id, _ = _alias_world(path, language, neutral_first)
    name = ("Marty" if neutral_first else "Martin Mark") if language == "en" else ("小晓" if neutral_first else "林晓")
    raw = f"{name} is my colleague." if language == "en" else f"{name}是我的同事。"
    proposition = raw if language == "en" else "用户" + raw
    _apply(path, raw, _relationship(proposition, name, "colleague", (0, len(raw)), evidence_id="colleague"), "colleague")
    assert not _selected(path, name_query, birthday_id)
    assert not _selected(path, alias_query, birthday_id)


@pytest.mark.parametrize("language", ["en", "zh"])
def test_self_name_reaches_first_person_birthday_without_rewriting(tmp_path: Path, language: str) -> None:
    path = tmp_path / "world.sqlite3"
    query, birthday_id, proposition = _owner(path, language)
    with sqlite3.connect(path) as db:
        # This is the existing schema, without added tags or a targeted owner fact.
        assert db.execute("SELECT * FROM cognition_target WHERE cognition_id=?",
                          (birthday_id,)).fetchone() is None
        original = db.execute("SELECT raw_content FROM evidence ORDER BY id").fetchall()
        changes = db.total_changes
        snapshot = recall_world_snapshot(db, "owner", query)
        assert snapshot is not None
        assert ("cognition", birthday_id) in snapshot.selected_item_ids
        assert proposition in snapshot.rendered_recall
        assert snapshot.count <= 5 and len(snapshot.rendered_recall) <= 601
        assert db.total_changes == changes
        assert db.execute("SELECT raw_content FROM evidence ORDER BY id").fetchall() == original
        assert recall_world_snapshot(db, "owner", query) == snapshot


@pytest.mark.parametrize("language", ["en", "zh"])
def test_other_person_and_other_account_do_not_inherit_owner_name(tmp_path: Path, language: str) -> None:
    path = tmp_path / "world.sqlite3"
    query, birthday_id, _ = _owner(path, language)
    name, raw = ("Taylor Reed", "Taylor Reed was born on 2001-01-01.") if language == "en" else (
        "陈默", "陈默出生于2001年1月1日。")
    other_id = _apply(path, raw, _v5_item("attribute", raw, (0, len(raw)),
                      entity={"canonical_name": name, "kind": "person"}, evidence_id="other"), "other")
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, "owner", query)
        assert snapshot is not None
        assert ("cognition", birthday_id) in snapshot.selected_item_ids
        assert ("cognition", other_id) not in snapshot.selected_item_ids
        assert "2001" not in snapshot.rendered_recall
        other = recall_world_snapshot(db, "another-account", query)
        assert other is not None and other.count == 0
        targeted = recall_world_snapshot(db, "owner", f"{name}?")
        assert targeted is not None
        assert ("cognition", other_id) in targeted.selected_item_ids
        assert ("cognition", birthday_id) not in targeted.selected_item_ids


@pytest.mark.parametrize("language", ["en", "zh"])
def test_unknown_or_partial_name_does_not_establish_owner_identity(tmp_path: Path, language: str) -> None:
    path = tmp_path / "world.sqlite3"
    _, birthday_id, _ = _owner(path, language)
    queries = ("Who is Alex Mark?", "What is Martin's birth date?", "What is Alex Mark's birth date?") if language == "en" else (
        "陈默是谁？", "林晓明是谁？")
    with sqlite3.connect(path) as db:
        for query in queries:
            snapshot = recall_world_snapshot(db, "owner", query)
            assert snapshot is not None
            assert ("cognition", birthday_id) not in snapshot.selected_item_ids


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("mutation,tier", [
    ("UPDATE evidence SET deleted_at='2026-10-16' WHERE id='name'", "local"),
    ("UPDATE evidence SET allow_local_read=0 WHERE id='name'", "local"),
    ("UPDATE evidence SET allow_cloud_read=0 WHERE id='name'", "cloud"),
    ("UPDATE evidence SET subject_id='another-account' WHERE id='name'", "local"),
    ("UPDATE cognition SET muted_at='2026-10-16' WHERE content_type='naming'", "local"),
])
def test_owner_association_requires_current_naming_source(
    tmp_path: Path, language: str, mutation: str, tier: str,
) -> None:
    path = tmp_path / "world.sqlite3"
    query, birthday_id, _ = _owner(path, language)
    with sqlite3.connect(path) as db:
        db.execute(mutation)
        db.commit()
        snapshot = recall_world_snapshot(db, "owner", query, model_tier=tier)  # type: ignore[arg-type]
        assert snapshot is not None
        assert ("cognition", birthday_id) not in snapshot.selected_item_ids


@pytest.mark.parametrize("language", ["en", "zh"])
def test_same_name_owner_and_friend_remains_ambiguous(tmp_path: Path, language: str) -> None:
    path = tmp_path / "world.sqlite3"
    query, birthday_id, birth_proposition = _owner(path, language)
    name, raw = ("Martin Mark", "Martin Mark is my colleague.") if language == "en" else (
        "林晓", "林晓是我的同事。")
    proposition = raw if language == "en" else "用户" + raw
    _apply(path, raw, _relationship(proposition, name, "colleague", (0, len(raw)),
           evidence_id="colleague"), "colleague")
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, "owner", query)
        assert snapshot is not None and snapshot.count == 0
        semantic = recall_world_snapshot(db, "owner", query, candidate_provider=ForcedProvider([
            {"id": birthday_id, "content": birth_proposition},
        ]))
        assert semantic is not None and semantic.count == 0


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("tier", ["local", "cloud"])
def test_sourced_alias_reaches_owner_but_revoked_alias_cannot(tmp_path: Path, language: str, tier: str) -> None:
    path = tmp_path / "world.sqlite3"
    _, birthday_id, _ = _owner(path, language)
    if language == "en":
        name, alias, declaration = "Martin Mark", "Marty", "Named Marty."
        raw, proposition = "Marty is Martin Mark.", "Marty is Martin Mark."
        alias_query, lower_query = "What is Marty's birth date?", "what is martin mark's birth date?"
    else:
        name, alias, declaration = "林晓", "小晓", "取名叫小晓。"
        raw, proposition = "小晓就是林晓。", "小晓就是林晓。"
        alias_query, lower_query = "小晓的出生日期是什么？", "林晓的出生日期是什么？"
    _apply(path, declaration, _naming(declaration, alias, (0, len(declaration)),
           evidence_id="alias-name"), "alias-name")
    _apply(path, raw, _v5_item("alias", proposition, (0, len(raw)),
           entity={"canonical_name": alias, "kind": "person"},
           alias_of={"canonical_name": name, "kind": "person"}, evidence_id="alias"), "alias")
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, "owner", alias_query, model_tier=tier)  # type: ignore[arg-type]
        assert snapshot is not None
        assert ("cognition", birthday_id) in snapshot.selected_item_ids
        lowercase = recall_world_snapshot(db, "owner", lower_query)
        assert lowercase is not None
        assert ("cognition", birthday_id) in lowercase.selected_item_ids
        permission = "allow_local_read" if tier == "local" else "allow_cloud_read"
        db.execute(f"UPDATE evidence SET {permission}=0 WHERE id='alias'")
        db.commit()
        denied = recall_world_snapshot(db, "owner", alias_query, model_tier=tier)  # type: ignore[arg-type]
        assert denied is not None
        assert ("cognition", birthday_id) not in denied.selected_item_ids


def test_third_party_naming_never_becomes_self_naming(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite3"
    declaration = "My sister's name is Martin Mark."
    _apply(path, declaration, _naming(declaration, "Martin Mark", (0, len(declaration)),
           evidence_id="name"), "name")
    raw = "I was born on 1996-08-02."
    birthday_id = _apply(path, raw, _form(raw, (0, len(raw)), kind="attribute",
                         evidence_id="birthday"), "birthday")
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, "owner", "What is Martin Mark's birth date?")
        assert snapshot is not None
        assert ("cognition", birthday_id) not in snapshot.selected_item_ids


def test_legacy_entity_without_naming_provenance_does_not_establish_owner_identity(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite3"
    query, birthday_id, _ = _owner(path, "en")
    with sqlite3.connect(path) as db:
        # Simulate a legacy imported entity without the formation ledger. Do
        # not promote its display name into a newly asserted owner identity.
        db.execute("DELETE FROM evidence_ledger")
        db.commit()
        snapshot = recall_world_snapshot(db, "owner", query)
        assert snapshot is not None
        assert ("cognition", birthday_id) not in snapshot.selected_item_ids
        assert "My name is Martin Mark" in snapshot.rendered_recall
        direct = recall_world_snapshot(db, "owner", "1996-08-02")
        assert direct is not None
        assert ("cognition", birthday_id) in direct.selected_item_ids


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("case", ["pronoun", "topic", "sister", "perspective", "possessive", "missing_subject"])
def test_unresolved_fact_subject_never_inherits_owner_name(tmp_path: Path, language: str, case: str) -> None:
    path = tmp_path / "world.sqlite3"
    birthday_query, _, _ = _owner(path, language)
    if language == "en":
        raw = {
            "pronoun": "They prefer jasmine tea.",
            "topic": "They prefer jasmine tea.",
            "sister": "My sister was born on 2001-01-01.",
            "perspective": "I think they prefer jasmine tea.",
            "possessive": "My favorite author prefers jasmine tea.",
            "missing_subject": "Prefers jasmine tea.",
        }[case]
        proposition, name, topic = raw, "Martin Mark", "jasmine tea"
        query = birthday_query if case == "sister" else f"What does {name} prefer?"
    else:
        raw = {
            "pronoun": "他们更喜欢茉莉花茶。",
            "topic": "他们更喜欢茉莉花茶。",
            "sister": "我的妹妹出生于2001年1月1日。",
            "perspective": "我觉得他们更喜欢茉莉花茶。",
            "possessive": "我最喜欢的作家更喜欢茉莉花茶。",
            "missing_subject": "更喜欢茉莉花茶。",
        }[case]
        proposition = "用户" + (raw[1:] if raw.startswith("我") else raw)
        name, topic = "林晓", "茉莉花茶"
        query = birthday_query if case == "sister" else f"{name}喜欢什么？"
    item = _v8_item("attribute" if case == "sister" else "preference", proposition, (0, len(raw)),
                    evidence_id="third-person",
                    entity={"canonical_name": topic, "kind": "topic"} if case == "topic" else None)
    other_id = _apply_v8(path, raw, item, "third-person")
    with sqlite3.connect(path) as db:
        original = db.execute("SELECT raw_content FROM evidence WHERE id='third-person'").fetchone()[0]
        snapshot = recall_world_snapshot(db, "owner", query)
        assert snapshot is not None
        assert ("cognition", other_id) not in snapshot.selected_item_ids
        assert db.execute("SELECT raw_content FROM evidence WHERE id='third-person'").fetchone()[0] == original == raw
        direct_query = ("2001-01-01" if language == "en" else "2001年1月1日") if case == "sister" else topic
        direct = recall_world_snapshot(db, "owner", direct_query)
        assert direct is not None and ("cognition", other_id) in direct.selected_item_ids


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("topic_target", [False, True])
def test_explicit_first_person_preference_retains_owner_association(tmp_path: Path, language: str, topic_target: bool) -> None:
    path = tmp_path / "world.sqlite3"
    _owner(path, language)
    raw, proposition, topic, query = ("I prefer jasmine tea.", "I prefer jasmine tea.", "jasmine tea", "What does Martin Mark prefer?") if language == "en" else (
        "我更喜欢茉莉花茶。", "用户更喜欢茉莉花茶。", "茉莉花茶", "林晓喜欢什么？")
    item = _v8_item("preference", proposition, (0, len(raw)), evidence_id="self-preference",
                    entity={"canonical_name": topic, "kind": "topic"} if topic_target else None)
    own_id = _apply_v8(path, raw, item, "self-preference")
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, "owner", query)
        assert snapshot is not None and ("cognition", own_id) in snapshot.selected_item_ids


@pytest.mark.parametrize("language", ["en", "zh"])
def test_self_clause_in_shared_source_does_not_qualify_another_claim(tmp_path: Path, language: str) -> None:
    path = tmp_path / "world.sqlite3"
    _owner(path, language)
    first, second, query = ("I prefer jasmine tea.", "They prefer green tea.", "What does Martin Mark prefer?") if language == "en" else (
        "我喜欢茉莉花茶。", "他们喜欢绿茶。", "林晓喜欢什么？")
    raw = first + second
    own = first if language == "en" else "用户" + first[1:]
    other = second if language == "en" else "用户" + second
    items = (_v8_item("preference", own, (0, len(first)), evidence_id="shared"),
             _v8_item("preference", other, (len(first), len(raw)), evidence_id="shared"))
    _run(path, MutableClock(), [_model_v8(_batch_v8(*items))], ("shared",),
         lambda p: _set_evidence(p, "shared", raw), job_id="shared")
    assert _job(path, "shared")["state"] == "applied"
    with sqlite3.connect(path) as db:
        own_id = str(db.execute("SELECT id FROM cognition WHERE content=?", (own,)).fetchone()[0])
        other_id = str(db.execute("SELECT id FROM cognition WHERE content=?", (other,)).fetchone()[0])
        snapshot = recall_world_snapshot(db, "owner", query)
        assert snapshot is not None and ("cognition", own_id) in snapshot.selected_item_ids
        assert ("cognition", other_id) not in snapshot.selected_item_ids
        assert db.execute("SELECT raw_content FROM evidence WHERE id='shared'").fetchone()[0] == raw
