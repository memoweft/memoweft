"""Subject-bound Portable v4 export, plan, atomic apply and receipt service."""
from __future__ import annotations

from pathlib import Path
import json
import sqlite3
from typing import Any, Mapping, TypedDict

from ...clock import Clock, system_clock, to_iso_z
from ...portable import (
    BUNDLE_SCHEMA_VERSION,
    ImportPlan,
    build_bundle,
    canonical_json,
    canonical_sha256,
    import_bundle,
)
from ...store import open_db
from ...store.cognition import SqliteCognitionStore
from ...store.event import SqliteEventStore
from ...store.evidence import SqliteEvidenceStore
from ...store.interaction_context import SqliteInteractionContextStore
from ...store.semantic_resolution import SqliteSemanticResolutionStore
from .revision import advance_world_revision, current_world_revision


PORTABLE_SERVICE_SCHEMA_VERSION = 1
PORTABLE_CAPABILITIES_VERSION = "portable-v4"


class PortableError(RuntimeError):
    """Stable machine-readable Portable service failure."""


class _StoreDeps(TypedDict):
    evidence_store: SqliteEvidenceStore
    event_store: SqliteEventStore
    cognition_store: SqliteCognitionStore
    interaction_context_store: SqliteInteractionContextStore
    semantic_resolution_store: SqliteSemanticResolutionStore


def _plan_dict(plan: ImportPlan) -> dict[str, object]:
    return {
        "plan_version": 1,
        "mode": plan.mode,
        "valid": plan.valid,
        "errors": list(plan.errors),
        "warnings": list(plan.warnings),
        "bundle_id": plan.bundle_id,
        "source_subject_id": plan.source_subject_id,
        "target_subject_id": plan.target_subject_id,
        "target_world_revision": plan.target_world_revision,
        "target_snapshot_hash": plan.target_snapshot_hash,
        "conflicts": [dict(value) for value in plan.conflicts],
        "counts": {
            "evidence": plan.counts.evidence,
            "events": plan.counts.events,
            "cognitions": plan.counts.cognitions,
            "event_evidence": plan.counts.event_evidence,
            "cognition_evidence": plan.counts.cognition_evidence,
            "interaction_contexts": plan.counts.interaction_contexts,
            "semantic_resolutions": plan.counts.semantic_resolutions,
            "entities": plan.counts.entities,
            "entity_evidence": plan.counts.entity_evidence,
            "relationships": plan.counts.relationships,
            "world_events": plan.counts.world_events,
            "relationship_evidence": plan.counts.relationship_evidence,
            "world_event_evidence": plan.counts.world_event_evidence,
            "cognition_targets": plan.counts.cognition_targets,
            "retractions": plan.counts.retractions,
            "cognition_transitions": plan.counts.cognition_transitions,
            "world_item_lifecycle": plan.counts.world_item_lifecycle,
            "evidence_tombstones": plan.counts.evidence_tombstones,
        },
        "duplicates": {
            "evidence": plan.duplicates.evidence,
            "events": plan.duplicates.events,
            "cognitions": plan.duplicates.cognitions,
            "entities": plan.duplicates.entities,
            "relationships": plan.duplicates.relationships,
            "world_events": plan.duplicates.world_events,
            "retractions": plan.duplicates.retractions,
            "cognition_transitions": plan.duplicates.cognition_transitions,
            "world_item_lifecycle": plan.duplicates.world_item_lifecycle,
        },
        "would_advance_revision": plan.would_advance_revision,
        "plan_hash": plan.plan_hash,
        "command_id": plan.command_id,
        "receipt_id": plan.receipt_id,
    }


class PortableService:
    """One Core service for Portable v4 across Hermes/DSH/Experience hosts."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        subject_id: str,
        host_id: str,
        clock: Clock = system_clock,
    ) -> None:
        subject_id = subject_id.strip()
        host_id = host_id.strip()
        if not subject_id:
            raise PortableError("invalid_subject_id")
        if not host_id:
            raise PortableError("invalid_host_id")
        self._db_path = Path(db_path)
        self._subject_id = subject_id
        self._host_id = host_id
        self._clock = clock

    @staticmethod
    def _stores(db: sqlite3.Connection, clock: Clock) -> _StoreDeps:
        return {
            "evidence_store": SqliteEvidenceStore(db, clock=clock),
            "event_store": SqliteEventStore(db, clock=clock),
            "cognition_store": SqliteCognitionStore(db, clock=clock),
            "interaction_context_store": SqliteInteractionContextStore(
                db, clock=clock
            ),
            "semantic_resolution_store": SqliteSemanticResolutionStore(
                db, clock=clock
            ),
        }

    def get_capabilities(self) -> dict[str, object]:
        try:
            db = sqlite3.connect(
                self._db_path.resolve().as_uri() + "?mode=ro", uri=True
            )
        except (OSError, sqlite3.Error) as exc:
            raise PortableError("portable_database_unavailable") from exc
        try:
            revision = current_world_revision(db)
        except sqlite3.Error as exc:
            raise PortableError("portable_database_invalid") from exc
        finally:
            db.close()
        return {
            "schema_version": PORTABLE_SERVICE_SCHEMA_VERSION,
            "portable_schema_version": BUNDLE_SCHEMA_VERSION,
            "capabilities_version": PORTABLE_CAPABILITIES_VERSION,
            "subject_id": self._subject_id,
            "host_id": self._host_id,
            "world_revision": revision,
            "operations": ["export", "plan_import", "apply_import", "get_receipt"],
            "subject_remap": True,
            "revision_fence": True,
            "durable_receipts": True,
            "atomic_apply": True,
        }

    def export_bundle(self, *, exported_at: str | None = None) -> dict[str, Any]:
        try:
            db = open_db(str(self._db_path))
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            raise PortableError("portable_database_unavailable") from exc
        try:
            return build_bundle(
                db,
                self._subject_id,
                host_id=self._host_id,
                exported_at=exported_at or to_iso_z(self._clock()),
            )
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise PortableError("portable_export_failed") from exc
        finally:
            db.close()

    def plan_import(self, bundle: Mapping[str, object]) -> dict[str, object]:
        try:
            db = open_db(str(self._db_path))
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            raise PortableError("portable_database_unavailable") from exc
        try:
            plan = import_bundle(
                dict(bundle),
                **self._stores(db, self._clock),
                mode="dryRun",
                world_db=db,
                target_subject_id=self._subject_id,
            )
            return _plan_dict(plan)
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise PortableError("portable_plan_failed") from exc
        finally:
            db.close()

    def apply_import(
        self,
        bundle: Mapping[str, object],
        *,
        plan_hash: str,
    ) -> dict[str, object]:
        if not isinstance(plan_hash, str) or len(plan_hash) != 64:
            raise PortableError("invalid_portable_plan_hash")
        try:
            db = open_db(str(self._db_path))
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            raise PortableError("portable_database_unavailable") from exc
        try:
            db.execute("BEGIN IMMEDIATE")
            replay = self._receipt_by_plan_hash(db, plan_hash)
            if replay is not None:
                if replay.get("bundle_id") != bundle.get("bundleId"):
                    raise PortableError("portable_receipt_bundle_mismatch")
                db.execute("COMMIT")
                return {**replay, "replayed": True}

            planned = import_bundle(
                dict(bundle),
                **self._stores(db, self._clock),
                mode="dryRun",
                world_db=db,
                target_subject_id=self._subject_id,
            )
            if not planned.valid:
                raise PortableError("portable_plan_invalid")
            if planned.plan_hash != plan_hash:
                raise PortableError("portable_plan_stale")

            applied = import_bundle(
                dict(bundle),
                **self._stores(db, self._clock),
                mode="merge",
                world_db=db,
                target_subject_id=self._subject_id,
                target_world_revision=planned.target_world_revision,
                target_snapshot_hash=planned.target_snapshot_hash,
            )
            if not applied.valid or applied.plan_hash != plan_hash:
                raise PortableError("portable_apply_diverged_from_plan")

            before_revision = int(planned.target_world_revision or 0)
            after_revision = (
                advance_world_revision(db)
                if applied.would_advance_revision
                else before_revision
            )
            completed_at = to_iso_z(self._clock())
            receipt: dict[str, object] = {
                "schema_version": PORTABLE_SERVICE_SCHEMA_VERSION,
                "receipt_id": applied.receipt_id,
                "command_id": applied.command_id,
                "plan_hash": applied.plan_hash,
                "bundle_id": applied.bundle_id,
                "source_subject_id": applied.source_subject_id,
                "target_subject_id": applied.target_subject_id,
                "target_world_revision": before_revision,
                "target_snapshot_hash": applied.target_snapshot_hash,
                "after_world_revision": after_revision,
                "result_state": (
                    "applied" if applied.would_advance_revision else "no_change"
                ),
                "counts": _plan_dict(applied)["counts"],
                "duplicates": _plan_dict(applied)["duplicates"],
                "completed_at": completed_at,
            }
            result_json = canonical_json(receipt)
            result_hash = canonical_sha256(receipt)
            db.execute(
                "INSERT INTO portable_import_receipt (receipt_id, schema_version, "
                "command_id, plan_hash, bundle_id, source_subject_id, "
                "target_subject_id, target_world_revision, target_snapshot_hash, "
                "after_world_revision, result_state, result_json, result_hash, "
                "completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    receipt["receipt_id"],
                    PORTABLE_SERVICE_SCHEMA_VERSION,
                    receipt["command_id"],
                    receipt["plan_hash"],
                    receipt["bundle_id"],
                    receipt["source_subject_id"],
                    receipt["target_subject_id"],
                    before_revision,
                    receipt["target_snapshot_hash"],
                    after_revision,
                    receipt["result_state"],
                    result_json,
                    result_hash,
                    completed_at,
                ),
            )
            db.execute("COMMIT")
            return {**receipt, "result_hash": result_hash, "replayed": False}
        except PortableError:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            if db.in_transaction:
                try:
                    db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise PortableError("portable_apply_failed") from exc
        finally:
            db.close()

    def get_receipt(self, receipt_id: str) -> dict[str, object]:
        if not isinstance(receipt_id, str) or not receipt_id:
            raise PortableError("invalid_portable_receipt_id")
        try:
            db = sqlite3.connect(
                self._db_path.resolve().as_uri() + "?mode=ro", uri=True
            )
        except (OSError, sqlite3.Error) as exc:
            raise PortableError("portable_database_unavailable") from exc
        try:
            row = db.execute(
                "SELECT result_json, result_hash FROM portable_import_receipt "
                "WHERE receipt_id = ? AND target_subject_id = ?",
                (receipt_id, self._subject_id),
            ).fetchone()
            if row is None:
                raise PortableError("portable_receipt_not_found")
            return self._decode_receipt(str(row[0]), str(row[1]))
        except sqlite3.Error as exc:
            raise PortableError("portable_receipt_read_failed") from exc
        finally:
            db.close()

    def _receipt_by_plan_hash(
        self, db: sqlite3.Connection, plan_hash: str
    ) -> dict[str, object] | None:
        row = db.execute(
            "SELECT result_json, result_hash FROM portable_import_receipt "
            "WHERE plan_hash = ? AND target_subject_id = ?",
            (plan_hash, self._subject_id),
        ).fetchone()
        if row is None:
            return None
        return self._decode_receipt(str(row[0]), str(row[1]))

    @staticmethod
    def _decode_receipt(result_json: str, result_hash: str) -> dict[str, object]:
        try:
            decoded = json.loads(result_json)
        except (TypeError, ValueError) as exc:
            raise PortableError("portable_receipt_invalid") from exc
        if not isinstance(decoded, dict) or canonical_sha256(decoded) != result_hash:
            raise PortableError("portable_receipt_hash_mismatch")
        return {**decoded, "result_hash": result_hash, "replayed": False}


__all__ = [
    "PORTABLE_CAPABILITIES_VERSION",
    "PORTABLE_SERVICE_SCHEMA_VERSION",
    "PortableError",
    "PortableService",
]
