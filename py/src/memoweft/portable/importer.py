"""导入便携记忆包，并保持 TypeScript importBundle 的完整 ImportPlan 语义。

保真 + 幂等 + 不污染:
  - 保真:按【原 id 与时间戳】落库(store.insert),溯源链不丢。
  - 幂等去重:按 id 判重,已存在则跳过(计 duplicates)。
  - 引用完整:evidence 因 originId 撞库中【另一条不同 id】而无法落库时标记悬空,**连带丢弃指向它的 join 行**并告警;
    悬空 correctsEvidenceId 落库前置空——绝不写出悬空引用。
  - 不污染:非法包(validate_bundle 不过)绝不写库;merge 写入包进事务(若传),中途失败整体回滚。
dryRun:只算不写。
"""
from __future__ import annotations

import copy
from collections import Counter
import json
from hashlib import sha256
import sqlite3
from typing import Any, Optional

from ..config import resolve_lang
from ..store.cognition import SqliteCognitionStore
from ..store.event import SqliteEventStore
from ..store.evidence import SqliteEvidenceStore
from ..store.interaction_context import SqliteInteractionContextStore
from ..store.semantic_resolution import SqliteSemanticResolutionStore
from ..store.transaction import Transaction
from ..store._tombstones import is_evidence_tombstoned
from ..types import (
    Cognition,
    Event,
    Evidence,
    EvidenceLink,
    InteractionContext,
    SemanticResolution,
    VisibleTurn,
)
from .model import (
    ImportCounts,
    ImportDuplicates,
    ImportMode,
    ImportPlan,
    canonical_sha256,
    derive_bundle_id,
    derive_plan_ids,
)
from .validate import validate_bundle


def _to_evidence(d: dict[str, Any]) -> Evidence:
    return Evidence(
        id=d["id"], subject_id=d["subjectId"], source_kind=d["sourceKind"], host_id=d["hostId"],
        origin_id=d.get("originId"), occurred_at=d["occurredAt"], recorded_at=d["recordedAt"],
        raw_content=d["rawContent"], summary=d["summary"], allow_local_read=bool(d["allowLocalRead"]),
        allow_cloud_read=bool(d["allowCloudRead"]), allow_inference=bool(d["allowInference"]),
        corrects_evidence_id=d.get("correctsEvidenceId"),
    )


def _to_event(d: dict[str, Any]) -> Event:
    return Event(id=d["id"], subject_id=d["subjectId"], summary=d["summary"], occurred_at=d["occurredAt"], created_at=d["createdAt"])


def _to_cognition(d: dict[str, Any]) -> Cognition:
    return Cognition(
        id=d["id"], subject_id=d["subjectId"], content=d["content"], content_type=d["contentType"],
        formed_by=d["formedBy"], confidence=d["confidence"], cred_status=d["credStatus"], scope=d.get("scope"),
        valid_at=d.get("validAt"), invalid_at=d.get("invalidAt"), asked_at=d.get("askedAt"),
        archived_at=d.get("archivedAt"), muted_at=d.get("mutedAt"), created_at=d["createdAt"], updated_at=d["updatedAt"],
    )


def _to_interaction_context(d: dict[str, Any]) -> InteractionContext:
    return InteractionContext(
        id=d["id"], subject_id=d["subjectId"], conversation_id=d["conversationId"], episode_id=d["episodeId"],
        context=[VisibleTurn(
            role=t["role"], content=t["content"], source_ref=t.get("source_ref"),
            message_id=t.get("message_id"), timestamp=t.get("timestamp"),
            model_context_dependencies=t.get("model_context_dependencies"),
        ) for t in d["context"]],
        context_hash=d["contextHash"], created_at=d["createdAt"],
    )


def _to_semantic_resolution(d: dict[str, Any]) -> SemanticResolution:
    return SemanticResolution(
        id=d["id"], evidence_id=d["evidenceId"], resolved_content=d["resolvedContent"],
        response_act=d.get("responseAct"), prompt_act=d.get("promptAct"), proposition_origin=d.get("propositionOrigin"),
        assertion_strength=d.get("assertionStrength"), required_context=d.get("requiredContext"),
        resolver_version=d["resolverVersion"], created_at=d["createdAt"],
    )


def _same_string_multiset(left: list[str], right: list[str]) -> bool:
    return sorted(left) == sorted(right)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _to_snake_members(members: Any) -> list[dict[str, str]]:
    """bundle camelCase 参与者/对象 → 库内 snake_case 契约。"""
    out: list[dict[str, str]] = []
    for member in members or []:
        if isinstance(member, dict) and isinstance(member.get("canonicalName"), str):
            out.append(
                {
                    "canonical_name": str(member["canonicalName"]),
                    "kind": str(member.get("kind") or "thing"),
                }
            )
    return out


def _remap_bundle_subject(bundle: dict[str, Any], target_subject_id: str) -> dict[str, Any]:
    """Return a private import view with every ownership field remapped.

    Object and provenance ids intentionally remain stable.  The source bundle
    bytes/identity are never rewritten; this copy is only the target plan.
    """

    remapped: dict[str, Any] = copy.deepcopy(bundle)
    remapped["subjectId"] = target_subject_id
    data = remapped["data"]
    for section in ("evidence", "events", "cognitions", "interactionContexts"):
        for item in data.get(section) or []:
            item["subjectId"] = target_subject_id
    for section in ("entities", "relationships", "worldEvents"):
        for item in data.get(section) or []:
            item["worldId"] = target_subject_id
    for item in data.get("worldItemLifecycle") or []:
        item["subjectId"] = target_subject_id
    return remapped


def _count_payload(counts: ImportCounts) -> dict[str, int]:
    return {
        "evidence": counts.evidence,
        "events": counts.events,
        "cognitions": counts.cognitions,
        "eventEvidence": counts.event_evidence,
        "cognitionEvidence": counts.cognition_evidence,
        "interactionContexts": counts.interaction_contexts,
        "semanticResolutions": counts.semantic_resolutions,
        "entities": counts.entities,
        "entityEvidence": counts.entity_evidence,
        "relationships": counts.relationships,
        "worldEvents": counts.world_events,
        "relationshipEvidence": counts.relationship_evidence,
        "worldEventEvidence": counts.world_event_evidence,
        "cognitionTargets": counts.cognition_targets,
        "retractions": counts.retractions,
        "cognitionTransitions": counts.cognition_transitions,
        "worldItemLifecycle": counts.world_item_lifecycle,
        "evidenceTombstones": counts.evidence_tombstones,
    }


def _duplicate_payload(duplicates: ImportDuplicates) -> dict[str, int]:
    return {
        "evidence": duplicates.evidence,
        "events": duplicates.events,
        "cognitions": duplicates.cognitions,
        "entities": duplicates.entities,
        "relationships": duplicates.relationships,
        "worldEvents": duplicates.world_events,
        "retractions": duplicates.retractions,
        "cognitionTransitions": duplicates.cognition_transitions,
        "worldItemLifecycle": duplicates.world_item_lifecycle,
    }


def _finalize_v4_plan(plan: ImportPlan, write_set: dict[str, list[str]]) -> ImportPlan:
    """Freeze the deterministic v4 plan identity before any apply mutation."""

    normalized_write_set = {
        name: sorted(str(value) for value in values)
        for name, values in sorted(write_set.items())
    }
    plan.would_advance_revision = plan.valid and any(
        value > 0 for value in _count_payload(plan.counts).values()
    )
    payload = {
        "planVersion": 1,
        "bundleId": plan.bundle_id,
        "sourceSubjectId": plan.source_subject_id,
        "targetSubjectId": plan.target_subject_id,
        "targetWorldRevision": plan.target_world_revision,
        "targetSnapshotHash": plan.target_snapshot_hash,
        "valid": plan.valid,
        "conflicts": plan.conflicts,
        "warnings": plan.warnings,
        "counts": _count_payload(plan.counts),
        "duplicates": _duplicate_payload(plan.duplicates),
        "writeSet": normalized_write_set,
        "wouldAdvanceRevision": plan.would_advance_revision,
    }
    plan.plan_hash, plan.command_id, plan.receipt_id = derive_plan_ids(payload)
    return plan


def _record_conflict(plan: ImportPlan, kind: str, item_id: str, code: str) -> None:
    plan.conflicts.append({"kind": kind, "id": item_id, "code": code})


def _plan_world_import(
    world_db: Optional[sqlite3.Connection],
    data: dict[str, Any],
    plan: ImportPlan,
    lang: str,
) -> dict[str, Any]:
    """Plan the 2.0 World sections (entities/relationships/worldEvents + links).

    Collision rule mirrors the 1.0 contract: a same-id row is a safe duplicate
    only when the complete row is identical; any difference rejects the whole
    bundle (fail-closed, zero writes).  Returns the rows to insert, keyed for
    the write phase.
    """
    world: dict[str, Any] = {
        "entities": [],
        "entity_evidence": [],
        "entity_evidence_keys": [],
        "relationships": [],
        "world_events": [],
        "relationship_evidence": [],
        "world_event_evidence": [],
        "cognition_targets": [],
        "retractions": [],
        "cognition_transitions": [],
        "world_item_lifecycle": [],
    }
    present = any(
        bool(data.get(key))
        for key in (
            "entities",
            "entityEvidence",
            "relationships",
            "relationshipEvidence",
            "worldEvents",
            "worldEventEvidence",
            "cognitionTargets",
            "retractions",
            "cognitionTransitions",
            "worldItemLifecycle",
        )
    )
    if not present:
        return world
    if world_db is None:
        plan.warnings.append(
            "World sections skipped: no world_db connection provided"
            if lang == "en"
            else "World 段未导入：未提供 world_db 连接"
        )
        return world

    def collision_guard(
        table: str,
        row_id: str,
        expected: dict[str, object],
        *,
        duplicate_field: str | None = None,
    ) -> bool:
        columns = tuple(expected.keys())
        existing = world_db.execute(
            f"SELECT {', '.join(columns)} FROM {table} WHERE id = ?", (row_id,)
        ).fetchone()
        if existing is None:
            return True  # 可插入
        if tuple(existing) == tuple(expected[column] for column in columns):
            if duplicate_field is not None:
                setattr(
                    plan.duplicates,
                    duplicate_field,
                    getattr(plan.duplicates, duplicate_field) + 1,
                )
            return False  # 完全相同 → 幂等跳过
        _record_conflict(plan, table, row_id, "same_id_different_content")
        plan.errors.append(
            f"{table} {row_id} collides with a different target record; import rejected"
            if lang == "en"
            else f"{table} {row_id} 与目标库同 id 记录不一致，拒绝导入"
        )
        return False

    def link_insert_list(
        link_table: str,
        columns: tuple[str, ...],
        bundle_links: Any,
        mapping: dict[str, object],
    ) -> list[tuple[object, ...]]:
        new_links: list[tuple[object, ...]] = []
        for link in bundle_links or []:
            values = tuple(link[mapping[column]] for column in columns)
            clauses: list[str] = []
            params: list[object] = []
            for column, value in zip(columns, values):
                if value is None:
                    clauses.append(f"{column} IS NULL")
                else:
                    clauses.append(f"{column} = ?")
                    params.append(value)
            existing = world_db.execute(
                f"SELECT 1 FROM {link_table} WHERE {' AND '.join(clauses)}",
                params,
            ).fetchone()
            if existing is None:
                new_links.append(values)
        return new_links

    def guard_owned_links(
        *,
        parent_table: str,
        parent_id: str,
        conflict_kind: str,
        link_table: str,
        parent_column: str,
        value_columns: tuple[str, ...],
        expected_values: list[tuple[object, ...]],
    ) -> None:
        """Treat an existing object's owned links as part of its collision identity."""

        parent_exists = world_db.execute(
            f"SELECT 1 FROM {parent_table} WHERE id = ?", (parent_id,)
        ).fetchone()
        if parent_exists is None:
            return
        existing_values = world_db.execute(
            f"SELECT {', '.join(value_columns)} FROM {link_table} "
            f"WHERE {parent_column} = ?",
            (parent_id,),
        ).fetchall()
        if Counter(tuple(row) for row in existing_values) == Counter(expected_values):
            return
        _record_conflict(
            plan,
            conflict_kind,
            parent_id,
            "same_id_different_provenance",
        )
        plan.errors.append(
            f"{conflict_kind} {parent_id} collides with different target provenance links; import rejected"
            if lang == "en"
            else f"{conflict_kind} {parent_id} 与目标库同 id 记录的溯源关系不一致，拒绝导入"
        )

    for item in data.get("entities") or []:
        expected = {
            "id": item["id"],
            "world_id": item["worldId"],
            "kind": item["kind"],
            "canonical_name": item["canonicalName"],
            "aliases_json": _canonical_json(item.get("aliases") or []),
            "invalid_at": item.get("invalidAt"),
            "created_at": item["createdAt"],
            "updated_at": item["updatedAt"],
        }
        if not collision_guard(
            "entity", item["id"], expected, duplicate_field="entities"
        ):
            continue
        canonical_collision = world_db.execute(
            "SELECT id FROM entity WHERE world_id = ? AND canonical_name = ?",
            (expected["world_id"], expected["canonical_name"]),
        ).fetchone()
        if canonical_collision is not None:
            # The same ID path above is the only idempotent duplicate.  A
            # different ID with this natural key would violate the target
            # unique index during merge, so dry-run must report it identically
            # and fail before any write is attempted.
            _record_conflict(
                plan,
                "entity",
                item["id"],
                "same_canonical_name_different_id",
            )
            plan.errors.append(
                "entity "
                f"{item['id']} collides with target entity "
                f"{canonical_collision[0]} on world_id/canonical_name; import rejected"
                if lang == "en"
                else "entity "
                f"{item['id']} 与目标 entity {canonical_collision[0]} 的 "
                "world_id/canonical_name 唯一键冲突，拒绝导入"
            )
            continue
        world["entities"].append(
            (
                expected["id"],
                expected["world_id"],
                expected["kind"],
                expected["canonical_name"],
                expected["aliases_json"],
                expected["invalid_at"],
                expected["created_at"],
                expected["updated_at"],
            )
        )

    entity_links: dict[str, list[tuple[object, ...]]] = {}
    for link in data.get("entityEvidence") or []:
        entity_links.setdefault(link["entityId"], []).append(
            (
                link["evidenceId"],
                link["relation"],
                link.get("start"),
                link.get("end"),
            )
        )

    def existing_entity_links(entity_id: str) -> list[tuple[object, ...]]:
        links: list[tuple[object, ...]] = []
        for content_json, payload_json in world_db.execute(
            "SELECT content, payload_json FROM evidence_ledger ORDER BY id"
        ).fetchall():
            try:
                content = json.loads(str(content_json))
                payload = json.loads(str(payload_json))
            except (TypeError, ValueError):
                continue
            if not (
                isinstance(content, dict)
                and content.get("entity_id") == entity_id
                and content.get("relation") == "support"
                and isinstance(content.get("evidence_id"), str)
                and isinstance(payload, dict)
                and payload.get("schema_version") == 1
            ):
                continue
            links.append(
                (
                    content["evidence_id"],
                    "support",
                    payload.get("start") if isinstance(payload.get("start"), int) else None,
                    payload.get("end") if isinstance(payload.get("end"), int) else None,
                )
            )
        return links

    if "entityEvidence" in data:
        for item in data.get("entities") or []:
            parent_exists = world_db.execute(
                "SELECT 1 FROM entity WHERE id = ?", (item["id"],)
            ).fetchone()
            if parent_exists is not None and Counter(
                existing_entity_links(item["id"])
            ) != Counter(entity_links.get(item["id"], [])):
                _record_conflict(
                    plan,
                    "entity",
                    item["id"],
                    "same_id_different_provenance",
                )
                plan.errors.append(
                    f"entity {item['id']} collides with different target provenance links; import rejected"
                    if lang == "en"
                    else f"entity {item['id']} 与目标库同 id 记录的溯源关系不一致，拒绝导入"
                )

    for link in data.get("entityEvidence") or []:
        semantic_link = (
            link["evidenceId"],
            link["relation"],
            link.get("start"),
            link.get("end"),
        )
        if semantic_link in existing_entity_links(link["entityId"]):
            continue
        content = _canonical_json(
            {
                "entity_id": link["entityId"],
                "evidence_id": link["evidenceId"],
                "relation": link["relation"],
            }
        )
        payload = _canonical_json(
            {
                "schema_version": 1,
                "start": link.get("start"),
                "end": link.get("end"),
            }
        )
        ledger_id = "evidence-ledger-" + canonical_sha256(
            [
                "entity_formation",
                link["entityId"],
                link["evidenceId"],
                link.get("start"),
                link.get("end"),
            ]
        )
        existing = world_db.execute(
            "SELECT content, payload_json FROM evidence_ledger WHERE id = ?",
            (ledger_id,),
        ).fetchone()
        if existing is None:
            world["entity_evidence"].append((ledger_id, content, payload))
            world["entity_evidence_keys"].append(
                f"{link['entityId']}/{link['evidenceId']}/{link['relation']}/"
                f"{'' if link.get('start') is None else link['start']}/"
                f"{'' if link.get('end') is None else link['end']}"
            )
        elif tuple(existing) != (content, payload):
            _record_conflict(plan, "entity_evidence", ledger_id, "same_id_different_content")
            plan.errors.append(
                f"entity_evidence {ledger_id} collides with a different target record; import rejected"
                if lang == "en"
                else f"entity_evidence {ledger_id} 与目标库同 id 记录不一致，拒绝导入"
            )
    for item in data.get("relationships") or []:
        expected = {
            "id": item["id"],
            "world_id": item["worldId"],
            "source_entity_id": item["sourceEntityId"],
            "target_entity_id": item["targetEntityId"],
            "relation_type": item["relationType"],
            "content": item["content"],
            "formed_by": item["formedBy"],
            "confidence": item["confidence"],
            "cred_status": item["credStatus"],
            "invalid_at": item.get("invalidAt"),
            "created_at": item["createdAt"],
            "updated_at": item["updatedAt"],
        }
        if collision_guard(
            "relationship",
            item["id"],
            expected,
            duplicate_field="relationships",
        ):
            world["relationships"].append(
                (
                    expected["id"],
                    expected["world_id"],
                    expected["source_entity_id"],
                    expected["target_entity_id"],
                    expected["relation_type"],
                    expected["content"],
                    expected["formed_by"],
                    expected["confidence"],
                    expected["cred_status"],
                    expected["invalid_at"],
                    expected["created_at"],
                    expected["updated_at"],
                )
            )
    for item in data.get("worldEvents") or []:
        expected = {
            "id": item["id"],
            "world_id": item["worldId"],
            "content": item["content"],
            "occurred_at": item.get("occurredAt"),
            "time_expression": item.get("timeExpression"),
            "participants_json": _canonical_json(
                _to_snake_members(item.get("participants"))
            ),
            "objects_json": _canonical_json(_to_snake_members(item.get("objects"))),
            "formed_by": item["formedBy"],
            "confidence": item["confidence"],
            "cred_status": item["credStatus"],
            "invalid_at": item.get("invalidAt"),
            "created_at": item["createdAt"],
            "updated_at": item["updatedAt"],
        }
        if collision_guard(
            "world_event",
            item["id"],
            expected,
            duplicate_field="world_events",
        ):
            world["world_events"].append(
                (
                    expected["id"],
                    expected["world_id"],
                    expected["content"],
                    expected["occurred_at"],
                    expected["time_expression"],
                    expected["participants_json"],
                    expected["objects_json"],
                    expected["formed_by"],
                    expected["confidence"],
                    expected["cred_status"],
                    expected["invalid_at"],
                    expected["created_at"],
                    expected["updated_at"],
                )
            )

    relationship_links: dict[str, list[tuple[object, ...]]] = {}
    for link in data.get("relationshipEvidence") or []:
        relationship_links.setdefault(link["relationshipId"], []).append(
            (link["evidenceId"], link["relation"])
        )
    for item in data.get("relationships") or []:
        guard_owned_links(
            parent_table="relationship",
            parent_id=item["id"],
            conflict_kind="relationship",
            link_table="relationship_evidence",
            parent_column="relationship_id",
            value_columns=("evidence_id", "relation"),
            expected_values=relationship_links.get(item["id"], []),
        )

    world_event_links: dict[str, list[tuple[object, ...]]] = {}
    for link in data.get("worldEventEvidence") or []:
        world_event_links.setdefault(link["worldEventId"], []).append(
            (link["evidenceId"], link["relation"])
        )
    for item in data.get("worldEvents") or []:
        guard_owned_links(
            parent_table="world_event",
            parent_id=item["id"],
            conflict_kind="world_event",
            link_table="world_event_evidence",
            parent_column="world_event_id",
            value_columns=("evidence_id", "relation"),
            expected_values=world_event_links.get(item["id"], []),
        )

    cognition_target_links: dict[str, list[tuple[object, ...]]] = {}
    for link in data.get("cognitionTargets") or []:
        cognition_target_links.setdefault(link["cognitionId"], []).append(
            (link["targetEntityId"], link.get("perspectiveEntityId"))
        )
    for item in data.get("cognitions") or []:
        guard_owned_links(
            parent_table="cognition",
            parent_id=item["id"],
            conflict_kind="cognition_target",
            link_table="cognition_target",
            parent_column="cognition_id",
            value_columns=("target_entity_id", "perspective_entity_id"),
            expected_values=cognition_target_links.get(item["id"], []),
        )

    world["relationship_evidence"] = link_insert_list(
        "relationship_evidence",
        ("relationship_id", "evidence_id", "relation"),
        data.get("relationshipEvidence"),
        {"relationship_id": "relationshipId", "evidence_id": "evidenceId", "relation": "relation"},
    )
    world["world_event_evidence"] = link_insert_list(
        "world_event_evidence",
        ("world_event_id", "evidence_id", "relation"),
        data.get("worldEventEvidence"),
        {"world_event_id": "worldEventId", "evidence_id": "evidenceId", "relation": "relation"},
    )
    world["cognition_targets"] = link_insert_list(
        "cognition_target",
        ("cognition_id", "target_entity_id", "perspective_entity_id"),
        data.get("cognitionTargets"),
        {
            "cognition_id": "cognitionId",
            "target_entity_id": "targetEntityId",
            "perspective_entity_id": "perspectiveEntityId",
        },
    )
    for item in data.get("retractions") or []:
        expected = {
            "id": item["id"],
            "prior_cognition_id": item.get("priorCognitionId"),
            "prior_relationship_id": item.get("priorRelationshipId"),
            "reason": item["reason"],
            "revision": item["revision"],
            "created_at": item["createdAt"],
            "prior_event_id": item.get("priorEventId"),
        }
        if collision_guard(
            "retraction",
            item["id"],
            expected,
            duplicate_field="retractions",
        ):
            world["retractions"].append(tuple(expected.values()))
    for item in data.get("cognitionTransitions") or []:
        expected = {
            "id": item["id"],
            "prior_cognition_id": item["priorCognitionId"],
            "replacement_cognition_id": item["replacementCognitionId"],
            "reason": item["reason"],
            "revision": item["revision"],
        }
        if collision_guard(
            "cognition_transitions",
            item["id"],
            expected,
            duplicate_field="cognition_transitions",
        ):
            world["cognition_transitions"].append(tuple(expected.values()))
    for item in data.get("worldItemLifecycle") or []:
        expected = {
            "subject_id": item["subjectId"],
            "object_kind": item["objectKind"],
            "item_id": item["itemId"],
            "archived_at": item.get("archivedAt"),
            "muted_at": item.get("mutedAt"),
            "updated_at": item["updatedAt"],
        }
        existing = world_db.execute(
            "SELECT subject_id, object_kind, item_id, archived_at, muted_at, "
            "updated_at FROM world_item_lifecycle WHERE subject_id = ? "
            "AND object_kind = ? AND item_id = ?",
            (expected["subject_id"], expected["object_kind"], expected["item_id"]),
        ).fetchone()
        values = tuple(expected.values())
        if existing is None:
            world["world_item_lifecycle"].append(values)
        elif tuple(existing) == values:
            plan.duplicates.world_item_lifecycle += 1
        else:
            _record_conflict(
                plan,
                "world_item_lifecycle",
                f"{item['objectKind']}/{item['itemId']}",
                "same_id_different_content",
            )
            plan.errors.append(
                "world_item_lifecycle "
                f"{item['objectKind']}/{item['itemId']} collides with a different "
                "target record; import rejected"
            )
    return world


def import_bundle(
    bundle: Any,
    *,
    evidence_store: SqliteEvidenceStore,
    event_store: SqliteEventStore,
    cognition_store: SqliteCognitionStore,
    interaction_context_store: SqliteInteractionContextStore,
    semantic_resolution_store: SqliteSemanticResolutionStore,
    transaction: Optional[Transaction] = None,
    mode: ImportMode = "merge",
    world_db: Optional[sqlite3.Connection] = None,
    target_subject_id: str | None = None,
    target_world_revision: int | None = None,
    target_snapshot_hash: str | None = None,
) -> ImportPlan:
    """按共享便携包契约生成并执行导入计划。

    v3 起 2.0 World 段（entity/relationship/world_event）经 ``world_db``
    写入 Python-owned 表；``world_db`` 必须与各 store 共用同一连接（随
    transaction 原子提交）。不提供时 World 段跳过并告警，1.0 核心照常导入。
    """
    lang = resolve_lang()
    validation = validate_bundle(bundle)
    is_v4 = isinstance(bundle, dict) and bundle.get("schemaVersion") == 4
    source_subject_id = (
        str(bundle.get("sourceSubjectId") or bundle.get("subjectId"))
        if isinstance(bundle, dict)
        else None
    )
    effective_target_subject_id = target_subject_id or source_subject_id
    precondition_conflict: tuple[str, str, str] | None = None
    if is_v4 and world_db is not None:
        revision_row = world_db.execute(
            "SELECT revision, snapshot_hash FROM memory_state WHERE singleton = 1"
        ).fetchone()
        observed_revision = 0 if revision_row is None else int(revision_row[0])
        observed_snapshot_hash = "" if revision_row is None else str(revision_row[1])
        if target_world_revision is None:
            target_world_revision = observed_revision
        elif target_world_revision != observed_revision:
            precondition_conflict = (
                "world_revision",
                str(target_world_revision),
                "target_revision_changed",
            )
        if target_snapshot_hash is None:
            target_snapshot_hash = observed_snapshot_hash
        elif target_snapshot_hash != observed_snapshot_hash:
            precondition_conflict = (
                "world_snapshot",
                target_snapshot_hash,
                "target_snapshot_changed",
            )
    elif is_v4:
        if target_world_revision is None:
            target_world_revision = 0
        if target_snapshot_hash is None:
            target_snapshot_hash = ""
    plan = ImportPlan(
        mode=mode, valid=validation.valid, errors=list(validation.errors), warnings=list(validation.warnings),
        counts=ImportCounts(), duplicates=ImportDuplicates(),
        bundle_id=(derive_bundle_id(bundle) if is_v4 and isinstance(bundle, dict) else None),
        source_subject_id=source_subject_id,
        target_subject_id=effective_target_subject_id,
        target_world_revision=target_world_revision,
        target_snapshot_hash=target_snapshot_hash,
    )
    if not validation.valid:
        return _finalize_v4_plan(plan, {}) if is_v4 else plan

    if precondition_conflict is not None:
        plan.valid = False
        _record_conflict(plan, *precondition_conflict)
        plan.errors.append(precondition_conflict[2])
        return _finalize_v4_plan(plan, {})

    if is_v4 and (not effective_target_subject_id):
        plan.valid = False
        plan.errors.append("target_subject_id is required for Portable v4")
        return _finalize_v4_plan(plan, {})

    requires_world_db = is_v4 and isinstance(bundle, dict) and (
        any(evidence.get("deletedAt") is not None for evidence in bundle["data"]["evidence"])
        or any(
            bool(bundle["data"].get(key))
            for key in (
                "entities",
                "entityEvidence",
                "relationships",
                "relationshipEvidence",
                "worldEvents",
                "worldEventEvidence",
                "cognitionTargets",
                "retractions",
                "cognitionTransitions",
                "worldItemLifecycle",
            )
        )
    )
    if requires_world_db and world_db is None:
        plan.valid = False
        plan.errors.append("Portable v4 requires a world_db connection")
        return _finalize_v4_plan(plan, {})

    import_view = (
        _remap_bundle_subject(bundle, str(effective_target_subject_id))
        if is_v4 and isinstance(bundle, dict)
        else bundle
    )
    data = import_view["data"]
    unconsolidated_set = set(data.get("unconsolidatedEventIds") or [])

    # A hard-deleted row is a content-free suppression marker. Reject the
    # whole old bundle before planning any World rows, including a copy whose
    # Evidence has a different id but the same source origin.
    if world_db is not None:
        for evidence in data["evidence"]:
            origin = evidence.get("originId")
            marker = world_db.execute(
                "SELECT id FROM evidence WHERE subject_id = ? "
                "AND deleted_at IS NOT NULL AND raw_content = '' AND summary = '' "
                "AND (id = ? OR (origin_id IS NOT NULL AND origin_id = ?))",
                (effective_target_subject_id, evidence["id"], origin),
            ).fetchone()
            if marker is not None:
                plan.valid = False
                _record_conflict(plan, "evidence", str(evidence["id"]), "hard_deleted_source")
                plan.errors.append("hard_deleted_source")
                return _finalize_v4_plan(plan, {}) if is_v4 else plan
            if origin is not None and world_db.execute(
                "SELECT 1 FROM hard_deleted_origin WHERE origin_hash = ? "
                "AND subject_id = ?",
                (sha256(str(origin).encode("utf-8")).hexdigest(), effective_target_subject_id),
            ).fetchone():
                plan.valid = False
                _record_conflict(plan, "evidence", str(evidence["id"]), "hard_deleted_source")
                plan.errors.append("hard_deleted_source")
                return _finalize_v4_plan(plan, {}) if is_v4 else plan
        for section, kind in (
            ("entities", "entity"),
            ("relationships", "relationship"),
            ("worldEvents", "event"),
            ("cognitions", "cognition"),
        ):
            for item in data.get(section, []):
                if world_db.execute(
                    "SELECT 1 FROM world_delete_marker WHERE subject_id = ? "
                    "AND object_kind = ? AND item_id = ?",
                    (effective_target_subject_id, kind, item["id"]),
                ).fetchone():
                    plan.valid = False
                    _record_conflict(plan, section, str(item["id"]), "hard_deleted_world_item")
                    plan.errors.append("hard_deleted_world_item")
                    return _finalize_v4_plan(plan, {}) if is_v4 else plan

    # 同 id 仅在完整实体及其自有关系完全相同时才是安全幂等。否则把包内派生实体
    # 绑定到目标行会造成跨血缘授权漂白；rc.2 选择整包 fail-closed，等待显式冲突解决。
    event_links: dict[str, list[str]] = {}
    for link in data["eventEvidence"]:
        event_links.setdefault(link["eventId"], []).append(link["evidenceId"])
    cognition_link_keys: dict[str, list[str]] = {}
    for link in data["cognitionEvidence"]:
        cognition_link_keys.setdefault(link["cognitionId"], []).append(
            f"{link['evidenceId']}\0{link['relation']}"
        )

    for evidence in data["evidence"]:
        if is_v4 and world_db is not None:
            row = world_db.execute(
                "SELECT id, subject_id, source_kind, host_id, origin_id, "
                "occurred_at, recorded_at, raw_content, summary, "
                "allow_local_read, allow_cloud_read, allow_inference, "
                "corrects_evidence_id, preceding_ai_context, deleted_at "
                "FROM evidence WHERE id = ?",
                (evidence["id"],),
            ).fetchone()
            if row is None:
                continue
            # A live old backup encountering an existing target tombstone is
            # not a content collision: deletion wins monotonically below.
            if row[14] is not None and evidence.get("deletedAt") is None:
                continue
            expected_base = (
                evidence["id"],
                evidence["subjectId"],
                evidence["sourceKind"],
                evidence["hostId"],
                evidence.get("originId"),
                evidence["occurredAt"],
                evidence["recordedAt"],
                evidence["rawContent"],
                evidence["summary"],
                int(bool(evidence["allowLocalRead"])),
                int(bool(evidence["allowCloudRead"])),
                int(bool(evidence["allowInference"])),
                evidence.get("correctsEvidenceId"),
            )
            if tuple(row[:13]) != expected_base:
                _record_conflict(
                    plan,
                    "evidence",
                    evidence["id"],
                    "same_id_different_content",
                )
                plan.errors.append(
                    f"evidence {evidence['id']} collides with a different target record; import rejected"
                )
            continue
        existing_evidence = evidence_store.get(evidence["id"])
        if existing_evidence is not None and existing_evidence != _to_evidence(evidence):
            _record_conflict(
                plan, "evidence", evidence["id"], "same_id_different_content"
            )
            plan.errors.append(
                f"evidence {evidence['id']} 与目标库同 id 记录内容或授权不一致，拒绝导入"
                if lang == "zh"
                else f"evidence {evidence['id']} collides with a different target record; import rejected"
            )
    for event in data["events"]:
        existing_event = event_store.get(event["id"])
        if existing_event is None:
            continue
        same_links = _same_string_multiset(
            event_store.evidence_of(event["id"]), event_links.get(event["id"], [])
        )
        target_unconsolidated = any(
            candidate.id == event["id"] for candidate in event_store.unconsolidated(event["subjectId"])
        )
        if (
            existing_event != _to_event(event)
            or not same_links
            or target_unconsolidated != (event["id"] in unconsolidated_set)
        ):
            plan.errors.append(
                f"event {event['id']} 与目标库同 id 事件的内容、证据关系或消化状态不一致，拒绝导入"
                if lang == "zh"
                else f"event {event['id']} collides with different target content, evidence links, or consolidation state; import rejected"
            )
            _record_conflict(
                plan, "event", event["id"], "same_id_different_content"
            )
    for cognition in data["cognitions"]:
        existing_cognition = cognition_store.get(cognition["id"])
        if existing_cognition is None:
            continue
        target_sources = [
            f"{link.evidence_id}\0{link.relation}"
            for link in cognition_store.sources_of(cognition["id"])
        ]
        if (
            existing_cognition != _to_cognition(cognition)
            or not _same_string_multiset(
                target_sources, cognition_link_keys.get(cognition["id"], [])
            )
        ):
            plan.errors.append(
                f"cognition {cognition['id']} 与目标库同 id 认知的内容或溯源关系不一致，拒绝导入"
                if lang == "zh"
                else f"cognition {cognition['id']} collides with different target content or provenance links; import rejected"
            )
            _record_conflict(
                plan, "cognition", cognition["id"], "same_id_different_content"
            )
    if plan.errors:
        plan.valid = False
        return _finalize_v4_plan(plan, {}) if is_v4 else plan

    # ── v3：2.0 World 段（entity/relationship/world_event + 链接；碰撞同样整包 fail-closed）──
    world = _plan_world_import(world_db, data, plan, lang)
    if plan.errors:
        plan.valid = False
        return _finalize_v4_plan(plan, {}) if is_v4 else plan

    # ── 判重(evidence:按 id;额外防 originId 唯一约束撞车)──
    unresolved_evidence: set[str] = set()
    new_evidence: list[dict[str, Any]] = []
    evidence_tombstone_updates: list[tuple[str, str]] = []
    for e in data["evidence"]:
        # 删除是单调的：旧备份绝不能让目标库已软删除的同 id 证据复活。
        # get() 刻意不返回墓碑，恢复路径须显式辨认；其关联 join / 解析也一并跳过。
        if is_evidence_tombstoned(evidence_store, e["id"]):
            plan.duplicates.evidence += 1
            if is_v4 and e.get("deletedAt") is not None and world_db is not None:
                target_deleted = world_db.execute(
                    "SELECT deleted_at FROM evidence WHERE id = ?", (e["id"],)
                ).fetchone()
                if (
                    target_deleted is not None
                    and str(target_deleted[0]) < str(e["deletedAt"])
                ):
                    evidence_tombstone_updates.append((e["id"], str(e["deletedAt"])))
                continue
            unresolved_evidence.add(e["id"])
            plan.warnings.append(
                f"evidence {e['id']} 在目标库已被删除（墓碑），跳过旧备份以保持删除单调性"
                if lang == "zh"
                else f"evidence {e['id']} is tombstoned in the target database; skipping the older backup to preserve deletion monotonicity"
            )
            continue
        if evidence_store.get(e["id"]) is not None:
            plan.duplicates.evidence += 1  # 同 id 已在 → 跳过(join 仍指向它,安全)
            if is_v4 and e.get("deletedAt") is not None:
                evidence_tombstone_updates.append((e["id"], str(e["deletedAt"])))
            continue
        origin = e.get("originId")
        if origin is not None and evidence_store.find_by_origin(origin) is not None:
            plan.duplicates.evidence += 1
            unresolved_evidence.add(e["id"])  # 无法按原 id 落库 → 指向它的 join 行必须一并丢
            plan.warnings.append(
                f"evidence {e['id']} 的 originId 已被库中另一条占用，跳过（其溯源引用一并丢弃）"
                if lang == "zh"
                else f"evidence {e['id']} originId is already taken by another record in the database; skipping (its provenance links are dropped too)"
            )
            continue
        new_evidence.append(e)
        if is_v4 and e.get("deletedAt") is not None:
            evidence_tombstone_updates.append((e["id"], str(e["deletedAt"])))

    candidate_events = []
    for ev in data["events"]:
        if event_store.get(ev["id"]) is not None:
            plan.duplicates.events += 1
            continue
        candidate_events.append(ev)

    candidate_cognitions = []
    for c in data["cognitions"]:
        if cognition_store.get(c["id"]) is not None:
            plan.duplicates.cognitions += 1
            continue
        candidate_cognitions.append(c)

    # 删除单调性同样适用于派生层：event.summary / cognition.content 均由 evidence 派生。
    # 任一来源未恢复即整实体 fail-closed，绝不留下脱离被删证据的摘要或画像。
    new_events: list[dict[str, Any]] = []
    for event in candidate_events:
        unresolved = [eid for eid in event_links.get(event["id"], []) if eid in unresolved_evidence]
        if unresolved:
            plan.warnings.append(
                f"event {event['id']} 依赖未恢复的 evidence {', '.join(unresolved)}，跳过以防摘要复活"
                if lang == "zh"
                else f"event {event['id']} depends on unresolved evidence {', '.join(unresolved)}; skipping to prevent derived summary revival"
            )
            continue
        new_events.append(event)

    cognition_links: dict[str, list[str]] = {}
    for link in data["cognitionEvidence"]:
        cognition_links.setdefault(link["cognitionId"], []).append(link["evidenceId"])
    new_cognitions: list[dict[str, Any]] = []
    for cognition in candidate_cognitions:
        unresolved = [eid for eid in cognition_links.get(cognition["id"], []) if eid in unresolved_evidence]
        if unresolved:
            plan.warnings.append(
                f"cognition {cognition['id']} 依赖未恢复的 evidence {', '.join(unresolved)}，跳过以防派生内容复活"
                if lang == "zh"
                else f"cognition {cognition['id']} depends on unresolved evidence {', '.join(unresolved)}; skipping to prevent derived content revival"
            )
            continue
        new_cognitions.append(cognition)

    # 将新建 event 的覆盖证据。实体筛选已 fail-closed，因此这里不会给残缺事件写 join。
    new_event_ids = {e["id"] for e in new_events}
    event_evidence_of: dict[str, list[str]] = {}
    event_evidence_count = 0
    for link in data["eventEvidence"]:
        if link["eventId"] not in new_event_ids:
            continue
        if link["evidenceId"] in unresolved_evidence:
            continue  # 悬空 → 丢
        event_evidence_of.setdefault(link["eventId"], []).append(link["evidenceId"])
        event_evidence_count += 1

    # 将新建 cognition 的溯源链(同理丢弃悬空)。
    new_cognition_ids = {c["id"] for c in new_cognitions}
    cognition_sources_of: dict[str, list[EvidenceLink]] = {}
    cognition_evidence_count = 0
    for link in data["cognitionEvidence"]:
        if link["cognitionId"] not in new_cognition_ids:
            continue
        if link["evidenceId"] in unresolved_evidence:
            continue
        cognition_sources_of.setdefault(link["cognitionId"], []).append(
            EvidenceLink(evidence_id=link["evidenceId"], relation=link["relation"])
        )
        cognition_evidence_count += 1

    # 悬空 correctsEvidenceId 置空:目标库既无、也不在本次新建集 → 落库前置空。
    new_evidence_ids = {e["id"] for e in new_evidence}
    evidence_to_insert: list[dict[str, Any]] = []
    for e in new_evidence:
        cid = e.get("correctsEvidenceId")
        if cid is not None and evidence_store.get(cid) is None and cid not in new_evidence_ids:
            plan.warnings.append(
                f"evidence {e['id']} 的 correctsEvidenceId({cid}) 在目标库无法解析，导入时置空"
                if lang == "zh"
                else f"evidence {e['id']} correctsEvidenceId({cid}) cannot be resolved in the target database; cleared on import"
            )
            evidence_to_insert.append({**e, "correctsEvidenceId": None})
        else:
            evidence_to_insert.append(e)

    # 交互层:按 id 判重;向后兼容 v1 包(无这两段 → 空)。
    new_interaction_contexts = [c for c in (data.get("interactionContexts") or []) if interaction_context_store.get(c["id"]) is None]
    # 一证据一解析：目标库已有解析时保持既有结果；包内重复/孤儿在 validate_bundle 已致命拒绝。
    new_semantic_resolutions: list[dict[str, Any]] = []
    for r in data.get("semanticResolutions") or []:
        if r["evidenceId"] in unresolved_evidence:
            plan.warnings.append(
                f"semanticResolution {r['id']} 指向未恢复的 evidence {r['evidenceId']}，跳过"
                if lang == "zh"
                else f"semanticResolution {r['id']} references unresolved evidence {r['evidenceId']}; skipping"
            )
            continue
        if semantic_resolution_store.get(r["id"]) is not None:
            continue
        if semantic_resolution_store.of_evidence(r["evidenceId"]) is not None:
            plan.warnings.append(
                f"evidence {r['evidenceId']} 在目标库已有 semanticResolution，跳过 {r['id']} 以保持一证据一解析"
                if lang == "zh"
                else f"evidence {r['evidenceId']} already has a semanticResolution in the target database; skipping {r['id']} to preserve one resolution per evidence"
            )
            continue
        new_semantic_resolutions.append(r)

    plan.counts = ImportCounts(
        evidence=len(new_evidence), events=len(new_events), cognitions=len(new_cognitions),
        event_evidence=event_evidence_count, cognition_evidence=cognition_evidence_count,
        interaction_contexts=len(new_interaction_contexts), semantic_resolutions=len(new_semantic_resolutions),
        entities=len(world["entities"]),
        entity_evidence=len(world["entity_evidence"]),
        relationships=len(world["relationships"]),
        world_events=len(world["world_events"]),
        relationship_evidence=len(world["relationship_evidence"]),
        world_event_evidence=len(world["world_event_evidence"]),
        cognition_targets=len(world["cognition_targets"]),
        retractions=len(world["retractions"]),
        cognition_transitions=len(world["cognition_transitions"]),
        world_item_lifecycle=len(world["world_item_lifecycle"]),
        evidence_tombstones=len(evidence_tombstone_updates),
    )

    write_set: dict[str, list[str]] = {
        "evidence": [str(item["id"]) for item in evidence_to_insert],
        "events": [str(item["id"]) for item in new_events],
        "cognitions": [str(item["id"]) for item in new_cognitions],
        "eventEvidence": [
            f"{event_id}/{evidence_id}"
            for event_id, evidence_ids in event_evidence_of.items()
            for evidence_id in evidence_ids
        ],
        "cognitionEvidence": [
            f"{cognition_id}/{link.evidence_id}/{link.relation}"
            for cognition_id, links in cognition_sources_of.items()
            for link in links
        ],
        "interactionContexts": [str(item["id"]) for item in new_interaction_contexts],
        "semanticResolutions": [str(item["id"]) for item in new_semantic_resolutions],
        "entities": [str(item[0]) for item in world["entities"]],
        "entityEvidence": [str(item) for item in world["entity_evidence_keys"]],
        "relationships": [str(item[0]) for item in world["relationships"]],
        "worldEvents": [str(item[0]) for item in world["world_events"]],
        "relationshipEvidence": [
            f"{item[0]}/{item[1]}/{item[2]}"
            for item in world["relationship_evidence"]
        ],
        "worldEventEvidence": [
            f"{item[0]}/{item[1]}/{item[2]}"
            for item in world["world_event_evidence"]
        ],
        "cognitionTargets": [
            f"{item[0]}/{item[1]}/{'' if item[2] is None else item[2]}"
            for item in world["cognition_targets"]
        ],
        "retractions": [str(item[0]) for item in world["retractions"]],
        "cognitionTransitions": [
            str(item[0]) for item in world["cognition_transitions"]
        ],
        "worldItemLifecycle": [
            f"{item[1]}/{item[2]}" for item in world["world_item_lifecycle"]
        ],
        "evidenceTombstones": [str(item[0]) for item in evidence_tombstone_updates],
    }
    if is_v4:
        _finalize_v4_plan(plan, write_set)

    if mode == "dryRun":
        return plan  # 只算不写

    # ── merge:实际写入。顺序:evidence → event(挂证据)→ cognition(挂溯源)——被引方先落库。──
    def write() -> None:
        for e in evidence_to_insert:
            evidence_store.insert(_to_evidence(e))
        for ev in new_events:
            event_store.insert(
                _to_event(ev), event_evidence_of.get(ev["id"], []), consolidated=ev["id"] not in unconsolidated_set
            )
        for c in new_cognitions:
            cognition_store.insert(_to_cognition(c), cognition_sources_of.get(c["id"], []))
        for c in new_interaction_contexts:
            interaction_context_store.insert(_to_interaction_context(c))
        for r in new_semantic_resolutions:
            semantic_resolution_store.insert(_to_semantic_resolution(r))
        if world_db is not None:
            for values in world["entities"]:
                world_db.execute(
                    "INSERT INTO entity (id, world_id, kind, canonical_name, "
                    "aliases_json, invalid_at, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    values,
                )
            for values in world["entity_evidence"]:
                world_db.execute(
                    "INSERT INTO evidence_ledger (id, content, payload_json) VALUES (?,?,?)",
                    values,
                )
            for values in world["relationships"]:
                world_db.execute(
                    "INSERT INTO relationship (id, world_id, source_entity_id, "
                    "target_entity_id, relation_type, content, formed_by, "
                    "confidence, cred_status, invalid_at, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
            for values in world["world_events"]:
                world_db.execute(
                    "INSERT INTO world_event (id, world_id, content, occurred_at, "
                    "time_expression, participants_json, objects_json, formed_by, "
                    "confidence, cred_status, invalid_at, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
            for values in world["relationship_evidence"]:
                world_db.execute(
                    "INSERT INTO relationship_evidence (relationship_id, evidence_id, relation) "
                    "VALUES (?,?,?)",
                    values,
                )
            for values in world["world_event_evidence"]:
                world_db.execute(
                    "INSERT INTO world_event_evidence (world_event_id, evidence_id, relation) "
                    "VALUES (?,?,?)",
                    values,
                )
            for values in world["cognition_targets"]:
                world_db.execute(
                    "INSERT INTO cognition_target (cognition_id, target_entity_id, "
                    "perspective_entity_id) VALUES (?,?,?)",
                    values,
                )
            for values in world["retractions"]:
                world_db.execute(
                    "INSERT INTO retraction (id, prior_cognition_id, "
                    "prior_relationship_id, reason, revision, created_at, "
                    "prior_event_id) VALUES (?,?,?,?,?,?,?)",
                    values,
                )
            for values in world["cognition_transitions"]:
                world_db.execute(
                    "INSERT INTO cognition_transitions (id, prior_cognition_id, "
                    "replacement_cognition_id, reason, revision) VALUES (?,?,?,?,?)",
                    values,
                )
            for values in world["world_item_lifecycle"]:
                world_db.execute(
                    "INSERT INTO world_item_lifecycle (subject_id, object_kind, "
                    "item_id, archived_at, muted_at, updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    values,
                )
            for evidence_id, deleted_at in evidence_tombstone_updates:
                world_db.execute(
                    "UPDATE evidence SET deleted_at = ?, origin_id = NULL "
                    "WHERE id = ? AND (deleted_at IS NULL OR deleted_at < ?)",
                    (deleted_at, evidence_id, deleted_at),
                )

    try:
        if transaction is not None:
            transaction(write)
        else:
            write()
    except Exception as e:  # 将写入错误归入 ImportPlan；无事务时同时报告可能存在的部分写入。
        plan.valid = False
        plan.errors.append(f"导入写入失败：{e}" if lang == "zh" else f"Import write failed: {e}")
        if transaction is None:
            plan.warnings.append(
                "未提供 transaction，写入中途失败可能已残留部分数据（建议用 openStores 的 transaction）"
                if lang == "zh"
                else "No transaction provided; a mid-write failure may have left partial data (use the transaction from openStores)"
            )
        plan.counts = ImportCounts()
        return plan

    return plan
