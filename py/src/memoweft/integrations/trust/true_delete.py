"""Irreversible logical deletion of one Evidence and its linked projections.

The caller owns BEGIN IMMEDIATE and the revision/receipt commit.  Keep the
Evidence identity and origin as a content-free suppression marker so old
Portable bundles and repeated source events cannot recreate the content.
"""
from __future__ import annotations

import json
import sqlite3
from hashlib import sha256

from .command_store import CommandMutation, TrustCommandError
from .model import CommandEnvelopeV1


def _ids(db: sqlite3.Connection, sql: str, value: str) -> set[str]:
    return {str(row[0]) for row in db.execute(sql, (value,))}


def _delete_ids(db: sqlite3.Connection, table: str, column: str, ids: set[str]) -> None:
    for item_id in ids:
        db.execute(f"DELETE FROM {table} WHERE {column} = ?", (item_id,))


def _mentions_id(raw: str | None, identifiers: set[str]) -> bool:
    if raw is None:
        return False
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return False
    def visit(item: object) -> bool:
        if isinstance(item, str):
            return item in identifiers
        if isinstance(item, list):
            return any(visit(child) for child in item)
        if isinstance(item, dict):
            return any(visit(child) for child in item.values())
        return False
    return visit(value)


def delete_evidence(
    db: sqlite3.Connection,
    command: CommandEnvelopeV1,
    completed_at: str,
    subject_id: str,
) -> CommandMutation:
    if command["payload"]:
        raise TrustCommandError("invalid_trust_command_payload")
    if command["target_kind"] != "evidence":
        return CommandMutation("rejected")
    evidence_id = command["target_id"]
    row = db.execute(
        "SELECT deleted_at, raw_content, summary, preceding_ai_context, "
        "origin_id "
        "FROM evidence WHERE id = ? AND subject_id = ?",
        (evidence_id, subject_id),
    ).fetchone()
    if row is None:
        return CommandMutation("rejected")
    if row[0] is not None and row[1] == row[2] == "" and row[3] is None:
        return CommandMutation("no_change")
    prior_origin = db.execute(
        "SELECT origin_id FROM evidence_origin_history WHERE evidence_id = ?",
        (evidence_id,),
    ).fetchone()
    origin_id = row[4] if row[4] is not None else (prior_origin[0] if prior_origin else None)
    if row[0] is not None and origin_id is None:
        return CommandMutation("rejected", rejection_code="source_origin_unrecoverable")
    if origin_id is not None:
        if db.execute(
            "SELECT 1 FROM evidence WHERE origin_id = ? AND id <> ? "
            "AND deleted_at IS NULL",
            (origin_id, evidence_id),
        ).fetchone():
            return CommandMutation("rejected", rejection_code="source_origin_reused")
        db.execute(
            "INSERT OR IGNORE INTO hard_deleted_origin (origin_hash, subject_id, evidence_id) "
            "VALUES (?, ?, ?)",
            (sha256(str(origin_id).encode("utf-8")).hexdigest(), subject_id, evidence_id),
        )

    # Remove complete derived objects when any of their provenance refers to
    # this Evidence. A mixed-source object may contain the removed source's
    # wording, so keeping its content would not be safe.
    event_ids = _ids(db, "SELECT event_id FROM event_evidence WHERE evidence_id = ?", evidence_id)
    cognition_ids = _ids(db, "SELECT cognition_id FROM cognition_evidence WHERE evidence_id = ?", evidence_id)
    relationship_ids = _ids(db, "SELECT relationship_id FROM relationship_evidence WHERE evidence_id = ?", evidence_id)
    world_event_ids = _ids(db, "SELECT world_event_id FROM world_event_evidence WHERE evidence_id = ?", evidence_id)

    entity_ids: set[str] = set()
    ledger_ids: set[str] = set()
    for ledger_id, content in db.execute("SELECT id, content FROM evidence_ledger"):
        try:
            value = json.loads(str(content))
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and value.get("evidence_id") == evidence_id:
            ledger_ids.add(str(ledger_id))
            if isinstance(value.get("entity_id"), str):
                entity_ids.add(value["entity_id"])
    if entity_ids:
        for ledger_id, content in db.execute("SELECT id, content FROM evidence_ledger"):
            try:
                value = json.loads(str(content))
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and value.get("entity_id") in entity_ids:
                ledger_ids.add(str(ledger_id))
        for entity_id in entity_ids:
            relationship_ids.update(_ids(db, "SELECT id FROM relationship WHERE source_entity_id = ?", entity_id))
            relationship_ids.update(_ids(db, "SELECT id FROM relationship WHERE target_entity_id = ?", entity_id))
            cognition_ids.update(_ids(db, "SELECT cognition_id FROM cognition_target WHERE target_entity_id = ?", entity_id))
            cognition_ids.update(_ids(db, "SELECT cognition_id FROM cognition_target WHERE perspective_entity_id = ?", entity_id))

    # A queued or claimed worker may already hold the old batch in memory.
    # Removing its row under the same write lock fences every later apply and
    # settlement. Its terminal delivery row can also contain derived text.
    job_ids: set[str] = set()
    for job_id, evidence_ids_json in db.execute(
        "SELECT job_id, evidence_ids_json FROM memory_world_job WHERE subject_id = ?",
        (subject_id,),
    ):
        try:
            ids = json.loads(str(evidence_ids_json))
        except (TypeError, ValueError):
            ids = []
        if isinstance(ids, list) and evidence_id in ids:
            job_ids.add(str(job_id))
    _delete_ids(db, "terminal_outcome", "job_id", job_ids)
    _delete_ids(db, "memory_world_job", "job_id", job_ids)

    _delete_ids(db, "event_evidence", "event_id", event_ids)
    _delete_ids(db, "event", "id", event_ids)
    _delete_ids(db, "cognition_evidence", "cognition_id", cognition_ids)
    _delete_ids(db, "cognition_target", "cognition_id", cognition_ids)
    _delete_ids(db, "cognition_transitions", "prior_cognition_id", cognition_ids)
    _delete_ids(db, "cognition_transitions", "replacement_cognition_id", cognition_ids)
    _delete_ids(db, "evidence_retraction", "cognition_id", cognition_ids)
    _delete_ids(db, "retraction", "prior_cognition_id", cognition_ids)
    _delete_ids(db, "cognition", "id", cognition_ids)
    _delete_ids(db, "relationship_evidence", "relationship_id", relationship_ids)
    _delete_ids(db, "retraction", "prior_relationship_id", relationship_ids)
    _delete_ids(db, "relationship", "id", relationship_ids)
    _delete_ids(db, "world_event_evidence", "world_event_id", world_event_ids)
    _delete_ids(db, "retraction", "prior_event_id", world_event_ids)
    _delete_ids(db, "world_event", "id", world_event_ids)
    _delete_ids(db, "evidence_ledger", "id", ledger_ids)
    _delete_ids(db, "entity", "id", entity_ids)
    for kind, ids in (
        ("entity", entity_ids),
        ("relationship", relationship_ids),
        ("event", world_event_ids),
        ("cognition", cognition_ids),
    ):
        for item_id in ids:
            db.execute(
                "INSERT OR IGNORE INTO world_delete_marker "
                "(subject_id, object_kind, item_id, deleted_at) VALUES (?, ?, ?, ?)",
                (subject_id, kind, item_id, completed_at),
            )
            db.execute(
                "DELETE FROM world_item_lifecycle WHERE subject_id = ? "
                "AND object_kind = ? AND item_id = ?",
                (subject_id, kind, item_id),
            )
    affected_ids = ({evidence_id} | event_ids | cognition_ids | relationship_ids
                    | world_event_ids | entity_ids)
    for proposal_id, payload, review in list(db.execute(
        "SELECT id, payload_json, review_payload_json FROM proposals"
    )):
        if _mentions_id(payload, affected_ids) or _mentions_id(review, affected_ids):
            db.execute("DELETE FROM proposal_decision_receipts WHERE proposal_id = ?", (proposal_id,))
            db.execute("DELETE FROM proposals WHERE id = ?", (proposal_id,))
    for command_id, target_id, payload in list(db.execute(
        "SELECT command_id, target_id, payload_json FROM trust_command WHERE subject_id = ?",
        (subject_id,),
    )):
        if str(target_id) in affected_ids or _mentions_id(payload, affected_ids):
            db.execute(
                "UPDATE trust_command SET payload_json = '{}' WHERE command_id = ?",
                (command_id,),
            )
    _delete_ids(db, "management_log", "target_id", affected_ids)
    _delete_ids(db, "clarification", "answer_evidence_id", {evidence_id})
    _delete_ids(db, "clarification", "source_job_id", job_ids)
    _delete_ids(db, "clarification", "follow_up_job_id", job_ids)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'relationship_transitions'").fetchone():
        _delete_ids(db, "relationship_transitions", "prior_relationship_id", relationship_ids)
        _delete_ids(db, "relationship_transitions", "replacement_relationship_id", relationship_ids)
    # Remove any remaining direct provenance and content-bearing resolution.
    for table in (
        "event_evidence", "cognition_evidence", "relationship_evidence",
        "world_event_evidence", "evidence_retraction", "semantic_resolution",
        "boundary_evidence_content",
    ):
        db.execute(f"DELETE FROM {table} WHERE evidence_id = ?", (evidence_id,))
    db.execute(
        "UPDATE evidence SET raw_content = '', summary = '', "
        "preceding_ai_context = NULL, origin_id = NULL, allow_local_read = 0, "
        "allow_cloud_read = 0, allow_inference = 0, "
        "deleted_at = COALESCE(deleted_at, ?) WHERE id = ? AND subject_id = ?",
        (completed_at, evidence_id, subject_id),
    )
    db.execute("DELETE FROM evidence_origin_history WHERE evidence_id = ?", (evidence_id,))
    # An optional local FTS index is outside the versioned schema.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'cognition_fts'").fetchone():
        _delete_ids(db, "cognition_fts", "cognition_id", cognition_ids)
    # Identity is a derived cache; its JSON may contain names or statements
    # from any of the removed World objects. It can be rebuilt from survivors.
    db.execute("DELETE FROM identity_state WHERE world_id = ?", (subject_id,))
    return CommandMutation("applied", (evidence_id,))


def delete_world_item(
    db: sqlite3.Connection,
    command: CommandEnvelopeV1,
    completed_at: str,
    subject_id: str,
) -> CommandMutation:
    if command["payload"]:
        raise TrustCommandError("invalid_trust_command_payload")
    kind = command["target_kind"]
    item_id = command["target_id"]
    table_and_owner = {
        "entity": ("entity", "world_id"),
        "relationship": ("relationship", "world_id"),
        "event": ("world_event", "world_id"),
        "cognition": ("cognition", "subject_id"),
    }
    if kind not in table_and_owner:
        return CommandMutation("rejected", rejection_code="invalid_world_target")
    table, owner = table_and_owner[kind]
    if db.execute(
        f"SELECT 1 FROM {table} WHERE id = ? AND {owner} = ?",
        (item_id, subject_id),
    ).fetchone() is None:
        return CommandMutation("rejected", rejection_code="world_item_not_found")

    sources: set[str] = set()
    if kind == "entity":
        for content, in db.execute("SELECT content FROM evidence_ledger"):
            try:
                value = json.loads(str(content))
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and value.get("entity_id") == item_id:
                evidence_id = value.get("evidence_id")
                if isinstance(evidence_id, str):
                    sources.add(evidence_id)
    else:
        link_table, column = {
            "relationship": ("relationship_evidence", "relationship_id"),
            "event": ("world_event_evidence", "world_event_id"),
            "cognition": ("cognition_evidence", "cognition_id"),
        }[kind]
        sources = _ids(
            db, f"SELECT evidence_id FROM {link_table} WHERE {column} = ?", item_id
        )
    if not sources:
        return CommandMutation("rejected", rejection_code="source_provenance_missing")
    if kind == "entity" and (
        db.execute(
            "SELECT 1 FROM relationship WHERE source_entity_id = ? OR target_entity_id = ?",
            (item_id, item_id),
        ).fetchone()
        or db.execute(
            "SELECT 1 FROM cognition_target WHERE target_entity_id = ? "
            "OR perspective_entity_id = ?",
            (item_id, item_id),
        ).fetchone()
    ):
        return CommandMutation("rejected", rejection_code="world_item_has_dependents")

    for evidence_id in sources:
        row = db.execute(
            "SELECT 1 FROM evidence WHERE id = ? AND subject_id = ?",
            (evidence_id, subject_id),
        ).fetchone()
        if row is None:
            return CommandMutation("rejected", rejection_code="source_provenance_missing")
        for other_kind, link_table, parent in (
            ("relationship", "relationship_evidence", "relationship_id"),
            ("event", "world_event_evidence", "world_event_id"),
            ("cognition", "cognition_evidence", "cognition_id"),
        ):
            for linked_id, in db.execute(
                f"SELECT {parent} FROM {link_table} WHERE evidence_id = ?",
                (evidence_id,),
            ):
                if other_kind != kind or str(linked_id) != item_id:
                    return CommandMutation("rejected", rejection_code="source_evidence_shared")
        ledger_owner = {
            "entity": "entity_id",
            "relationship": "relationship_id",
            "event": "world_event_id",
            "cognition": "cognition_id",
        }[kind]
        ledger_item_fields = (
            "entity_id", "relationship_id", "world_event_id", "cognition_id"
        )
        for content, in db.execute("SELECT content FROM evidence_ledger"):
            try:
                value = json.loads(str(content))
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and value.get("evidence_id") == evidence_id:
                # The source's own formation ledger is provenance, not a
                # second consumer. Another target (including another kind)
                # still makes deletion of this source unsafe.
                if value.get(ledger_owner) != item_id or any(
                    field != ledger_owner and field in value
                    for field in ledger_item_fields
                ):
                    return CommandMutation("rejected", rejection_code="source_evidence_shared")
        if db.execute(
            "SELECT 1 FROM event_evidence WHERE evidence_id = ?", (evidence_id,)
        ).fetchone():
            return CommandMutation("rejected", rejection_code="source_evidence_shared")

    # A source can still reject its own deletion (for example, a historical
    # soft tombstone with no recoverable origin). Keep the marker and every
    # source mutation in a savepoint so the World item remains unchanged if
    # any source rejects, including one after earlier sources were removed.
    db.execute("SAVEPOINT delete_world_item_sources")
    try:
        db.execute(
            "INSERT OR IGNORE INTO world_delete_marker "
            "(subject_id, object_kind, item_id, deleted_at) VALUES (?, ?, ?, ?)",
            (subject_id, kind, item_id, completed_at),
        )
        for evidence_id in sorted(sources):
            source_command = dict(command)
            source_command["target_kind"] = "evidence"
            source_command["target_id"] = evidence_id
            result = delete_evidence(
                db, source_command, completed_at, subject_id  # type: ignore[arg-type]
            )
            if result.result_state != "applied":
                db.execute("ROLLBACK TO SAVEPOINT delete_world_item_sources")
                db.execute("RELEASE SAVEPOINT delete_world_item_sources")
                return CommandMutation(
                    "rejected",
                    rejection_code=(result.rejection_code or "source_already_deleted"),
                )
        db.execute("RELEASE SAVEPOINT delete_world_item_sources")
        return CommandMutation("applied", (item_id, *sorted(sources)))
    except BaseException:
        db.execute("ROLLBACK TO SAVEPOINT delete_world_item_sources")
        db.execute("RELEASE SAVEPOINT delete_world_item_sources")
        raise
