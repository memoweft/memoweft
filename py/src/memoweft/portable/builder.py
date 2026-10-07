"""Build a deterministic Portable v4 bundle from a live MemoWeft database.

Read-only and deterministic: the 1.0 core sections (evidence / event /
cognition + links + interaction contexts + semantic resolutions) plus the 2.0
World sections plus durable history/currentness (tombstones, retractions,
transitions, lifecycle and revision metadata). Rows keep their original ids and
timestamps; raw Evidence text is preserved verbatim — portability is an
owner-level migration tool, distinct from the permission-gated recall surface.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .model import BUNDLE_FORMAT, BUNDLE_SCHEMA_VERSION, derive_bundle_id

#: The MemoWeft version stamped into the bundle header.
MEMOWEFT_VERSION = "2.0.0"


def _rows(db: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return list(db.execute(sql, params).fetchall())


def _json_list(value: str | None) -> list[Any]:
    try:
        decoded = json.loads(str(value) if value is not None else "[]")
    except ValueError:
        return []
    return decoded if isinstance(decoded, list) else []


def _normalize_members(value: str | None) -> list[dict[str, str]]:
    """世界事件 participants/objects：库内 snake_case 契约 → bundle camelCase。"""
    members: list[dict[str, str]] = []
    for item in _json_list(value):
        if isinstance(item, dict) and isinstance(item.get("canonical_name"), str):
            members.append(
                {
                    "canonicalName": str(item["canonical_name"]),
                    "kind": str(item.get("kind") or "thing"),
                }
            )
        elif isinstance(item, str) and item:
            members.append({"canonicalName": item, "kind": "thing"})
    return members


def build_bundle(
    db: sqlite3.Connection,
    subject_id: str,
    *,
    host_id: str,
    exported_at: str,
    export_mode: str = "full",
) -> dict[str, Any]:
    """Export one subject's complete Portable v4 bundle."""

    evidence = [
        {
            "id": str(r[0]),
            "subjectId": str(r[1]),
            "sourceKind": str(r[2]),
            "hostId": str(r[3]),
            "originId": None if r[4] is None else str(r[4]),
            "occurredAt": str(r[5]),
            "recordedAt": str(r[6]),
            "rawContent": str(r[7]),
            "summary": str(r[8]),
            "allowLocalRead": bool(r[9]),
            "allowCloudRead": bool(r[10]),
            "allowInference": bool(r[11]),
            "correctsEvidenceId": None if r[12] is None else str(r[12]),
            "deletedAt": None if r[13] is None else str(r[13]),
        }
        for r in _rows(
            db,
            "SELECT id, subject_id, source_kind, host_id, origin_id, occurred_at, "
            "recorded_at, raw_content, summary, allow_local_read, allow_cloud_read, "
            "allow_inference, corrects_evidence_id, deleted_at FROM evidence "
            "WHERE subject_id = ? ORDER BY recorded_at, id",
            (subject_id,),
        )
    ]
    events = [
        {
            "id": str(r[0]),
            "subjectId": str(r[1]),
            "summary": str(r[2]),
            "occurredAt": str(r[3]),
            "createdAt": str(r[4]),
        }
        for r in _rows(
            db,
            "SELECT id, subject_id, summary, occurred_at, created_at FROM event "
            "WHERE subject_id = ? ORDER BY created_at, id",
            (subject_id,),
        )
    ]
    unconsolidated_event_ids = [
        str(r[0])
        for r in _rows(
            db,
            "SELECT id FROM event WHERE subject_id = ? AND consolidated = 0 "
            "ORDER BY created_at, id",
            (subject_id,),
        )
    ]
    cognitions = [
        {
            "id": str(r[0]),
            "subjectId": str(r[1]),
            "content": str(r[2]),
            "contentType": str(r[3]),
            "formedBy": str(r[4]),
            "confidence": int(r[5]),
            "credStatus": str(r[6]),
            "scope": None if r[7] is None else str(r[7]),
            "validAt": None if r[8] is None else str(r[8]),
            "invalidAt": None if r[9] is None else str(r[9]),
            "askedAt": None if r[10] is None else str(r[10]),
            "archivedAt": None if r[11] is None else str(r[11]),
            "mutedAt": None if r[12] is None else str(r[12]),
            "createdAt": str(r[13]),
            "updatedAt": str(r[14]),
        }
        for r in _rows(
            db,
            "SELECT id, subject_id, content, content_type, formed_by, confidence, "
            "cred_status, scope, valid_at, invalid_at, asked_at, archived_at, "
            "muted_at, created_at, updated_at FROM cognition "
            "WHERE subject_id = ? ORDER BY created_at, id",
            (subject_id,),
        )
    ]
    event_evidence = [
        {"eventId": str(r[0]), "evidenceId": str(r[1])}
        for r in _rows(
            db,
            "SELECT event_id, evidence_id FROM event_evidence "
            "WHERE event_id IN (SELECT id FROM event WHERE subject_id = ?) "
            "ORDER BY event_id, evidence_id",
            (subject_id,),
        )
    ]
    cognition_evidence = [
        {"cognitionId": str(r[0]), "evidenceId": str(r[1]), "relation": str(r[2])}
        for r in _rows(
            db,
            "SELECT cognition_id, evidence_id, relation FROM cognition_evidence "
            "WHERE cognition_id IN (SELECT id FROM cognition WHERE subject_id = ?) "
            "ORDER BY cognition_id, evidence_id, relation",
            (subject_id,),
        )
    ]
    interaction_contexts = []
    for r in _rows(
        db,
        "SELECT id, subject_id, conversation_id, episode_id, context_json, "
        "context_hash, created_at FROM interaction_context WHERE subject_id = ? "
        "ORDER BY created_at, id",
        (subject_id,),
    ):
        interaction_contexts.append(
            {
                "id": str(r[0]),
                "subjectId": str(r[1]),
                "conversationId": str(r[2]),
                "episodeId": str(r[3]),
                "context": _json_list(r[4]),
                "contextHash": str(r[5]),
                "createdAt": str(r[6]),
            }
        )
    semantic_resolutions = [
        {
            "id": str(r[0]),
            "evidenceId": str(r[1]),
            "resolvedContent": str(r[2]),
            "responseAct": None if r[3] is None else str(r[3]),
            "promptAct": None if r[4] is None else str(r[4]),
            "propositionOrigin": None if r[5] is None else str(r[5]),
            "assertionStrength": None if r[6] is None else str(r[6]),
            "requiredContext": None if r[7] is None else str(r[7]),
            "resolverVersion": str(r[8]),
            "createdAt": str(r[9]),
        }
        for r in _rows(
            db,
            "SELECT id, evidence_id, resolved_content, response_act, prompt_act, "
            "proposition_origin, assertion_strength, required_context, "
            "resolver_version, created_at FROM semantic_resolution "
            "WHERE evidence_id IN (SELECT id FROM evidence WHERE subject_id = ?) "
            "ORDER BY created_at, id",
            (subject_id,),
        )
    ]

    # ── 2.0 World sections (Python-owned tables) ──
    entities = [
        {
            "id": str(r[0]),
            "worldId": str(r[1]),
            "kind": str(r[2]),
            "canonicalName": str(r[3]),
            "aliases": _json_list(r[4]),
            "invalidAt": None if r[5] is None else str(r[5]),
            "createdAt": str(r[6]),
            "updatedAt": str(r[7]),
        }
        for r in _rows(
            db,
            "SELECT id, world_id, kind, canonical_name, aliases_json, invalid_at, "
            "created_at, updated_at FROM entity WHERE world_id = ? "
            "ORDER BY created_at, id",
            (subject_id,),
        )
    ]
    entity_evidence: list[dict[str, Any]] = []
    subject_entity_ids = {str(item["id"]) for item in entities}
    subject_evidence_ids = {str(item["id"]) for item in evidence}
    for content_json, payload_json in _rows(
        db,
        "SELECT content, payload_json FROM evidence_ledger ORDER BY id",
    ):
        try:
            content = json.loads(str(content_json))
            payload = json.loads(str(payload_json))
        except (TypeError, ValueError):
            continue
        if not (
            isinstance(content, dict)
            and content.get("relation") == "support"
            and isinstance(content.get("entity_id"), str)
            and content["entity_id"] in subject_entity_ids
            and isinstance(content.get("evidence_id"), str)
            and content["evidence_id"] in subject_evidence_ids
            and isinstance(payload, dict)
            and payload.get("schema_version") == 1
        ):
            continue
        start = payload.get("start")
        end = payload.get("end")
        entity_evidence.append(
            {
                "entityId": content["entity_id"],
                "evidenceId": content["evidence_id"],
                "relation": "support",
                "start": start if isinstance(start, int) and not isinstance(start, bool) else None,
                "end": end if isinstance(end, int) and not isinstance(end, bool) else None,
            }
        )
    entity_evidence.sort(
        key=lambda item: (
            str(item["entityId"]),
            str(item["evidenceId"]),
            -1 if item["start"] is None else int(item["start"]),
            -1 if item["end"] is None else int(item["end"]),
        )
    )
    relationships = [
        {
            "id": str(r[0]),
            "worldId": str(r[1]),
            "sourceEntityId": str(r[2]),
            "targetEntityId": str(r[3]),
            "relationType": str(r[4]),
            "content": str(r[5]),
            "formedBy": str(r[6]),
            "confidence": int(r[7]),
            "credStatus": str(r[8]),
            "invalidAt": None if r[9] is None else str(r[9]),
            "createdAt": str(r[10]),
            "updatedAt": str(r[11]),
        }
        for r in _rows(
            db,
            "SELECT id, world_id, source_entity_id, target_entity_id, "
            "relation_type, content, formed_by, confidence, cred_status, "
            "invalid_at, created_at, updated_at FROM relationship "
            "WHERE world_id = ? ORDER BY created_at, id",
            (subject_id,),
        )
    ]
    relationship_evidence = [
        {"relationshipId": str(r[0]), "evidenceId": str(r[1]), "relation": str(r[2])}
        for r in _rows(
            db,
            "SELECT relationship_id, evidence_id, relation FROM relationship_evidence "
            "WHERE relationship_id IN "
            "(SELECT id FROM relationship WHERE world_id = ?) "
            "ORDER BY relationship_id, evidence_id, relation",
            (subject_id,),
        )
    ]
    world_events = [
        {
            "id": str(r[0]),
            "worldId": str(r[1]),
            "content": str(r[2]),
            "occurredAt": None if r[3] is None else str(r[3]),
            "timeExpression": None if r[4] is None else str(r[4]),
            "participants": _normalize_members(r[5]),
            "objects": _normalize_members(r[6]),
            "formedBy": str(r[7]),
            "confidence": int(r[8]),
            "credStatus": str(r[9]),
            "invalidAt": None if r[10] is None else str(r[10]),
            "createdAt": str(r[11]),
            "updatedAt": str(r[12]),
        }
        for r in _rows(
            db,
            "SELECT id, world_id, content, occurred_at, time_expression, "
            "participants_json, objects_json, formed_by, confidence, cred_status, "
            "invalid_at, created_at, updated_at FROM world_event "
            "WHERE world_id = ? ORDER BY created_at, id",
            (subject_id,),
        )
    ]
    world_event_evidence = [
        {"worldEventId": str(r[0]), "evidenceId": str(r[1]), "relation": str(r[2])}
        for r in _rows(
            db,
            "SELECT world_event_id, evidence_id, relation FROM world_event_evidence "
            "WHERE world_event_id IN (SELECT id FROM world_event WHERE world_id = ?) "
            "ORDER BY world_event_id, evidence_id, relation",
            (subject_id,),
        )
    ]
    cognition_targets = [
        {
            "cognitionId": str(r[0]),
            "targetEntityId": str(r[1]),
            "perspectiveEntityId": None if r[2] is None else str(r[2]),
        }
        for r in _rows(
            db,
            "SELECT cognition_id, target_entity_id, perspective_entity_id "
            "FROM cognition_target WHERE cognition_id IN "
            "(SELECT id FROM cognition WHERE subject_id = ?) "
            "ORDER BY cognition_id",
            (subject_id,),
        )
    ]

    # ── v4 durable history/currentness ──
    retractions = [
        {
            "id": str(r[0]),
            "priorCognitionId": None if r[1] is None else str(r[1]),
            "priorRelationshipId": None if r[2] is None else str(r[2]),
            "reason": str(r[3]),
            "revision": int(r[4]),
            "createdAt": str(r[5]),
            "priorEventId": None if r[6] is None else str(r[6]),
        }
        for r in _rows(
            db,
            "SELECT id, prior_cognition_id, prior_relationship_id, reason, "
            "revision, created_at, prior_event_id FROM retraction WHERE "
            "prior_cognition_id IN (SELECT id FROM cognition WHERE subject_id = ?) "
            "OR prior_relationship_id IN "
            "(SELECT id FROM relationship WHERE world_id = ?) "
            "OR prior_event_id IN (SELECT id FROM world_event WHERE world_id = ?) "
            "ORDER BY revision, id",
            (subject_id, subject_id, subject_id),
        )
    ]
    cognition_transitions = [
        {
            "id": str(r[0]),
            "priorCognitionId": str(r[1]),
            "replacementCognitionId": str(r[2]),
            "reason": str(r[3]),
            "revision": int(r[4]),
        }
        for r in _rows(
            db,
            "SELECT id, prior_cognition_id, replacement_cognition_id, reason, "
            "revision FROM cognition_transitions WHERE prior_cognition_id IN "
            "(SELECT id FROM cognition WHERE subject_id = ?) OR "
            "replacement_cognition_id IN "
            "(SELECT id FROM cognition WHERE subject_id = ?) "
            "ORDER BY revision, id",
            (subject_id, subject_id),
        )
    ]
    world_item_lifecycle = [
        {
            "subjectId": str(r[0]),
            "objectKind": str(r[1]),
            "itemId": str(r[2]),
            "archivedAt": None if r[3] is None else str(r[3]),
            "mutedAt": None if r[4] is None else str(r[4]),
            "updatedAt": str(r[5]),
        }
        for r in _rows(
            db,
            "SELECT subject_id, object_kind, item_id, archived_at, muted_at, "
            "updated_at FROM world_item_lifecycle WHERE subject_id = ? "
            "ORDER BY object_kind, item_id",
            (subject_id,),
        )
    ]
    revision_row = db.execute(
        "SELECT revision, snapshot_hash FROM memory_state WHERE singleton = 1"
    ).fetchone()
    world_revision = 0 if revision_row is None else int(revision_row[0])
    world_snapshot_hash = "" if revision_row is None else str(revision_row[1])

    counts = {
        "evidence": len(evidence),
        "events": len(events),
        "cognitions": len(cognitions),
        "entities": len(entities),
        "entityEvidence": len(entity_evidence),
        "relationships": len(relationships),
        "worldEvents": len(world_events),
        "retractions": len(retractions),
        "cognitionTransitions": len(cognition_transitions),
        "worldItemLifecycle": len(world_item_lifecycle),
    }
    bundle: dict[str, Any] = {
        "format": BUNDLE_FORMAT,
        "schemaVersion": BUNDLE_SCHEMA_VERSION,
        "exportedAt": exported_at,
        "memoWeftVersion": MEMOWEFT_VERSION,
        "subjectId": subject_id,
        "sourceSubjectId": subject_id,
        "worldRevision": world_revision,
        "worldSnapshotHash": world_snapshot_hash,
        "source": {"hostId": host_id, "exportMode": export_mode},
        "data": {
            "evidence": evidence,
            "events": events,
            "eventEvidence": event_evidence,
            "cognitions": cognitions,
            "cognitionEvidence": cognition_evidence,
            "unconsolidatedEventIds": unconsolidated_event_ids,
            "interactionContexts": interaction_contexts,
            "semanticResolutions": semantic_resolutions,
            "entities": entities,
            "entityEvidence": entity_evidence,
            "relationships": relationships,
            "relationshipEvidence": relationship_evidence,
            "worldEvents": world_events,
            "worldEventEvidence": world_event_evidence,
            "cognitionTargets": cognition_targets,
            "retractions": retractions,
            "cognitionTransitions": cognition_transitions,
            "worldItemLifecycle": world_item_lifecycle,
        },
        "metadata": {"counts": counts, "notes": []},
    }
    bundle["bundleId"] = derive_bundle_id(bundle)
    return bundle
