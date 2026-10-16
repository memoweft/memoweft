"""Evidence-backed owner names must reach unchanged first-person World facts.

The English birth-date regression is the minimal public HaluMem reproduction
from memoweft-halumem-eval b32e942, reports/reproduction-fixtures.json. These
tests run the real formation compiler with fixed responses, never a model.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from memoweft.integrations.hermes.recall import recall_world_snapshot
from test_hermes_batch_adapter_v2 import _form, _run, _set_evidence
from test_hermes_batch_adapter_v3 import _naming
from test_hermes_batch_adapter_v3 import _relationship
from test_hermes_batch_adapter_v3c import _batch, _model, _v5_item
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
