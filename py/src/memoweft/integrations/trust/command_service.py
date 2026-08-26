"""Subject-bound N5 Trust Command service over the production World store."""
from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, cast

from ...clock import Clock, system_clock, to_iso_z
from .command_store import (
    CommandMutation,
    CommandStore,
    TrustCommandError,
    command_canonical_json,
)
from .currentness import linked_evidence, world_item_lifecycle
from .model import (
    CommandEnvelopeV1,
    CommandReceiptV1,
    TRUST_CAPABILITIES_VERSION,
    TRUST_SCHEMA_VERSION,
    TrustCommandOperation,
    TrustCommandTargetKind,
    TrustWorldItemKind,
)
from .revision import current_world_revision


_OPERATIONS: tuple[TrustCommandOperation, ...] = (
    "update_evidence_permissions",
    "correct_world_item",
    "retract_world_item",
    "forget_evidence",
    "archive_world_item",
    "mute_world_item",
)
_OPERATION_SET = frozenset(_OPERATIONS)
_TARGET_KINDS = frozenset(
    {"evidence", "entity", "relationship", "event", "cognition"}
)
_WORLD_KINDS = frozenset({"entity", "relationship", "event", "cognition"})
_COMMAND_KEYS = frozenset(
    {
        "schema_version",
        "command_id",
        "subject_id",
        "actor",
        "expected_world_revision",
        "operation",
        "target_kind",
        "target_id",
        "payload",
        "submitted_at",
    }
)
_SUBMIT_TOOL_KEYS = frozenset(_COMMAND_KEYS - {"schema_version", "subject_id"})


TRUST_COMMAND_PROVIDER_TOOL_SCHEMAS: tuple[dict[str, object], ...] = (
    {
        "name": "memoweft_trust_command_capabilities",
        "description": "Return MemoWeft subject-bound Trust Command capabilities and current World revision.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "memoweft_submit_trust_command",
        "description": "Submit one explicit subject-bound Trust mutation with optimistic World revision fencing.",
        "parameters": {
            "type": "object",
            "properties": {
                "command_id": {"type": "string"},
                "actor": {"type": "string"},
                "expected_world_revision": {"type": "integer", "minimum": 0},
                "operation": {"type": "string", "enum": list(_OPERATIONS)},
                "target_kind": {
                    "type": "string",
                    "enum": ["evidence", "entity", "relationship", "event", "cognition"],
                },
                "target_id": {"type": "string"},
                "payload": {"type": "object"},
                "submitted_at": {"type": "string"},
            },
            "required": sorted(_SUBMIT_TOOL_KEYS),
            "additionalProperties": False,
        },
    },
    {
        "name": "memoweft_get_trust_command_receipt",
        "description": "Read one durable Trust Command receipt by command identity.",
        "parameters": {
            "type": "object",
            "properties": {"command_id": {"type": "string"}},
            "required": ["command_id"],
            "additionalProperties": False,
        },
    },
)


class CommandService:
    """Validate and execute one closed Trust command against one subject."""

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
            raise TrustCommandError("invalid_subject_id")
        if not host_id:
            raise TrustCommandError("invalid_host_id")
        self._db_path = Path(db_path)
        self._subject_id = subject_id
        self._host_id = host_id
        self._clock = clock
        self._store = CommandStore(self._db_path, clock=clock)

    def submit_command(
        self, raw_command: Mapping[str, object]
    ) -> CommandReceiptV1:
        command = self._command(raw_command)
        return self._store.submit(command, self._mutate)

    def get_command_receipt(self, command_id: str) -> CommandReceiptV1:
        return self._store.get_receipt(
            self._identifier(command_id, "invalid_command_id"),
            subject_id=self._subject_id,
        )

    def get_capabilities(self) -> dict[str, object]:
        try:
            db = sqlite3.connect(
                self._db_path.resolve().as_uri() + "?mode=ro", uri=True
            )
        except (OSError, sqlite3.Error) as exc:
            raise TrustCommandError("trust_database_unavailable") from exc
        try:
            revision = current_world_revision(db)
        finally:
            db.close()
        return {
            "schema_version": TRUST_SCHEMA_VERSION,
            "subject_id": self._subject_id,
            "world_revision": revision,
            "capabilities_version": TRUST_CAPABILITIES_VERSION,
            "operations": list(_OPERATIONS),
            "result_states": [
                "applied",
                "no_change",
                "revision_conflict",
                "rejected",
            ],
            "expected_revision_fence": True,
            "durable_receipts": True,
            "mutation_surface": True,
        }

    def execute_provider_tool(
        self, tool_name: str, args: Mapping[str, object]
    ) -> dict[str, object]:
        if not isinstance(tool_name, str) or not isinstance(args, Mapping):
            raise TrustCommandError("invalid_trust_command_tool_call")
        raw = dict(args)
        if tool_name == "memoweft_trust_command_capabilities":
            self._require_keys(raw, allowed=frozenset())
            return self.get_capabilities()
        if tool_name == "memoweft_submit_trust_command":
            self._require_keys(
                raw, allowed=_SUBMIT_TOOL_KEYS, required=_SUBMIT_TOOL_KEYS
            )
            command = {
                "schema_version": TRUST_SCHEMA_VERSION,
                "subject_id": self._subject_id,
                **raw,
            }
            receipt = self.submit_command(command)
            return {
                "schema_version": TRUST_SCHEMA_VERSION,
                "subject_id": self._subject_id,
                "world_revision": receipt["after_revision"],
                "receipt": receipt,
                "mutation_surface": True,
            }
        if tool_name == "memoweft_get_trust_command_receipt":
            self._require_keys(
                raw,
                allowed=frozenset({"command_id"}),
                required=frozenset({"command_id"}),
            )
            receipt = self.get_command_receipt(
                self._identifier(raw["command_id"], "invalid_command_id")
            )
            return {
                "schema_version": TRUST_SCHEMA_VERSION,
                "subject_id": self._subject_id,
                "world_revision": receipt["after_revision"],
                "receipt": receipt,
                "mutation_surface": False,
            }
        raise TrustCommandError("unknown_trust_command_tool")

    @staticmethod
    def _require_keys(
        value: Mapping[str, object],
        *,
        allowed: frozenset[str],
        required: frozenset[str] = frozenset(),
    ) -> None:
        keys = frozenset(value)
        if keys - allowed:
            raise TrustCommandError("unexpected_trust_command_argument")
        if required - keys:
            raise TrustCommandError("missing_trust_command_argument")

    @staticmethod
    def _identifier(value: object, code: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 512
        ):
            raise TrustCommandError(code)
        return value

    def _command(self, raw_command: Mapping[str, object]) -> CommandEnvelopeV1:
        if not isinstance(raw_command, Mapping):
            raise TrustCommandError("invalid_trust_command")
        raw = dict(raw_command)
        self._require_keys(raw, allowed=_COMMAND_KEYS, required=_COMMAND_KEYS)
        if raw["schema_version"] != TRUST_SCHEMA_VERSION:
            raise TrustCommandError("unsupported_trust_command_schema")
        if raw["subject_id"] != self._subject_id:
            raise TrustCommandError("trust_command_subject_mismatch")
        command_id = self._identifier(raw["command_id"], "invalid_command_id")
        actor = self._identifier(raw["actor"], "invalid_command_actor")
        target_id = self._identifier(raw["target_id"], "invalid_target_id")
        submitted_at = self._identifier(raw["submitted_at"], "invalid_submitted_at")
        expected = raw["expected_world_revision"]
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise TrustCommandError("invalid_expected_world_revision")
        operation = raw["operation"]
        if not isinstance(operation, str) or operation not in _OPERATION_SET:
            raise TrustCommandError("unsupported_trust_command_operation")
        target_kind = raw["target_kind"]
        if not isinstance(target_kind, str) or target_kind not in _TARGET_KINDS:
            raise TrustCommandError("unsupported_trust_command_target_kind")
        payload = raw["payload"]
        if not isinstance(payload, Mapping):
            raise TrustCommandError("invalid_trust_command_payload")
        normalized_payload = dict(payload)
        command_canonical_json(normalized_payload)
        return CommandEnvelopeV1(
            schema_version=TRUST_SCHEMA_VERSION,
            command_id=command_id,
            subject_id=self._subject_id,
            actor=actor,
            expected_world_revision=expected,
            operation=operation,
            target_kind=cast(TrustCommandTargetKind, target_kind),
            target_id=target_id,
            payload=normalized_payload,
            submitted_at=submitted_at,
        )

    def _mutate(
        self,
        db: sqlite3.Connection,
        command: CommandEnvelopeV1,
        completed_at: str,
    ) -> CommandMutation:
        operation = command["operation"]
        if operation == "update_evidence_permissions":
            return self._update_permissions(db, command)
        if operation == "forget_evidence":
            return self._forget(db, command, completed_at)
        if operation in {"archive_world_item", "mute_world_item"}:
            return self._lifecycle(db, command, completed_at)
        if operation == "correct_world_item":
            return self._correct(db, command, completed_at)
        if operation == "retract_world_item":
            return self._retract(db, command, completed_at)
        raise TrustCommandError("unsupported_trust_command_operation")

    @staticmethod
    def _payload_keys(
        command: CommandEnvelopeV1,
        *,
        allowed: frozenset[str],
        require_any: bool = False,
    ) -> dict[str, object]:
        payload = command["payload"]
        keys = frozenset(payload)
        if keys - allowed or (require_any and not keys):
            raise TrustCommandError("invalid_trust_command_payload")
        return payload

    def _update_permissions(
        self, db: sqlite3.Connection, command: CommandEnvelopeV1
    ) -> CommandMutation:
        if command["target_kind"] != "evidence":
            return CommandMutation("rejected")
        fields = frozenset(
            {"allow_local_read", "allow_cloud_read", "allow_inference"}
        )
        payload = self._payload_keys(command, allowed=fields, require_any=True)
        if any(not isinstance(value, bool) for value in payload.values()):
            raise TrustCommandError("invalid_permission_value")
        row = db.execute(
            "SELECT allow_local_read, allow_cloud_read, allow_inference "
            "FROM evidence WHERE id = ? AND subject_id = ?",
            (command["target_id"], self._subject_id),
        ).fetchone()
        if row is None:
            return CommandMutation("rejected")
        current = {
            "allow_local_read": int(row[0]) == 1,
            "allow_cloud_read": int(row[1]) == 1,
            "allow_inference": int(row[2]) == 1,
        }
        updated = {**current, **payload}
        if updated == current:
            return CommandMutation("no_change")
        db.execute(
            "UPDATE evidence SET allow_local_read = ?, allow_cloud_read = ?, "
            "allow_inference = ? WHERE id = ? AND subject_id = ?",
            (
                int(bool(updated["allow_local_read"])),
                int(bool(updated["allow_cloud_read"])),
                int(bool(updated["allow_inference"])),
                command["target_id"],
                self._subject_id,
            ),
        )
        return CommandMutation("applied", (command["target_id"],))

    def _forget(
        self,
        db: sqlite3.Connection,
        command: CommandEnvelopeV1,
        completed_at: str,
    ) -> CommandMutation:
        self._payload_keys(command, allowed=frozenset())
        if command["target_kind"] != "evidence":
            return CommandMutation("rejected")
        row = db.execute(
            "SELECT deleted_at FROM evidence WHERE id = ? AND subject_id = ?",
            (command["target_id"], self._subject_id),
        ).fetchone()
        if row is None:
            return CommandMutation("rejected")
        if row[0] is not None:
            return CommandMutation("no_change")
        db.execute(
            "UPDATE evidence SET deleted_at = ?, origin_id = NULL "
            "WHERE id = ? AND subject_id = ? AND deleted_at IS NULL",
            (completed_at, command["target_id"], self._subject_id),
        )
        return CommandMutation("applied", (command["target_id"],))

    @staticmethod
    def _world_table(kind: str) -> tuple[str, str]:
        return {
            "entity": ("entity", "world_id"),
            "relationship": ("relationship", "world_id"),
            "event": ("world_event", "world_id"),
            "cognition": ("cognition", "subject_id"),
        }[kind]

    def _world_row(
        self, db: sqlite3.Connection, kind: str, item_id: str
    ) -> sqlite3.Row | None:
        table, subject_column = self._world_table(kind)
        return cast(
            sqlite3.Row | None,
            db.execute(
                f"SELECT * FROM {table} WHERE id = ? AND {subject_column} = ?",
                (item_id, self._subject_id),
            ).fetchone(),
        )

    def _lifecycle(
        self,
        db: sqlite3.Connection,
        command: CommandEnvelopeV1,
        completed_at: str,
    ) -> CommandMutation:
        self._payload_keys(command, allowed=frozenset())
        kind = command["target_kind"]
        if kind not in _WORLD_KINDS:
            return CommandMutation("rejected")
        row = self._world_row(db, kind, command["target_id"])
        if row is None or row["invalid_at"] is not None:
            return CommandMutation("rejected")
        archived_at, muted_at = world_item_lifecycle(
            db,
            self._subject_id,
            cast(TrustWorldItemKind, kind),
            command["target_id"],
        )
        column = (
            "archived_at"
            if command["operation"] == "archive_world_item"
            else "muted_at"
        )
        current_value = archived_at if column == "archived_at" else muted_at
        if current_value is not None:
            return CommandMutation("no_change")
        values = {
            "archived_at": completed_at if column == "archived_at" else archived_at,
            "muted_at": completed_at if column == "muted_at" else muted_at,
        }
        db.execute(
            "INSERT INTO world_item_lifecycle (subject_id, object_kind, item_id, "
            "archived_at, muted_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(subject_id, object_kind, item_id) DO UPDATE SET "
            "archived_at = excluded.archived_at, muted_at = excluded.muted_at, "
            "updated_at = excluded.updated_at",
            (
                self._subject_id,
                kind,
                command["target_id"],
                values["archived_at"],
                values["muted_at"],
                completed_at,
            ),
        )
        if kind == "cognition":
            db.execute(
                f"UPDATE cognition SET {column} = ?, updated_at = ? WHERE id = ? "
                "AND subject_id = ?",
                (completed_at, completed_at, command["target_id"], self._subject_id),
            )
        return CommandMutation("applied", (command["target_id"],))

    def _target_is_current(
        self, db: sqlite3.Connection, kind: str, item_id: str
    ) -> bool:
        row = self._world_row(db, kind, item_id)
        if row is None or row["invalid_at"] is not None:
            return False
        if kind == "cognition" and (
            row["archived_at"] is not None or row["muted_at"] is not None
        ):
            return False
        archived_at, muted_at = world_item_lifecycle(
            db, self._subject_id, cast(TrustWorldItemKind, kind), item_id
        )
        return archived_at is None and muted_at is None

    def _insert_correction_evidence(
        self,
        db: sqlite3.Connection,
        command: CommandEnvelopeV1,
        text: str,
        completed_at: str,
    ) -> str:
        evidence_id = "evidence-trust-command-" + sha256(
            command_canonical_json(
                ["trust_command_correction_v1", self._subject_id, command["command_id"]]
            ).encode("utf-8")
        ).hexdigest()
        prior_ids = linked_evidence(
            db,
            cast(TrustWorldItemKind, command["target_kind"]),
            command["target_id"],
        )
        permissions = {
            "allow_local_read": command["payload"].get("allow_local_read", True),
            "allow_cloud_read": command["payload"].get("allow_cloud_read", True),
            "allow_inference": command["payload"].get("allow_inference", True),
        }
        if any(not isinstance(value, bool) for value in permissions.values()):
            raise TrustCommandError("invalid_permission_value")
        db.execute(
            "INSERT INTO evidence (id, subject_id, source_kind, host_id, origin_id, "
            "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
            "allow_cloud_read, allow_inference, corrects_evidence_id, deleted_at, "
            "preceding_ai_context) VALUES (?, ?, 'spoken', ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, NULL, NULL)",
            (
                evidence_id,
                self._subject_id,
                self._host_id,
                "trust-command:"
                f"{command['command_id']}:{command['target_kind']}:"
                f"{command['target_id']}:correction",
                command["submitted_at"],
                completed_at,
                text,
                text,
                int(bool(permissions["allow_local_read"])),
                int(bool(permissions["allow_cloud_read"])),
                int(bool(permissions["allow_inference"])),
                prior_ids[0] if prior_ids else None,
            ),
        )
        db.execute(
            "INSERT INTO boundary_evidence_content (evidence_id, raw_content_hash) "
            "VALUES (?, ?)",
            (evidence_id, sha256(text.encode("utf-8")).hexdigest()),
        )
        return evidence_id

    def _apply_job(self, command: CommandEnvelopeV1, evidence_ids: tuple[str, ...]) -> Any:
        from ..hermes.world_worker import ClaimedWorldJob

        formal_target = command_canonical_json(
            {
                "boundary_schema_version": 1,
                "provider_name": "memoweft",
                "parent_session_id": "trust-command",
                "result_session_id": "trust-command",
                "mode": "in_place",
                "subject_id": self._subject_id,
                "host_id": self._host_id,
            }
        )
        return ClaimedWorldJob(
            job_id=f"trust-command:{command['command_id']}",
            boundary_event_id=f"trust-command:{command['command_id']}",
            boundary_payload_hash="0" * 64,
            boundary_schema_version=1,
            provider_name="memoweft",
            parent_session_id="trust-command",
            result_session_id="trust-command",
            boundary_mode="in_place",
            formal_target_json=formal_target,
            formal_target_hash=sha256(formal_target.encode("utf-8")).hexdigest(),
            subject_id=self._subject_id,
            host_id=self._host_id,
            evidence_ids_json=command_canonical_json(list(evidence_ids)),
            attempts=1,
            claim_owner="trust-command",
            claim_token=command["command_id"],
            claimed_at=command["submitted_at"],
            lease_expires_at=to_iso_z(self._clock() + timedelta(minutes=5)),
            fencing_generation=1,
        )

    def _processor(self) -> Any:
        from ..hermes.batch_adapter import HermesBatchAdapterProcessor

        def no_model(*_args: object, **_kwargs: object) -> Mapping[str, object]:
            raise AssertionError("Trust Command correction must not call a model")

        return HermesBatchAdapterProcessor(
            str(self._db_path), no_model, clock=self._clock
        )

    def _correct(
        self,
        db: sqlite3.Connection,
        command: CommandEnvelopeV1,
        completed_at: str,
    ) -> CommandMutation:
        kind = command["target_kind"]
        if kind not in {"cognition", "relationship", "event"}:
            return CommandMutation("rejected")
        allowed = frozenset(
            {
                "correction_text",
                "allow_local_read",
                "allow_cloud_read",
                "allow_inference",
                "relation_type",
                "source_entity_id",
                "target_entity_id",
                "occurred_at",
                "time_expression",
            }
        )
        payload = self._payload_keys(command, allowed=allowed)
        text = payload.get("correction_text")
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > 16000
        ):
            raise TrustCommandError("invalid_correction_text")
        if not self._target_is_current(db, kind, command["target_id"]):
            return CommandMutation("rejected")
        from ..hermes.batch_adapter import BatchItem, TrustCommandApplyError

        db.execute("SAVEPOINT trust_command_formal_apply")
        try:
            evidence_id = self._insert_correction_evidence(
                db, command, text, completed_at
            )
            support = ((evidence_id, 0, len(text), text),)
            row = self._world_row(db, kind, command["target_id"])
            assert row is not None
            if kind == "cognition":
                target = db.execute(
                    "SELECT te.canonical_name, te.kind, pe.canonical_name, pe.kind "
                    "FROM cognition_target ct JOIN entity te "
                    "ON te.id = ct.target_entity_id LEFT JOIN entity pe "
                    "ON pe.id = ct.perspective_entity_id WHERE ct.cognition_id = ?",
                    (command["target_id"],),
                ).fetchone()
                item = BatchItem(
                    action="correct",
                    proposition=text,
                    statement_kind=str(row["content_type"]),
                    formed_by="stated",
                    supports=support,
                    corrects_cognition_id=command["target_id"],
                    entity_canonical_name=(None if target is None else str(target[0])),
                    entity_kind=(None if target is None else str(target[1])),
                    perspective_holder_name=(
                        None if target is None or target[2] is None else str(target[2])
                    ),
                    perspective_holder_kind=(
                        None if target is None or target[3] is None else str(target[3])
                    ),
                )
            elif kind == "relationship":
                from ..hermes.batch_adapter import owner_entity_id_for

                source_id = str(payload.get("source_entity_id") or row["source_entity_id"])
                target_id = str(payload.get("target_entity_id") or row["target_entity_id"])
                source = db.execute(
                    "SELECT canonical_name, kind FROM entity WHERE id = ? AND world_id = ?",
                    (source_id, self._subject_id),
                ).fetchone()
                target = db.execute(
                    "SELECT canonical_name, kind FROM entity WHERE id = ? AND world_id = ?",
                    (target_id, self._subject_id),
                ).fetchone()
                if target is None or (
                    source_id != owner_entity_id_for(self._subject_id) and source is None
                ):
                    db.execute("ROLLBACK TO trust_command_formal_apply")
                    db.execute("RELEASE trust_command_formal_apply")
                    return CommandMutation("rejected")
                item = BatchItem(
                    action="correct",
                    proposition=text,
                    statement_kind="relationship",
                    formed_by="stated",
                    supports=support,
                    relation_type=str(payload.get("relation_type") or row["relation_type"]),
                    target_canonical_name=str(target[0]),
                    target_entity_kind=str(target[1]),
                    source_canonical_name=(None if source is None else str(source[0])),
                    source_entity_kind=(None if source is None else str(source[1])),
                    corrects_relationship_id=command["target_id"],
                )
                if item.apply_identity(self._subject_id) == command["target_id"]:
                    db.execute("ROLLBACK TO trust_command_formal_apply")
                    db.execute("RELEASE trust_command_formal_apply")
                    return CommandMutation("rejected")
            else:
                def event_members(raw: object) -> tuple[tuple[str, str], ...] | None:
                    try:
                        ids = json.loads(str(raw) or "[]")
                    except ValueError:
                        return None
                    if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
                        return None
                    members: list[tuple[str, str]] = []
                    for entity_id in ids:
                        entity = db.execute(
                            "SELECT canonical_name, kind FROM entity WHERE id = ? "
                            "AND world_id = ?",
                            (entity_id, self._subject_id),
                        ).fetchone()
                        if entity is None:
                            return None
                        members.append((str(entity[0]), str(entity[1])))
                    return tuple(members)

                participants = event_members(row["participants_json"])
                objects = event_members(row["objects_json"])
                if participants is None or objects is None:
                    db.execute("ROLLBACK TO trust_command_formal_apply")
                    db.execute("RELEASE trust_command_formal_apply")
                    return CommandMutation("rejected")
                item = BatchItem(
                    action="correct",
                    proposition=text,
                    statement_kind="event",
                    formed_by="stated",
                    supports=support,
                    corrects_event_id=command["target_id"],
                    event_participants=participants,
                    event_objects=objects,
                    event_occurred_at=cast(
                        str | None, payload.get("occurred_at", row["occurred_at"])
                    ),
                    event_time_expression=cast(
                        str | None,
                        payload.get("time_expression", row["time_expression"]),
                    ),
                )
            applied = self._processor().apply_trust_command_items_in_transaction(
                db,
                self._apply_job(command, (evidence_id,)),
                (item,),
                now_text=completed_at,
            )
            if not applied.wrote_any:
                db.execute("ROLLBACK TO trust_command_formal_apply")
                db.execute("RELEASE trust_command_formal_apply")
                return CommandMutation("no_change")
            result = applied.results[0]
            replacement = (
                result.get("replacement_cognition_id")
                or result.get("replacement_relationship_id")
                or result.get("replacement_event_id")
            )
            if not isinstance(replacement, str):
                raise TrustCommandError("invalid_trust_command_apply_result")
            db.execute("RELEASE trust_command_formal_apply")
            return CommandMutation(
                "applied",
                (command["target_id"], replacement, evidence_id),
                applied.transition_ids,
            )
        except TrustCommandApplyError:
            db.execute("ROLLBACK TO trust_command_formal_apply")
            db.execute("RELEASE trust_command_formal_apply")
            return CommandMutation("rejected")

    def _retract(
        self,
        db: sqlite3.Connection,
        command: CommandEnvelopeV1,
        completed_at: str,
    ) -> CommandMutation:
        self._payload_keys(command, allowed=frozenset())
        kind = command["target_kind"]
        if kind not in {"cognition", "relationship", "event"}:
            return CommandMutation("rejected")
        if not self._target_is_current(db, kind, command["target_id"]):
            return CommandMutation("rejected")
        from ..hermes.batch_adapter import BatchItem, TrustCommandApplyError

        row = self._world_row(db, kind, command["target_id"])
        assert row is not None
        item = BatchItem(
            action="correct",
            proposition="",
            statement_kind=(str(row["content_type"]) if kind == "cognition" else kind),
            formed_by="stated",
            supports=(),
            corrects_cognition_id=(command["target_id"] if kind == "cognition" else None),
            corrects_relationship_id=(command["target_id"] if kind == "relationship" else None),
            corrects_event_id=(command["target_id"] if kind == "event" else None),
            retract=True,
        )
        db.execute("SAVEPOINT trust_command_formal_apply")
        try:
            applied = self._processor().apply_trust_command_items_in_transaction(
                db,
                self._apply_job(command, ()),
                (item,),
                now_text=completed_at,
            )
            if not applied.wrote_any:
                db.execute("ROLLBACK TO trust_command_formal_apply")
                db.execute("RELEASE trust_command_formal_apply")
                return CommandMutation("no_change")
            db.execute("RELEASE trust_command_formal_apply")
            return CommandMutation(
                "applied", (command["target_id"],), applied.transition_ids
            )
        except TrustCommandApplyError:
            db.execute("ROLLBACK TO trust_command_formal_apply")
            db.execute("RELEASE trust_command_formal_apply")
            return CommandMutation("rejected")


__all__ = [
    "CommandService",
    "TRUST_COMMAND_PROVIDER_TOOL_SCHEMAS",
]
