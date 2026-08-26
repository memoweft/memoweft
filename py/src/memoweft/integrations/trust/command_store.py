"""Durable, idempotent SQLite command/receipt transaction boundary."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Callable, Literal, cast

from ...clock import Clock, system_clock, to_iso_z
from ...store import open_db
from .model import (
    CommandEnvelopeV1,
    CommandReceiptV1,
    TRUST_SCHEMA_VERSION,
    TrustCommandResultState,
)
from .revision import advance_world_revision, current_world_revision


class TrustCommandError(ValueError):
    """Stable fail-closed error for an invalid command or receipt lookup."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CommandMutation:
    result_state: Literal["applied", "no_change", "rejected"]
    affected_ids: tuple[str, ...] = ()
    transition_ids: tuple[str, ...] = ()


Mutation = Callable[[sqlite3.Connection, CommandEnvelopeV1, str], CommandMutation]


def command_canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise TrustCommandError("trust_command_not_canonical") from exc


def _hash(value: object) -> str:
    return sha256(command_canonical_json(value).encode("utf-8")).hexdigest()


class CommandStore:
    """One command identity plus one immutable receipt in one write lock."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        clock: Clock = system_clock,
    ) -> None:
        self._db_path = Path(db_path)
        self._clock = clock

    def submit(
        self, command: CommandEnvelopeV1, mutate: Mutation
    ) -> CommandReceiptV1:
        request_json = command_canonical_json(command)
        request_hash = sha256(request_json.encode("utf-8")).hexdigest()
        completed_at = to_iso_z(self._clock())
        db = open_db(str(self._db_path))
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT request_hash FROM trust_command WHERE command_id = ?",
                (command["command_id"],),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != request_hash:
                    raise TrustCommandError("command_id_conflict")
                receipt_row = db.execute(
                    "SELECT * FROM trust_command_receipt WHERE command_id = ?",
                    (command["command_id"],),
                ).fetchone()
                if receipt_row is None:
                    raise TrustCommandError("command_receipt_missing")
                receipt = self._receipt(receipt_row)
                db.execute("ROLLBACK")
                return receipt

            payload_json = command_canonical_json(command["payload"])
            db.execute(
                "INSERT INTO trust_command (command_id, schema_version, subject_id, "
                "actor, expected_world_revision, operation, target_kind, target_id, "
                "payload_json, request_hash, submitted_at) VALUES (?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?)",
                (
                    command["command_id"],
                    TRUST_SCHEMA_VERSION,
                    command["subject_id"],
                    command["actor"],
                    command["expected_world_revision"],
                    command["operation"],
                    command["target_kind"],
                    command["target_id"],
                    payload_json,
                    request_hash,
                    command["submitted_at"],
                ),
            )
            before_revision = current_world_revision(db)
            if command["expected_world_revision"] != before_revision:
                accepted = False
                state: TrustCommandResultState = "revision_conflict"
                mutation = CommandMutation("rejected")
                after_revision = before_revision
            else:
                mutation = mutate(db, command, completed_at)
                state = mutation.result_state
                accepted = state != "rejected"
                after_revision = (
                    advance_world_revision(db)
                    if state == "applied"
                    else before_revision
                )
            receipt_base: dict[str, object] = {
                "schema_version": TRUST_SCHEMA_VERSION,
                "command_id": command["command_id"],
                "accepted": accepted,
                "result_state": state,
                "before_revision": before_revision,
                "after_revision": after_revision,
                "affected_ids": list(mutation.affected_ids),
                "transition_ids": list(mutation.transition_ids),
                "completed_at": completed_at,
            }
            result_hash = _hash(receipt_base)
            db.execute(
                "INSERT INTO trust_command_receipt (command_id, schema_version, "
                "accepted, result_state, before_revision, after_revision, "
                "affected_ids_json, transition_ids_json, result_hash, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    command["command_id"],
                    TRUST_SCHEMA_VERSION,
                    int(accepted),
                    state,
                    before_revision,
                    after_revision,
                    command_canonical_json(list(mutation.affected_ids)),
                    command_canonical_json(list(mutation.transition_ids)),
                    result_hash,
                    completed_at,
                ),
            )
            db.execute("COMMIT")
            return cast(
                CommandReceiptV1,
                {**receipt_base, "result_hash": result_hash},
            )
        except BaseException:
            if db.in_transaction:
                try:
                    db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise
        finally:
            db.close()

    def get_receipt(
        self, command_id: str, *, subject_id: str | None = None
    ) -> CommandReceiptV1:
        try:
            db = sqlite3.connect(
                self._db_path.resolve().as_uri() + "?mode=ro",
                uri=True,
                isolation_level=None,
            )
        except (OSError, sqlite3.Error) as exc:
            raise TrustCommandError("trust_database_unavailable") from exc
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA query_only = ON")
            row = db.execute(
                "SELECT r.* FROM trust_command_receipt r "
                "JOIN trust_command c ON c.command_id = r.command_id "
                "WHERE r.command_id = ? AND (? IS NULL OR c.subject_id = ?)",
                (command_id, subject_id, subject_id),
            ).fetchone()
            if row is None:
                raise TrustCommandError("command_receipt_not_found")
            return self._receipt(row)
        except TrustCommandError:
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise TrustCommandError("trust_command_receipt_read_failed") from exc
        finally:
            db.close()

    @staticmethod
    def _receipt(row: sqlite3.Row) -> CommandReceiptV1:
        try:
            affected = json.loads(str(row["affected_ids_json"]))
            transitions = json.loads(str(row["transition_ids_json"]))
        except (TypeError, ValueError) as exc:
            raise TrustCommandError("invalid_command_receipt") from exc
        if not (
            isinstance(affected, list)
            and all(isinstance(value, str) for value in affected)
            and isinstance(transitions, list)
            and all(isinstance(value, str) for value in transitions)
        ):
            raise TrustCommandError("invalid_command_receipt")
        receipt_base: dict[str, object] = {
            "schema_version": int(row["schema_version"]),
            "command_id": str(row["command_id"]),
            "accepted": int(row["accepted"]) == 1,
            "result_state": str(row["result_state"]),
            "before_revision": int(row["before_revision"]),
            "after_revision": int(row["after_revision"]),
            "affected_ids": [str(value) for value in affected],
            "transition_ids": [str(value) for value in transitions],
            "completed_at": str(row["completed_at"]),
        }
        if _hash(receipt_base) != str(row["result_hash"]):
            raise TrustCommandError("command_receipt_hash_mismatch")
        return cast(
            CommandReceiptV1,
            {**receipt_base, "result_hash": str(row["result_hash"])},
        )


__all__ = [
    "CommandMutation",
    "CommandStore",
    "TrustCommandError",
    "command_canonical_json",
]
