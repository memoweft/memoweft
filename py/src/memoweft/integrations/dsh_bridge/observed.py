"""Typed, model-free observed Evidence revisions, permissions and true withdrawal.

Source identities are scoped to the initialized subject and host. Exact facts
become observed state Cognitions; this projection makes no inference/model call.
Only hashes, versions and IDs survive withdrawal. Newer lawful versions may be
accepted; old events and Portable backups remain suppressed by Trust tombstones.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Mapping, NotRequired, TypedDict, cast

from ...clock import Clock, system_clock, to_iso_z
from ...store import open_db
from ..trust.model import CommandEnvelopeV1, PermissionsV1
from ..trust.revision import advance_world_revision, current_world_revision
from ..trust.true_delete import delete_evidence


class ObservedEvidenceV1(TypedDict):
    source_key: str
    version: str
    content: str
    occurred_at: str
    valid_at: str
    valid_until: NotRequired[str | None]
    permissions: PermissionsV1


class ObservedError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _keys(value: Mapping[str, object], allowed: set[str], required: set[str]) -> None:
    if set(value) - allowed or required - set(value):
        raise ObservedError("invalid_observed_parameter")


def instant(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z", value
    ):
        raise ObservedError("invalid_observed_time")
    try:
        return to_iso_z(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise ObservedError("invalid_observed_time") from exc


def permissions(value: object) -> PermissionsV1:
    fields = {"allow_local_read", "allow_cloud_read", "allow_inference"}
    if not isinstance(value, Mapping) or set(value) != fields or any(
        not isinstance(item, bool) for item in value.values()
    ):
        raise ObservedError("invalid_observed_permissions")
    return cast(PermissionsV1, dict(value))


def source_hash(subject_id: str, host_id: str, key: object) -> str:
    if not isinstance(key, str) or not key.strip() or key != key.strip() or len(key) > 512:
        raise ObservedError("invalid_observed_source_key")
    return sha256(json.dumps([subject_id, host_id, key], ensure_ascii=False,
                             separators=(",", ":")).encode()).hexdigest()


class ObservedService:
    def __init__(self, db_path: Path | str, *, subject_id: str, host_id: str,
                 clock: Clock = system_clock) -> None:
        self.path = db_path
        self.subject = subject_id
        self.host = host_id
        self.clock = clock

    def execute(self, operation: str, params: Mapping[str, object]) -> dict[str, object]:
        content: str | None = None
        valid_at = valid_until = occurred_at = payload_hash = None
        perms: PermissionsV1 | None = None
        if operation == "upsert_observed":
            _keys(params, {"evidence"}, {"evidence"})
            value = params["evidence"]
            if not isinstance(value, Mapping):
                raise ObservedError("invalid_observed_parameter")
            required = {"source_key", "version", "content", "occurred_at", "valid_at", "permissions"}
            _keys(value, required | {"valid_until"}, required)
            key, version = value["source_key"], instant(value["version"])
            content = value["content"]
            if not isinstance(content, str) or len(content.encode()) > 65536:
                raise ObservedError("invalid_observed_content")
            occurred_at, valid_at = instant(value["occurred_at"]), instant(value["valid_at"])
            valid_until = None if value.get("valid_until") is None else instant(value["valid_until"])
            if valid_until is not None and valid_until <= valid_at:
                raise ObservedError("invalid_observed_time_range")
            perms = permissions(value["permissions"])
            payload_hash = sha256(json.dumps([content, occurred_at, valid_at, valid_until],
                ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        elif operation == "update_observed_permissions":
            _keys(params, {"source_key", "permission_version", "permissions"},
                  {"source_key", "permission_version", "permissions"})
            key, version = params["source_key"], instant(params["permission_version"])
            perms = permissions(params["permissions"])
        elif operation == "retract_observed":
            _keys(params, {"source_key", "withdrawn_through"}, {"source_key", "withdrawn_through"})
            key, version = params["source_key"], instant(params["withdrawn_through"])
        else:
            raise ObservedError("invalid_observed_operation")
        identity = source_hash(self.subject, self.host, key)
        db = open_db(str(self.path))
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA secure_delete = ON")
            db.execute("BEGIN IMMEDIATE")
            before = current_world_revision(db)
            row = db.execute("SELECT * FROM observed_source WHERE subject_id=? AND host_id=? AND source_hash=?",
                             (self.subject, self.host, identity)).fetchone()
            evidence_id = None if row is None else row["evidence_id"]
            changed = False
            if operation == "upsert_observed":
                assert content is not None and payload_hash is not None and perms is not None
                if row is not None:
                    if row["withdrawn_through"] is not None and version <= row["withdrawn_through"]:
                        raise ObservedError("observed_source_withdrawn")
                    if version < row["version"]:
                        raise ObservedError("stale_observed_version")
                    if version == row["version"] and payload_hash != row["payload_hash"]:
                        raise ObservedError("observed_version_conflict")
                    if version < row["permission_version"] and evidence_id:
                        prior = db.execute("SELECT allow_local_read,allow_cloud_read,allow_inference FROM evidence WHERE id=?", (evidence_id,)).fetchone()
                        assert prior is not None
                        perms = {"allow_local_read": bool(prior[0]), "allow_cloud_read": bool(prior[1]), "allow_inference": bool(prior[2])}
                    if evidence_id and payload_hash != row["payload_hash"]:
                        self._delete(db, str(evidence_id))
                        evidence_id = None
                if evidence_id is None:
                    # Version binds the origin so a newer source can follow a true delete.
                    evidence_id = "observed:" + sha256(f"{identity}:{version}:{payload_hash}".encode()).hexdigest()
                    if db.execute("SELECT 1 FROM evidence WHERE id=?", (evidence_id,)).fetchone():
                        raise ObservedError("observed_source_withdrawn")
                    now = to_iso_z(self.clock())
                    db.execute("INSERT INTO evidence (id,subject_id,source_kind,host_id,origin_id,occurred_at,"
                        "recorded_at,raw_content,summary,allow_local_read,allow_cloud_read,allow_inference) "
                        "VALUES (?,?,'observed',?,?,?,?,?,?,?,?,?)", (evidence_id, self.subject, self.host,
                        evidence_id, occurred_at, now, content, content, int(perms["allow_local_read"]),
                        int(perms["allow_cloud_read"]), int(perms["allow_inference"])))
                    if content.strip():
                        db.execute("INSERT INTO cognition (id,subject_id,content,content_type,formed_by,"
                            "confidence,cred_status,valid_at,created_at,updated_at) "
                            "VALUES (?,?,?,'state','observed',500,'limited',?,?,?)",
                            ("state:" + evidence_id, self.subject, content, valid_at, now, now))
                        db.execute("INSERT INTO cognition_evidence VALUES (?,?,'support')",
                                   ("state:" + evidence_id, evidence_id))
                    changed = True
                if row is None:
                    db.execute("INSERT INTO observed_source VALUES (?,?,?,?,?,?,?,NULL,?,?)",
                        (self.subject,self.host,identity,evidence_id,version,payload_hash,version,valid_at,valid_until))
                else:
                    changed = changed or version != row["version"]
                    db.execute("UPDATE observed_source SET evidence_id=?,version=?,payload_hash=?,valid_at=?,valid_until=? "
                        "WHERE subject_id=? AND host_id=? AND source_hash=?", (evidence_id,version,payload_hash,
                        valid_at,valid_until,self.subject,self.host,identity))
                # An old payload replay can never relax a newer explicit permission decision.
                if row is None or version >= row["permission_version"]:
                    changed = self._permissions(db, identity, str(evidence_id), version, perms) or changed
            elif operation == "update_observed_permissions":
                if evidence_id is None:
                    raise ObservedError("observed_source_not_found")
                assert row is not None and perms is not None
                if version < row["permission_version"]:
                    raise ObservedError("stale_observed_permissions")
                changed = self._permissions(db, identity, str(evidence_id), version, perms)
            else:
                if row is None:
                    db.execute("INSERT INTO observed_source VALUES (?,?,?,NULL,?,'',?,?,?,NULL)",
                        (self.subject,self.host,identity,version,version,version,version))
                    changed = True
                else:
                    # A delayed delete must not remove a newly uploaded version.
                    if version < row["version"]:
                        raise ObservedError("stale_observed_withdrawal")
                    if evidence_id:
                        self._delete(db, str(evidence_id))
                        changed = True
                    if row["withdrawn_through"] is None or version > row["withdrawn_through"]:
                        changed = True
                    db.execute("UPDATE observed_source SET evidence_id=NULL,payload_hash='',withdrawn_through=? "
                        "WHERE subject_id=? AND host_id=? AND source_hash=?",
                        (version,self.subject,self.host,identity))
            after = advance_world_revision(db) if changed else before
            db.execute("COMMIT")
            cleanup: dict[str, str] | None = None
            # Checkpoint every successful replay too: a previous replacement
            # may have committed while a WAL reader delayed storage cleanup.
            cleanup = self._cleanup(db)
            return {"schema_version": 1, "subject_id": self.subject, "source_hash": identity,
                "source_kind": "observed", "evidence_id": evidence_id if operation != "retract_observed" else None,
                "result_state": "applied" if changed else "no_change", "world_revision": after,
                "before_revision": before, "after_revision": after,
                "model_call_count": 0, **({"storage_cleanup": cleanup} if cleanup else {})}
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    def _permissions(self, db: sqlite3.Connection, identity: str, evidence_id: str,
                     version: str, perms: PermissionsV1) -> bool:
        values = tuple(int(perms[field]) for field in ("allow_local_read", "allow_cloud_read", "allow_inference"))
        current = db.execute("SELECT allow_local_read,allow_cloud_read,allow_inference,deleted_at FROM evidence WHERE id=?",
                             (evidence_id,)).fetchone()
        if current is None or current[3] is not None:
            raise ObservedError("observed_source_withdrawn")
        prior = db.execute("SELECT permission_version FROM observed_source WHERE source_hash=? AND subject_id=? AND host_id=?",
                           (identity, self.subject, self.host)).fetchone()
        if prior and version == prior[0] and any(old == 0 and new == 1 for old, new in zip(current[:3], values)):
            raise ObservedError("observed_permission_conflict")
        db.execute("UPDATE evidence SET allow_local_read=?,allow_cloud_read=?,allow_inference=? WHERE id=?", (*values,evidence_id))
        db.execute("UPDATE observed_source SET permission_version=? WHERE source_hash=? AND subject_id=? AND host_id=?",
                   (version,identity,self.subject,self.host))
        return tuple(current[:3]) != values or bool(prior and prior[0] != version)

    def _delete(self, db: sqlite3.Connection, evidence_id: str) -> None:
        command: CommandEnvelopeV1 = {"schema_version": 1, "command_id": "observed-withdrawal", "subject_id": self.subject,
            "actor": self.host, "expected_world_revision": current_world_revision(db), "operation": "delete_evidence",
            "target_kind": "evidence", "target_id": evidence_id, "payload": {}, "submitted_at": to_iso_z(self.clock())}
        result = delete_evidence(db, command, to_iso_z(self.clock()), self.subject)
        if result.result_state not in {"applied", "no_change"}:
            raise ObservedError("observed_withdrawal_failed")

    @staticmethod
    def _cleanup(db: sqlite3.Connection) -> dict[str, str]:
        try:
            if str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
                db.execute("PRAGMA busy_timeout=0")
                checkpoint = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint is None or checkpoint[0] != 0:
                    return {"state": "pending", "detail_code": "wal_reader_busy"}
            return {"state": "complete", "detail_code": "current_storage_committed"}
        except sqlite3.Error:
            return {"state": "pending", "detail_code": "checkpoint_status_unconfirmed"}
