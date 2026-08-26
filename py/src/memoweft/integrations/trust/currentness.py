"""One fail-closed currentness contract for Evidence-backed World surfaces.

The helpers in this module deliberately only answer eligibility/visibility.
They neither mutate World state nor select model routes. Callers retain their
own transaction and error contracts while sharing the same permission meaning.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal, Mapping, Sequence

from ...types import ModelTier

CurrentnessSurface = Literal[
    "formation", "recall", "export", "trust_local", "trust_cloud"
]
WorldItemKind = Literal["cognition", "entity", "relationship", "event"]


def world_item_lifecycle(
    db: sqlite3.Connection,
    subject_id: str,
    kind: WorldItemKind,
    item_id: str,
) -> tuple[str | None, str | None]:
    row = db.execute(
        "SELECT archived_at, muted_at FROM world_item_lifecycle "
        "WHERE subject_id = ? AND object_kind = ? AND item_id = ?",
        (subject_id, kind, item_id),
    ).fetchone()
    if row is None:
        return None, None
    return (
        None if row[0] is None else str(row[0]),
        None if row[1] is None else str(row[1]),
    )


def evidence_state(
    row: Mapping[str, Any],
    *,
    surface: CurrentnessSurface,
    model_tier: ModelTier = "cloud",
) -> str | None:
    """Return a stable denial code, or ``None`` for current eligible Evidence."""

    if row["deleted_at"] is not None:
        return "evidence_deleted"
    if surface == "formation":
        if int(row["allow_inference"]) != 1:
            return "evidence_inference_denied"
        if model_tier == "cloud" and int(row["allow_cloud_read"]) != 1:
            return "evidence_cloud_read_denied"
        if model_tier == "local" and int(row["allow_local_read"]) != 1:
            return "evidence_local_read_denied"
        return None
    if int(row["allow_local_read"]) != 1:
        return "evidence_local_read_denied"
    if surface == "trust_cloud" and int(row["allow_cloud_read"]) != 1:
        return "evidence_cloud_read_denied"
    return None


def linked_evidence(
    db: sqlite3.Connection, kind: WorldItemKind, item_id: str
) -> tuple[str, ...]:
    """Return deterministic direct Evidence provenance for one World item."""

    if kind == "cognition":
        rows = db.execute(
            "SELECT DISTINCT evidence_id FROM cognition_evidence "
            "WHERE cognition_id = ? ORDER BY evidence_id",
            (item_id,),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)
    if kind == "relationship":
        rows = db.execute(
            "SELECT DISTINCT evidence_id FROM relationship_evidence "
            "WHERE relationship_id = ? ORDER BY evidence_id",
            (item_id,),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)
    if kind == "event":
        rows = db.execute(
            "SELECT DISTINCT evidence_id FROM world_event_evidence "
            "WHERE world_event_id = ? ORDER BY evidence_id",
            (item_id,),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    evidence_ids: set[str] = set()
    for content, payload in db.execute(
        "SELECT content, payload_json FROM evidence_ledger ORDER BY id"
    ).fetchall():
        try:
            content_data = json.loads(str(content))
            payload_data = json.loads(str(payload))
        except (TypeError, ValueError):
            continue
        if (
            isinstance(content_data, dict)
            and content_data.get("entity_id") == item_id
            and content_data.get("relation") == "support"
            and isinstance(content_data.get("evidence_id"), str)
            and isinstance(payload_data, dict)
            and payload_data.get("schema_version") == 1
        ):
            evidence_ids.add(str(content_data["evidence_id"]))
    return tuple(sorted(evidence_ids))


def current_entity_aliases(
    db: sqlite3.Connection,
    subject_id: str,
    entity_id: str,
    *,
    surface: CurrentnessSurface,
    model_tier: ModelTier = "cloud",
) -> tuple[str, ...]:
    """Return aliases whose own alias-merge Evidence remains current.

    Alias merges are graph extensions, not entity-formation provenance. A
    revoked alias must not remain visible merely because the canonical entity
    still has independent eligible formation Evidence.
    """

    evidence_by_alias: dict[str, set[str]] = {}
    malformed_aliases: set[str] = set()
    for content, payload in db.execute(
        "SELECT content, payload_json FROM evidence_ledger ORDER BY id"
    ).fetchall():
        try:
            content_data = json.loads(str(content))
        except (TypeError, ValueError):
            continue
        alias_name = content_data.get("alias_name") if isinstance(content_data, dict) else None
        if not (
            isinstance(content_data, dict)
            and content_data.get("relation") == "alias"
            and content_data.get("canonical_entity_id") == entity_id
            and isinstance(alias_name, str)
        ):
            continue
        try:
            payload_data = json.loads(str(payload))
        except (TypeError, ValueError):
            malformed_aliases.add(alias_name)
            continue
        if (
            not isinstance(payload_data, dict)
            or payload_data.get("schema_version") != 1
            or not isinstance(payload_data.get("evidence_ids"), list)
            or not all(isinstance(evidence_id, str) for evidence_id in payload_data["evidence_ids"])
        ):
            malformed_aliases.add(alias_name)
            continue
        evidence_ids = payload_data.get("evidence_ids") if isinstance(payload_data, dict) else None
        if (
            not isinstance(evidence_ids, list)
            or not all(isinstance(evidence_id, str) for evidence_id in evidence_ids)
        ):
            continue
        evidence_by_alias.setdefault(str(content_data["alias_name"]), set()).update(
            str(evidence_id) for evidence_id in evidence_ids
        )
    return tuple(
        sorted(
            alias_name
            for alias_name, evidence_ids in evidence_by_alias.items()
            if alias_name not in malformed_aliases
            and _all_evidence_current(
                db,
                tuple(sorted(evidence_ids)),
                subject_id=subject_id,
                surface=surface,
                model_tier=model_tier,
            )
        )
    )


def _entity_alias_provenance(
    db: sqlite3.Connection, entity_id: str
) -> tuple[tuple[str | None, str], ...]:
    """Return alias-ledger Evidence IDs, retaining malformed ledgers as facts."""

    provenance: list[tuple[str | None, str]] = []
    for ledger_id, content, payload in db.execute(
        "SELECT id, content, payload_json FROM evidence_ledger ORDER BY id"
    ).fetchall():
        try:
            content_data = json.loads(str(content))
        except (TypeError, ValueError):
            continue
        if not (
            isinstance(content_data, dict)
            and content_data.get("relation") == "alias"
            and content_data.get("canonical_entity_id") == entity_id
        ):
            continue
        try:
            payload_data = json.loads(str(payload))
        except (TypeError, ValueError):
            provenance.append((None, f"alias:malformed:{ledger_id}"))
            continue
        evidence_ids = payload_data.get("evidence_ids") if isinstance(payload_data, dict) else None
        if (
            not isinstance(payload_data, dict)
            or payload_data.get("schema_version") != 1
            or not isinstance(evidence_ids, list)
            or not all(isinstance(evidence_id, str) for evidence_id in evidence_ids)
        ):
            provenance.append((None, f"alias:malformed:{ledger_id}"))
            continue
        provenance.extend((str(evidence_id), "alias") for evidence_id in evidence_ids)
    return tuple(provenance)


def _fact_from_evidence(
    db: sqlite3.Connection,
    *,
    kind: WorldItemKind,
    item_id: str,
    evidence_id: str | None,
    ledger_relation: str,
) -> dict[str, object]:
    row = (
        None
        if evidence_id is None
        else db.execute(
            "SELECT subject_id, deleted_at, allow_local_read, allow_cloud_read, "
            "allow_inference FROM evidence WHERE id = ?",
            (evidence_id,),
        ).fetchone()
    )
    return {
        "kind": kind,
        "item_id": item_id,
        "evidence_id": evidence_id,
        "ledger_relation": ledger_relation,
        "evidence_subject_id": None if row is None else str(row[0]),
        "exists": row is not None,
        "deleted": False if row is None else row[1] is not None,
        "allow_local_read": False if row is None else int(row[2]) == 1,
        "allow_cloud_read": False if row is None else int(row[3]) == 1,
        "allow_inference": False if row is None else int(row[4]) == 1,
    }


def subject_currentness_facts(
    db: sqlite3.Connection, subject_id: str
) -> tuple[dict[str, object], ...]:
    """Return canonical provenance/permission facts without text or timestamps.

    This is intentionally factual rather than an eligibility decision: missing
    and cross-subject Evidence remain represented so downstream snapshot-token
    builders can invalidate on either revocation or a later grant.
    """

    item_ids: list[tuple[WorldItemKind, str]] = []
    item_ids.extend(
        ("cognition", str(row[0]))
        for row in db.execute(
            "SELECT id FROM cognition WHERE subject_id = ? AND invalid_at IS NULL "
            "AND archived_at IS NULL AND muted_at IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM world_item_lifecycle l WHERE l.subject_id = cognition.subject_id "
            "AND l.object_kind = 'cognition' AND l.item_id = cognition.id "
            "AND (l.archived_at IS NOT NULL OR l.muted_at IS NOT NULL)) ORDER BY id",
            (subject_id,),
        ).fetchall()
    )
    relationship_event_tables: tuple[tuple[WorldItemKind, str], ...] = (
        ("relationship", "relationship"),
        ("event", "world_event"),
    )
    for kind, table in relationship_event_tables:
        item_ids.extend(
            (kind, str(row[0]))
            for row in db.execute(
                f"SELECT id FROM {table} WHERE world_id = ? AND invalid_at IS NULL "
                f"AND NOT EXISTS (SELECT 1 FROM world_item_lifecycle l "
                f"WHERE l.subject_id = {table}.world_id AND l.object_kind = ? "
                f"AND l.item_id = {table}.id AND (l.archived_at IS NOT NULL "
                f"OR l.muted_at IS NOT NULL)) ORDER BY id",
                (subject_id, kind),
            ).fetchall()
        )
    item_ids.extend(
        ("entity", str(row[0]))
        for row in db.execute(
            "SELECT id FROM entity WHERE world_id = ? AND invalid_at IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM world_item_lifecycle l "
            "WHERE l.subject_id = entity.world_id AND l.object_kind = 'entity' "
            "AND l.item_id = entity.id AND (l.archived_at IS NOT NULL "
            "OR l.muted_at IS NOT NULL)) ORDER BY id",
            (subject_id,),
        ).fetchall()
    )

    facts: list[dict[str, object]] = []
    for kind, item_id in item_ids:
        for evidence_id in linked_evidence(db, kind, item_id):
            facts.append(
                _fact_from_evidence(
                    db,
                    kind=kind,
                    item_id=item_id,
                    evidence_id=evidence_id,
                    ledger_relation="support",
                )
            )
        if kind == "entity":
            for alias_evidence_id, ledger_relation in _entity_alias_provenance(db, item_id):
                facts.append(
                    _fact_from_evidence(
                        db,
                        kind=kind,
                        item_id=item_id,
                        evidence_id=alias_evidence_id,
                        ledger_relation=ledger_relation,
                    )
                )
    return tuple(
        sorted(
            facts,
            key=lambda fact: (
                str(fact["kind"]),
                str(fact["item_id"]),
                str(fact["ledger_relation"]),
                str(fact["evidence_id"]),
            ),
        )
    )


def _all_evidence_current(
    db: sqlite3.Connection,
    evidence_ids: Sequence[str],
    *,
    subject_id: str,
    surface: CurrentnessSurface,
    model_tier: ModelTier,
) -> bool:
    if not evidence_ids:
        return False
    placeholders = ",".join("?" for _ in evidence_ids)
    rows = db.execute(
        "SELECT id, subject_id, deleted_at, allow_local_read, allow_cloud_read, allow_inference "
        f"FROM evidence WHERE id IN ({placeholders})",
        tuple(evidence_ids),
    ).fetchall()
    rows_by_id = {str(row[0]): row for row in rows}
    return all(
        evidence_id in rows_by_id
        and str(rows_by_id[evidence_id][1]) == subject_id
        and evidence_state(
            {
                "deleted_at": rows_by_id[evidence_id][2],
                "allow_local_read": rows_by_id[evidence_id][3],
                "allow_cloud_read": rows_by_id[evidence_id][4],
                "allow_inference": rows_by_id[evidence_id][5],
            },
            surface=surface,
            model_tier=model_tier,
        )
        is None
        for evidence_id in evidence_ids
    )


def world_item_visible(
    db: sqlite3.Connection,
    subject_id: str,
    kind: WorldItemKind,
    item_id: str,
    *,
    surface: CurrentnessSurface,
    model_tier: ModelTier = "cloud",
) -> bool:
    """Whether an existing World row may appear on a target surface."""

    table, world_column = (
        ("cognition", "subject_id")
        if kind == "cognition"
        else ("entity", "world_id")
        if kind == "entity"
        else ("relationship", "world_id")
        if kind == "relationship"
        else ("world_event", "world_id")
    )
    lifecycle_columns = ", archived_at, muted_at" if kind == "cognition" else ""
    row = db.execute(
        f"SELECT invalid_at{lifecycle_columns} FROM {table} "
        f"WHERE id = ? AND {world_column} = ?",
        (item_id, subject_id),
    ).fetchone()
    if row is None or row[0] is not None:
        return False
    if kind == "cognition" and (row[1] is not None or row[2] is not None):
        return False
    archived_at, muted_at = world_item_lifecycle(
        db, subject_id, kind, item_id
    )
    if archived_at is not None or muted_at is not None:
        return False
    return _all_evidence_current(
        db,
        linked_evidence(db, kind, item_id),
        subject_id=subject_id,
        surface=surface,
        model_tier=model_tier,
    )
