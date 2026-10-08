"""Read-only Trust Query service over the production MemoWeft SQLite world."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping, Sequence, cast

from .currentness import (
    current_entity_aliases,
    evidence_state,
    linked_evidence,
    world_item_lifecycle,
    world_item_visible,
)
from .model import (
    EvidenceV1,
    LifecycleV1,
    PermissionsV1,
    ProvenanceV1,
    TRUST_CAPABILITIES_VERSION,
    TRUST_SCHEMA_VERSION,
    TransitionV1,
    TrustQueryError,
    TrustSurface,
    TrustWorldItemKind,
    WorldItemV1,
)
from .revision import CoherentRevisionRead, coherent_revision_read


_KIND_ORDER: dict[str, int] = {
    "entity": 0,
    "relationship": 1,
    "event": 2,
    "cognition": 3,
}
_KINDS = frozenset(_KIND_ORDER)
_OPERATIONS = (
    "get_capabilities",
    "get_world_revision",
    "list_world_items",
    "get_world_item",
    "get_world_item_history",
    "get_world_item_provenance",
    "list_evidence",
    "get_evidence",
    "list_jobs",
    "get_job",
    "preview_recall",
)

TRUST_PROVIDER_TOOL_SCHEMAS: tuple[dict[str, object], ...] = (
    {
        "name": "memoweft_trust_capabilities",
        "description": "Return MemoWeft read-only Trust Query capabilities and current World revision.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "memoweft_query_world",
        "description": "Read current or historical Personal Memory World items and their provenance.",
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["list", "get", "history", "provenance", "revision"],
                },
                "object_kind": {
                    "type": "string",
                    "enum": ["entity", "relationship", "event", "cognition"],
                },
                "item_id": {"type": "string"},
                "include_history": {"type": "boolean"},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
    },
    {
        "name": "memoweft_query_evidence",
        "description": "Read Evidence source, permission, and tombstone facts without mutation.",
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["list", "get"]},
                "evidence_id": {"type": "string"},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
    },
    {
        "name": "memoweft_query_jobs",
        "description": "Read World Job acceptance, Core terminal, and delivery facts without mutation.",
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["list", "get"]},
                "job_id": {"type": "string"},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
    },
    {
        "name": "memoweft_preview_recall",
        "description": "Preview the deterministic production Recall snapshot with zero model calls and zero writes.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
)


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise TrustQueryError("trust_result_not_canonical") from exc


def _json_mapping(raw: object) -> dict[str, object]:
    if raw is None:
        return {}
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _json_list(raw: object) -> list[object]:
    if raw is None:
        return []
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    return list(value) if isinstance(value, list) else []


def _permissions(row: Sequence[object]) -> PermissionsV1:
    return {
        "allow_local_read": row[0] == 1,
        "allow_cloud_read": row[1] == 1,
        "allow_inference": row[2] == 1,
    }


class QueryService:
    """Stable subject-bound Trust API; every public method owns one read view."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        subject_id: str,
        surface: TrustSurface = "trust_local",
    ) -> None:
        subject_id = subject_id.strip()
        if not subject_id:
            raise TrustQueryError("invalid_subject_id")
        if surface not in {"trust_local", "trust_cloud"}:
            raise TrustQueryError("invalid_trust_surface")
        self._db_path = Path(db_path)
        self._subject_id = subject_id
        self._surface = surface

    def _base(self, revision: int) -> dict[str, object]:
        return {
            "schema_version": TRUST_SCHEMA_VERSION,
            "subject_id": self._subject_id,
            "world_revision": revision,
        }

    def _read(self) -> Any:
        return coherent_revision_read(self._db_path)

    @staticmethod
    def _kind(value: object) -> TrustWorldItemKind:
        if not isinstance(value, str) or value not in _KINDS:
            raise TrustQueryError("unsupported_object_kind")
        return cast(TrustWorldItemKind, value)

    def get_capabilities(self) -> dict[str, object]:
        with self._read() as read:
            return {
                **self._base(read.world_revision),
                "capabilities_version": TRUST_CAPABILITIES_VERSION,
                "operations": list(_OPERATIONS),
                "object_kinds": [
                    "entity",
                    "relationship",
                    "event",
                    "cognition",
                ],
                "read_only": True,
                "coherent_revision_read": True,
                "recall_preview_model_calls": 0,
                "recall_preview_world_writes": 0,
            }

    def execute_provider_tool(
        self, tool_name: str, args: Mapping[str, object]
    ) -> dict[str, object]:
        """Dispatch one closed read-only provider operation.

        The provider is subject-bound at initialization.  No subject/profile
        selector is accepted here, and every operation rejects extra fields.
        """

        if not isinstance(tool_name, str) or not isinstance(args, Mapping):
            raise TrustQueryError("invalid_trust_tool_call")
        raw = dict(args)
        if tool_name == "memoweft_trust_capabilities":
            self._require_keys(raw, allowed=frozenset())
            return self.get_capabilities()
        if tool_name == "memoweft_query_world":
            self._require_keys(
                raw,
                allowed=frozenset(
                    {"operation", "object_kind", "item_id", "include_history"}
                ),
                required=frozenset({"operation"}),
            )
            operation = raw["operation"]
            if operation == "revision":
                self._require_keys(raw, allowed=frozenset({"operation"}))
                return self.get_world_revision()
            if operation == "list":
                include_history = raw.get("include_history", False)
                if not isinstance(include_history, bool):
                    raise TrustQueryError("invalid_include_history")
                kind = raw.get("object_kind")
                return self.list_world_items(
                    None if kind is None else self._kind(kind),
                    include_history=include_history,
                )
            kind = self._kind(raw.get("object_kind"))
            item_id = self._identifier(raw.get("item_id"), "invalid_item_id")
            if operation == "get":
                include_history = raw.get("include_history", False)
                if not isinstance(include_history, bool):
                    raise TrustQueryError("invalid_include_history")
                return self.get_world_item(
                    kind, item_id, include_history=include_history
                )
            self._require_keys(
                raw,
                allowed=frozenset({"operation", "object_kind", "item_id"}),
                required=frozenset({"operation", "object_kind", "item_id"}),
            )
            if operation == "history":
                return self.get_world_item_history(kind, item_id)
            if operation == "provenance":
                return self.get_world_item_provenance(kind, item_id)
            raise TrustQueryError("unsupported_trust_operation")
        if tool_name == "memoweft_query_evidence":
            self._require_keys(
                raw,
                allowed=frozenset({"operation", "evidence_id"}),
                required=frozenset({"operation"}),
            )
            if raw["operation"] == "list":
                self._require_keys(raw, allowed=frozenset({"operation"}))
                return self.list_evidence()
            if raw["operation"] == "get":
                return self.get_evidence(
                    self._identifier(raw.get("evidence_id"), "invalid_evidence_id")
                )
            raise TrustQueryError("unsupported_trust_operation")
        if tool_name == "memoweft_query_jobs":
            self._require_keys(
                raw,
                allowed=frozenset({"operation", "job_id"}),
                required=frozenset({"operation"}),
            )
            if raw["operation"] == "list":
                self._require_keys(raw, allowed=frozenset({"operation"}))
                return self.list_jobs()
            if raw["operation"] == "get":
                return self.get_job(
                    self._identifier(raw.get("job_id"), "invalid_job_id")
                )
            raise TrustQueryError("unsupported_trust_operation")
        if tool_name == "memoweft_preview_recall":
            self._require_keys(
                raw,
                allowed=frozenset({"query", "model_tier"}),
                required=frozenset({"query"}),
            )
            query = raw["query"]
            if not isinstance(query, str):
                raise TrustQueryError("invalid_recall_query")
            return self.preview_recall(query, model_tier=cast(Any, raw.get("model_tier", "local")))
        raise TrustQueryError("unknown_trust_tool")

    @staticmethod
    def _require_keys(
        value: Mapping[str, object],
        *,
        allowed: frozenset[str],
        required: frozenset[str] = frozenset(),
    ) -> None:
        keys = frozenset(value)
        if keys - allowed:
            raise TrustQueryError("unexpected_trust_tool_argument")
        if required - keys:
            raise TrustQueryError("missing_trust_tool_argument")

    def get_world_revision(self) -> dict[str, object]:
        with self._read() as read:
            return {**self._base(read.world_revision), "revision": read.world_revision}

    def list_world_items(
        self,
        object_kind: TrustWorldItemKind | None = None,
        *,
        include_history: bool = False,
    ) -> dict[str, object]:
        selected_kind = None if object_kind is None else self._kind(object_kind)
        with self._read() as read:
            kinds: Iterable[TrustWorldItemKind] = (
                (selected_kind,)
                if selected_kind is not None
                else ("entity", "relationship", "event", "cognition")
            )
            items: list[WorldItemV1] = []
            for kind in kinds:
                for item_id in self._item_ids(read.db, kind):
                    item = self._world_item(read, kind, item_id)
                    if include_history or item["current_state"] == "current":
                        items.append(item)
            items.sort(
                key=lambda item: (
                    _KIND_ORDER[item["object_kind"]],
                    item["created_at"],
                    item["item_id"],
                )
            )
            return {**self._base(read.world_revision), "items": items}

    def get_world_item(
        self,
        object_kind: TrustWorldItemKind,
        item_id: str,
        *,
        include_history: bool = False,
    ) -> dict[str, object]:
        kind = self._kind(object_kind)
        item_id = self._identifier(item_id, "invalid_item_id")
        with self._read() as read:
            item = self._world_item(read, kind, item_id)
            if not include_history and item["current_state"] != "current":
                raise TrustQueryError("world_item_not_current")
            return {**self._base(read.world_revision), "item": item}

    def get_world_item_history(
        self, object_kind: TrustWorldItemKind, item_id: str
    ) -> dict[str, object]:
        kind = self._kind(object_kind)
        item_id = self._identifier(item_id, "invalid_item_id")
        with self._read() as read:
            item = self._world_item(read, kind, item_id)
            history = self._transitions(read.db, kind, item_id)
            return {
                **self._base(read.world_revision),
                "item": item,
                "transition_history": history,
            }

    def get_world_item_provenance(
        self, object_kind: TrustWorldItemKind, item_id: str, *, projection: str = "history"
    ) -> dict[str, object]:
        if projection not in {"history", "model"}:
            raise TrustQueryError("invalid_provenance_projection")
        kind = self._kind(object_kind)
        item_id = self._identifier(item_id, "invalid_item_id")
        with self._read() as read:
            self._require_item_exists(read.db, kind, item_id)
            provenance = self._provenance(read.db, read.world_revision, kind, item_id, projection=projection)
            return {
                **self._base(read.world_revision),
                "object_kind": kind,
                "item_id": item_id,
                "projection": projection,
                "provenance": provenance,
                "transition_history": self._transitions(read.db, kind, item_id),
            }

    def list_evidence(self) -> dict[str, object]:
        with self._read() as read:
            rows = read.db.execute(
                "SELECT * FROM evidence WHERE subject_id = ? "
                "ORDER BY recorded_at, rowid, id",
                (self._subject_id,),
            ).fetchall()
            return {
                **self._base(read.world_revision),
                "evidence": [self._evidence(read.world_revision, row) for row in rows],
            }

    def get_evidence(self, evidence_id: str) -> dict[str, object]:
        evidence_id = self._identifier(evidence_id, "invalid_evidence_id")
        with self._read() as read:
            row = read.db.execute(
                "SELECT * FROM evidence WHERE id = ? AND subject_id = ?",
                (evidence_id, self._subject_id),
            ).fetchone()
            if row is None:
                raise TrustQueryError("evidence_not_found")
            return {
                **self._base(read.world_revision),
                "evidence": self._evidence(read.world_revision, row),
            }

    def list_jobs(self) -> dict[str, object]:
        with self._read() as read:
            rows = read.db.execute(
                "SELECT job_id FROM memory_world_job WHERE subject_id = ? "
                "ORDER BY created_at, job_id",
                (self._subject_id,),
            ).fetchall()
            jobs = [self._job(read, str(row[0])) for row in rows]
            return {**self._base(read.world_revision), "jobs": jobs}

    def get_job(self, job_id: str) -> dict[str, object]:
        job_id = self._identifier(job_id, "invalid_job_id")
        with self._read() as read:
            return {
                **self._base(read.world_revision),
                "job": self._job(read, job_id),
            }

    def preview_recall(self, query: str, *, model_tier: str = "local") -> dict[str, object]:
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            raise TrustQueryError("invalid_recall_query")
        if model_tier not in {"local", "cloud"}:
            raise TrustQueryError("invalid_model_tier")
        # Imported lazily because ``hermes.recall`` itself imports the shared
        # currentness module from this package.  The runtime dependency is one-
        # way at operation time and avoids a package-initialization cycle.
        from ..hermes.recall import recall_world_snapshot

        with self._read() as read:
            snapshot = recall_world_snapshot(read.db, self._subject_id, query, model_tier=cast(Any, model_tier))
            if snapshot is None or snapshot.world_revision != read.world_revision:
                raise TrustQueryError("recall_snapshot_unavailable")
            return {
                **self._base(read.world_revision),
                "preview": {
                    "query_hash": sha256(query.encode("utf-8")).hexdigest(),
                    "model_tier": model_tier,
                    "selected_item_ids": [list(pair) for pair in snapshot.selected_item_ids],
                    "currentness_digest": snapshot.currentness_digest,
                    "rendered_recall": snapshot.rendered_recall,
                    "recall_snapshot_token": snapshot.recall_snapshot_token,
                    "count": snapshot.count,
                    "model_call_count": 0,
                    "world_write_count": 0,
                },
            }

    @staticmethod
    def _identifier(value: object, code: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 512
        ):
            raise TrustQueryError(code)
        return value

    def _item_ids(
        self, db: sqlite3.Connection, kind: TrustWorldItemKind
    ) -> tuple[str, ...]:
        table, subject_column = self._table(kind)
        rows = db.execute(
            f"SELECT id FROM {table} WHERE {subject_column} = ? ORDER BY id",
            (self._subject_id,),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    @staticmethod
    def _table(kind: TrustWorldItemKind) -> tuple[str, str]:
        return {
            "entity": ("entity", "world_id"),
            "relationship": ("relationship", "world_id"),
            "event": ("world_event", "world_id"),
            "cognition": ("cognition", "subject_id"),
        }[kind]

    def _require_item_exists(
        self, db: sqlite3.Connection, kind: TrustWorldItemKind, item_id: str
    ) -> sqlite3.Row:
        table, subject_column = self._table(kind)
        row = db.execute(
            f"SELECT * FROM {table} WHERE id = ? AND {subject_column} = ?",
            (item_id, self._subject_id),
        ).fetchone()
        if row is None:
            raise TrustQueryError("world_item_not_found")
        return cast(sqlite3.Row, row)

    def _world_item(
        self, read: CoherentRevisionRead, kind: TrustWorldItemKind, item_id: str
    ) -> WorldItemV1:
        row = self._require_item_exists(read.db, kind, item_id)
        invalid_at = cast(str | None, row["invalid_at"])
        native_archived_at = (
            cast(str | None, row["archived_at"]) if kind == "cognition" else None
        )
        native_muted_at = (
            cast(str | None, row["muted_at"]) if kind == "cognition" else None
        )
        sidecar_archived_at, sidecar_muted_at = world_item_lifecycle(
            read.db, self._subject_id, kind, item_id
        )
        archived_at = native_archived_at or sidecar_archived_at
        muted_at = native_muted_at or sidecar_muted_at
        lifecycle_current = invalid_at is None and archived_at is None and muted_at is None
        visible = lifecycle_current and world_item_visible(
            read.db,
            self._subject_id,
            kind,
            item_id,
            surface=self._surface,
        )
        currentness_state = "current" if visible else self._item_denial_state(
            read.db, kind, item_id, invalid_at, archived_at, muted_at
        )
        provenance = self._provenance(read.db, read.world_revision, kind, item_id)
        lifecycle: LifecycleV1 = {
            "invalid_at": invalid_at,
            "archived_at": archived_at,
            "muted_at": muted_at,
            "deleted_at": None,
            "visible": visible,
            "currentness_state": currentness_state,
        }
        value, created_at, updated_at = self._item_value(read.db, kind, row)
        restricted_states = {
            "evidence_deleted",
            "evidence_local_read_denied",
            "evidence_missing",
            "evidence_subject_mismatch",
        }
        if self._surface == "trust_cloud":
            restricted_states.add("evidence_cloud_read_denied")
        if any(
            entry["currentness_state"] in restricted_states
            for entry in provenance
        ):
            value = {"redacted": True}
        return {
            "schema_version": TRUST_SCHEMA_VERSION,
            "subject_id": self._subject_id,
            "world_revision": read.world_revision,
            "object_kind": kind,
            "item_id": item_id,
            "current_state": "current" if visible else "not_current",
            "lifecycle": lifecycle,
            "permissions": [entry["permissions"] for entry in provenance],
            "provenance": provenance,
            "transition_history": self._transitions(read.db, kind, item_id),
            "value": value,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def _item_denial_state(
        self,
        db: sqlite3.Connection,
        kind: TrustWorldItemKind,
        item_id: str,
        invalid_at: str | None,
        archived_at: str | None,
        muted_at: str | None,
    ) -> str:
        if invalid_at is not None:
            return "world_item_invalidated"
        if archived_at is not None:
            return "world_item_archived"
        if muted_at is not None:
            return "world_item_muted"
        evidence_ids = linked_evidence(db, kind, item_id)
        if not evidence_ids:
            return "world_item_missing_provenance"
        for evidence_id in evidence_ids:
            row = db.execute(
                "SELECT subject_id, deleted_at, allow_local_read, allow_cloud_read, "
                "allow_inference FROM evidence WHERE id = ?",
                (evidence_id,),
            ).fetchone()
            if row is None:
                return "evidence_missing"
            if str(row[0]) != self._subject_id:
                return "evidence_subject_mismatch"
            state = evidence_state(
                {
                    "deleted_at": row[1],
                    "allow_local_read": row[2],
                    "allow_cloud_read": row[3],
                    "allow_inference": row[4],
                },
                surface=self._surface,
            )
            if state is not None:
                return state
        return "world_item_not_current"

    def _item_value(
        self, db: sqlite3.Connection, kind: TrustWorldItemKind, row: sqlite3.Row
    ) -> tuple[dict[str, object], str, str]:
        if kind == "entity":
            current_aliases = list(
                current_entity_aliases(
                    db,
                    self._subject_id,
                    str(row["id"]),
                    surface=self._surface,
                )
            )
            return (
                {
                    "kind": str(row["kind"]),
                    "canonical_name": str(row["canonical_name"]),
                    "aliases": current_aliases,
                    "current_aliases": current_aliases,
                },
                str(row["created_at"]),
                str(row["updated_at"]),
            )
        if kind == "relationship":
            return (
                {
                    "source_entity_id": str(row["source_entity_id"]),
                    "target_entity_id": str(row["target_entity_id"]),
                    "relation_type": str(row["relation_type"]),
                    "content": str(row["content"]),
                    "formed_by": str(row["formed_by"]),
                    "confidence": int(row["confidence"]),
                    "cred_status": str(row["cred_status"]),
                },
                str(row["created_at"]),
                str(row["updated_at"]),
            )
        if kind == "event":
            return (
                {
                    "content": str(row["content"]),
                    "occurred_at": cast(str | None, row["occurred_at"]),
                    "time_expression": cast(str | None, row["time_expression"]),
                    "participants": _json_list(row["participants_json"]),
                    "objects": _json_list(row["objects_json"]),
                    "formed_by": str(row["formed_by"]),
                    "confidence": int(row["confidence"]),
                    "cred_status": str(row["cred_status"]),
                },
                str(row["created_at"]),
                str(row["updated_at"]),
            )
        target = db.execute(
            "SELECT target_entity_id, perspective_entity_id FROM cognition_target "
            "WHERE cognition_id = ?",
            (row["id"],),
        ).fetchone()
        return (
            {
                "content": str(row["content"]),
                "content_type": str(row["content_type"]),
                "formed_by": str(row["formed_by"]),
                "confidence": int(row["confidence"]),
                "cred_status": str(row["cred_status"]),
                "scope": cast(str | None, row["scope"]),
                "valid_at": cast(str | None, row["valid_at"]),
                "asked_at": cast(str | None, row["asked_at"]),
                "target_entity_id": None if target is None else cast(str, target[0]),
                "perspective_entity_id": None if target is None else cast(str | None, target[1]),
            },
            str(row["created_at"]),
            str(row["updated_at"]),
        )

    def _direct_evidence_relations(
        self, db: sqlite3.Connection, kind: TrustWorldItemKind, item_id: str
    ) -> tuple[tuple[str, str], ...]:
        relations: set[tuple[str, str]] = set()
        if kind == "cognition":
            rows = db.execute(
                "SELECT evidence_id, relation FROM cognition_evidence "
                "WHERE cognition_id = ? ORDER BY evidence_id, relation",
                (item_id,),
            ).fetchall()
            relations.update((str(row[0]), str(row[1])) for row in rows)
        elif kind == "relationship":
            rows = db.execute(
                "SELECT evidence_id, relation FROM relationship_evidence "
                "WHERE relationship_id = ? ORDER BY evidence_id, relation",
                (item_id,),
            ).fetchall()
            relations.update((str(row[0]), str(row[1])) for row in rows)
        elif kind == "event":
            rows = db.execute(
                "SELECT evidence_id, relation FROM world_event_evidence "
                "WHERE world_event_id = ? ORDER BY evidence_id, relation",
                (item_id,),
            ).fetchall()
            relations.update((str(row[0]), str(row[1])) for row in rows)
        for _ledger_id, content, payload in db.execute(
            "SELECT id, content, payload_json FROM evidence_ledger ORDER BY id"
        ).fetchall():
            data = _json_mapping(content)
            payload_data = _json_mapping(payload)
            if kind == "entity" and data.get("entity_id") == item_id and isinstance(
                data.get("evidence_id"), str
            ):
                relations.add((str(data["evidence_id"]), str(data.get("relation") or "support")))
            if kind == "entity" and data.get("canonical_entity_id") == item_id:
                evidence_ids = payload_data.get("evidence_ids")
                if isinstance(evidence_ids, list):
                    relations.update(
                        (str(evidence_id), str(data.get("relation") or "alias"))
                        for evidence_id in evidence_ids
                        if isinstance(evidence_id, str)
                    )
            prior_key = {
                "cognition": "prior_cognition_id",
                "relationship": "prior_relationship_id",
                "event": "prior_event_id",
                "entity": None,
            }[kind]
            if (
                prior_key is not None
                and data.get("relation") == "retracts"
                and data.get(prior_key) == item_id
            ):
                evidence_ids = payload_data.get("evidence_ids")
                if isinstance(evidence_ids, list):
                    relations.update(
                        (str(evidence_id), "retracts")
                        for evidence_id in evidence_ids
                        if isinstance(evidence_id, str)
                    )
        return tuple(sorted(relations))

    def _provenance(
        self,
        db: sqlite3.Connection,
        revision: int,
        kind: TrustWorldItemKind,
        item_id: str,
        *,
        projection: str = "history",
    ) -> list[ProvenanceV1]:
        result: list[ProvenanceV1] = []
        for evidence_id, relation in self._direct_evidence_relations(db, kind, item_id):
            row = db.execute(
                "SELECT * FROM evidence WHERE id = ? AND subject_id = ?",
                (evidence_id, self._subject_id),
            ).fetchone()
            if row is None:
                permissions: PermissionsV1 = {
                    "allow_local_read": False,
                    "allow_cloud_read": False,
                    "allow_inference": False,
                }
                result.append(
                    {
                        "evidence_id": evidence_id,
                        "relation": relation,
                        "currentness_state": "evidence_missing",
                        "permissions": permissions,
                        "evidence": None,
                    }
                )
                continue
            evidence = self._evidence(revision, row)
            linked_items, overflow = self._linked_world_items_for_evidence(db, evidence_id)
            denial: str | None = None
            if overflow:
                denial = "linked_world_items_overflow"
            elif relation != "support" or any(item["relation"] != "support" for item in linked_items):
                denial = "non_support_relation"
            elif not world_item_visible(db, self._subject_id, kind, item_id, surface="recall"):
                denial = "target_not_current"
            elif int(row["allow_local_read"]) != 1 or int(row["allow_inference"]) != 1 or row["deleted_at"] is not None:
                denial = "evidence_not_model_readable"
            elif not any(item["object_kind"] == kind and item["item_id"] == item_id and item["relation"] == "support" for item in linked_items):
                denial = "provenance_reverse_link_missing"
            elif any(item["current_state"] != "current" for item in linked_items):
                denial = "mixed_world_currentness"
            if projection == "model" and denial is not None:
                evidence = {**evidence, "raw_content": None, "summary": None, "content_available": False}
            result.append(
                {
                    "evidence_id": evidence_id,
                    "relation": relation,
                    "currentness_state": ("not_current" if denial == "target_not_current" and evidence["currentness_state"] == "current"
                                          else evidence["currentness_state"]),
                    "permissions": evidence["permissions"],
                    "evidence": evidence,
                    "linked_world_items": linked_items,
                    "model_content_available": denial is None,
                    "model_denial_reason": denial,
                }
            )
        return sorted(result, key=lambda value: (value["evidence_id"], value["relation"]))

    def _linked_world_items_for_evidence(
        self, db: sqlite3.Connection, evidence_id: str
    ) -> tuple[list[dict[str, object]], bool]:
        relations: set[tuple[str, str, str]] = set()
        def belongs(kind: str, linked_item_id: str) -> bool:
            table, subject_column = {
                "entity": ("entity", "world_id"),
                "cognition": ("cognition", "subject_id"),
                "relationship": ("relationship", "world_id"),
                "event": ("world_event", "world_id"),
            }[kind]
            return db.execute(f"SELECT 1 FROM {table} WHERE id = ? AND {subject_column} = ?", (linked_item_id, self._subject_id)).fetchone() is not None
        for kind, table, link_table, item_column, subject_column in (
            ("cognition", "cognition", "cognition_evidence", "cognition_id", "subject_id"),
            ("relationship", "relationship", "relationship_evidence", "relationship_id", "world_id"),
            ("event", "world_event", "world_event_evidence", "world_event_id", "world_id"),
        ):
            rows = db.execute(
                f"SELECT l.{item_column}, l.relation FROM {link_table} l JOIN {table} w "
                f"ON w.id = l.{item_column} WHERE l.evidence_id = ? AND w.{subject_column} = ?",
                (evidence_id, self._subject_id),
            ).fetchall()
            relations.update((kind, str(row[0]), str(row[1])) for row in rows)
        for _ledger_id, content, payload in db.execute(
            "SELECT id, content, payload_json FROM evidence_ledger ORDER BY id"
        ).fetchall():
            data, payload_data = _json_mapping(content), _json_mapping(payload)
            entity_id = data.get("entity_id")
            if isinstance(entity_id, str) and data.get("evidence_id") == evidence_id and belongs("entity", entity_id):
                relations.add(("entity", entity_id, str(data.get("relation") or "support")))
            canonical_id = data.get("canonical_entity_id")
            evidence_ids = payload_data.get("evidence_ids")
            if isinstance(canonical_id, str) and isinstance(evidence_ids, list) and evidence_id in evidence_ids and belongs("entity", canonical_id):
                relations.add(("entity", canonical_id, str(data.get("relation") or "alias")))
            if data.get("relation") == "retracts" and isinstance(evidence_ids, list) and evidence_id in evidence_ids:
                for item_kind, key in (("cognition", "prior_cognition_id"), ("relationship", "prior_relationship_id"), ("event", "prior_event_id")):
                    prior_id = data.get(key)
                    if isinstance(prior_id, str) and belongs(item_kind, prior_id):
                        relations.add((item_kind, prior_id, "retracts"))
        ordered = sorted(relations)
        overflow = len(ordered) > 64
        items: list[dict[str, object]] = [
            {
                "object_kind": kind,
                "item_id": linked_item_id,
                "relation": relation,
                "current_state": "current" if world_item_visible(db, self._subject_id, cast(Any, kind), linked_item_id, surface="recall") else "not_current",
            }
            for kind, linked_item_id, relation in ordered[:64]
        ]
        return items, overflow

    def _evidence(self, revision: int, row: sqlite3.Row) -> EvidenceV1:
        permissions = _permissions(
            (row["allow_local_read"], row["allow_cloud_read"], row["allow_inference"])
        )
        state = evidence_state(
            {
                "deleted_at": row["deleted_at"],
                "allow_local_read": row["allow_local_read"],
                "allow_cloud_read": row["allow_cloud_read"],
                "allow_inference": row["allow_inference"],
            },
            surface=self._surface,
        )
        currentness_state = "current" if state is None else state
        deleted_at = cast(str | None, row["deleted_at"])
        content_available = (
            deleted_at is None
            and permissions["allow_local_read"]
            and (self._surface != "trust_cloud" or permissions["allow_cloud_read"])
        )
        lifecycle: LifecycleV1 = {
            "invalid_at": None,
            "archived_at": None,
            "muted_at": None,
            "deleted_at": deleted_at,
            "visible": state is None,
            "currentness_state": currentness_state,
        }
        return {
            "schema_version": TRUST_SCHEMA_VERSION,
            "subject_id": self._subject_id,
            "world_revision": revision,
            "evidence_id": str(row["id"]),
            "source_kind": str(row["source_kind"]),
            "host_id": str(row["host_id"]),
            "origin_id": cast(str | None, row["origin_id"]),
            "occurred_at": str(row["occurred_at"]),
            "recorded_at": str(row["recorded_at"]),
            "raw_content": str(row["raw_content"]) if content_available else None,
            "summary": str(row["summary"]) if content_available else None,
            "content_available": content_available,
            "permissions": permissions,
            "corrects_evidence_id": cast(str | None, row["corrects_evidence_id"]),
            "currentness_state": currentness_state,
            "lifecycle": lifecycle,
        }

    def _transitions(
        self, db: sqlite3.Connection, kind: TrustWorldItemKind, item_id: str
    ) -> list[TransitionV1]:
        transitions: list[TransitionV1] = []
        if kind == "relationship" and db.execute("SELECT 1 FROM sqlite_master WHERE name='relationship_transitions'").fetchone():
            for row in db.execute(
                "SELECT id, prior_relationship_id, replacement_relationship_id, reason, revision "
                "FROM relationship_transitions WHERE prior_relationship_id=? OR replacement_relationship_id=?",
                (item_id, item_id),
            ).fetchall():
                transitions.append({
                    "transition_id": str(row[0]), "transition_kind": str(row[3]),
                    "object_kind": kind, "prior_item_id": str(row[1]),
                    "replacement_item_id": str(row[2]), "revision": int(row[4]),
                    "occurred_at": None, "evidence_ids": list(linked_evidence(db, kind, str(row[2]))),
                })
        if kind == "cognition":
            for row in db.execute(
                "SELECT id, prior_cognition_id, replacement_cognition_id, reason, revision "
                "FROM cognition_transitions WHERE prior_cognition_id = ? "
                "OR replacement_cognition_id = ? ORDER BY revision, id",
                (item_id, item_id),
            ).fetchall():
                replacement = str(row[2])
                transitions.append(
                    {
                        "transition_id": str(row[0]),
                        "transition_kind": str(row[3]),
                        "object_kind": kind,
                        "prior_item_id": str(row[1]),
                        "replacement_item_id": replacement,
                        "revision": int(row[4]),
                        "occurred_at": None,
                        "evidence_ids": list(linked_evidence(db, kind, replacement)),
                    }
                )
        key = {
            "cognition": "prior_cognition_id",
            "relationship": "prior_relationship_id",
            "event": "prior_event_id",
            "entity": None,
        }[kind]
        if key is not None:
            for row in db.execute(
                f"SELECT id, reason, revision, created_at FROM retraction WHERE {key} = ? "
                "ORDER BY revision, id",
                (item_id,),
            ).fetchall():
                transitions.append(
                    {
                        "transition_id": str(row[0]),
                        "transition_kind": str(row[1]),
                        "object_kind": kind,
                        "prior_item_id": item_id,
                        "replacement_item_id": None,
                        "revision": int(row[2]),
                        "occurred_at": str(row[3]),
                        "evidence_ids": [],
                    }
                )
        for ledger_id, content, payload in db.execute(
            "SELECT id, content, payload_json FROM evidence_ledger ORDER BY id"
        ).fetchall():
            data = _json_mapping(content)
            payload_data = _json_mapping(payload)
            relation = data.get("relation")
            if kind in {"relationship", "event", "entity"} and relation == "corrects":
                prefix = kind
                ledger_prior = data.get(f"prior_{prefix}_id")
                ledger_replacement = data.get(f"replacement_{prefix}_id")
                if item_id not in {ledger_prior, ledger_replacement}:
                    continue
                replacement_text = (
                    str(ledger_replacement)
                    if isinstance(ledger_replacement, str)
                    else None
                )
                transitions.append(
                    {
                        "transition_id": str(ledger_id),
                        "transition_kind": "corrects",
                        "object_kind": kind,
                        "prior_item_id": (
                            str(ledger_prior) if isinstance(ledger_prior, str) else None
                        ),
                        "replacement_item_id": replacement_text,
                        "revision": None,
                        "occurred_at": None,
                        "evidence_ids": (
                            []
                            if replacement_text is None
                            else list(linked_evidence(db, kind, replacement_text))
                        ),
                    }
                )
            elif kind == "entity" and relation == "alias":
                alias_prior = data.get("merged_entity_id")
                alias_replacement = data.get("canonical_entity_id")
                if item_id not in {alias_prior, alias_replacement}:
                    continue
                raw_ids = payload_data.get("evidence_ids")
                transitions.append(
                    {
                        "transition_id": str(ledger_id),
                        "transition_kind": "alias",
                        "object_kind": kind,
                        "prior_item_id": (
                            str(alias_prior) if isinstance(alias_prior, str) else None
                        ),
                        "replacement_item_id": (
                            str(alias_replacement)
                            if isinstance(alias_replacement, str)
                            else None
                        ),
                        "revision": None,
                        "occurred_at": None,
                        "evidence_ids": sorted(
                            str(value)
                            for value in raw_ids
                            if isinstance(value, str)
                        )
                        if isinstance(raw_ids, list)
                        else [],
                    }
                )
        deduped = {transition["transition_id"]: transition for transition in transitions}
        return sorted(
            deduped.values(),
            key=lambda value: (
                value["revision"] is None,
                -1 if value["revision"] is None else value["revision"],
                value["transition_id"],
            ),
        )

    def _job(self, read: CoherentRevisionRead, job_id: str) -> dict[str, object]:
        row = read.db.execute(
            "SELECT * FROM memory_world_job WHERE job_id = ? AND subject_id = ?",
            (job_id, self._subject_id),
        ).fetchone()
        if row is None:
            raise TrustQueryError("job_not_found")
        outcome = read.db.execute(
            "SELECT * FROM terminal_outcome WHERE job_id = ? AND subject_id = ?",
            (job_id, self._subject_id),
        ).fetchone()
        evidence_ids = [str(value) for value in _json_list(row["evidence_ids_json"])]
        content_available = all(
            self._job_evidence_current(read.db, evidence_id)
            for evidence_id in evidence_ids
        )
        terminal_revision = (
            read.world_revision if outcome is None else int(outcome["world_revision"])
        )
        delivery: dict[str, object] = {
            "available": False,
            "outcome_id": None,
            "outcome_schema_version": None,
            "delivery_state": None,
            "attempts": 0,
            "next_attempt_at": None,
            "delivered_at": None,
            "last_error": None,
            "result_hash": None,
        }
        if outcome is not None:
            delivery = {
                "available": True,
                "outcome_id": str(outcome["outcome_id"]),
                "outcome_schema_version": int(outcome["schema_version"]),
                "delivery_state": str(outcome["delivery_state"]),
                "attempts": int(outcome["attempts"]),
                "next_attempt_at": cast(str | None, outcome["next_attempt_at"]),
                "delivered_at": cast(str | None, outcome["delivered_at"]),
                "last_error": cast(str | None, outcome["last_error"]),
                "result_hash": str(outcome["result_hash"]),
            }
        return {
            "schema_version": TRUST_SCHEMA_VERSION,
            "subject_id": self._subject_id,
            "world_revision": read.world_revision,
            "job_id": str(row["job_id"]),
            "job_schema_version": int(row["job_schema_version"]),
            "acceptance": {
                "boundary_event_id": str(row["boundary_event_id"]),
                "boundary_schema_version": int(row["boundary_schema_version"]),
                "provider_name": str(row["provider_name"]),
                "parent_session_id": str(row["parent_session_id"]),
                "result_session_id": str(row["result_session_id"]),
                "boundary_mode": str(row["boundary_mode"]),
                "host_id": str(row["host_id"]),
                "evidence_ids": evidence_ids,
                "delivery_receipt": _json_mapping(row["delivery_receipt_json"]),
                "created_at": str(row["created_at"]),
            },
            "worker": {
                "state": str(row["state"]),
                "attempts": int(row["attempts"]),
                "next_attempt_at": cast(str | None, row["next_attempt_at"]),
                "last_error_type": cast(str | None, row["last_error_type"]),
            },
            "core_terminal": {
                "terminal_state": cast(str | None, row["terminal_state"]),
                "terminal_detail": (
                    cast(str | None, row["terminal_detail"])
                    if content_available
                    else None
                ),
                "world_revision": terminal_revision,
                "world_result": (
                    _json_mapping(row["world_result_json"])
                    if content_available
                    else {}
                ),
                "content_available": content_available,
                "result_hash": cast(str | None, row["result_hash"]),
                "completed_at": cast(str | None, row["completed_at"]),
            },
            "delivery": delivery,
            "host_event": {
                "available": False,
                "scope": "hermes_state",
                "reason": "not_stored_in_core_database",
            },
        }

    def _job_evidence_current(
        self, db: sqlite3.Connection, evidence_id: str
    ) -> bool:
        row = db.execute(
            "SELECT subject_id, deleted_at, allow_local_read, allow_cloud_read, "
            "allow_inference FROM evidence WHERE id = ?",
            (evidence_id,),
        ).fetchone()
        if row is None or str(row[0]) != self._subject_id:
            return False
        return (
            evidence_state(
                {
                    "deleted_at": row[1],
                    "allow_local_read": row[2],
                    "allow_cloud_read": row[3],
                    "allow_inference": row[4],
                },
                surface=self._surface,
            )
            is None
        )
