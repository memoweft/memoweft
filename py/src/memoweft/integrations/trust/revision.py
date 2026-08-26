"""One coherent, query-only SQLite revision snapshot per Trust operation."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from .model import TrustQueryError


@dataclass(frozen=True)
class CoherentRevisionRead:
    db: sqlite3.Connection
    world_revision: int


def current_world_revision(db: sqlite3.Connection) -> int:
    row = db.execute(
        "SELECT revision FROM memory_state WHERE singleton = 1"
    ).fetchone()
    return 0 if row is None else int(row[0])


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def advance_world_revision(db: sqlite3.Connection) -> int:
    """Advance one mutation revision and rebuild the canonical World snapshot.

    The caller owns the write transaction. Trust permission/tombstone changes
    may leave the World rows unchanged, but still advance the revision so
    Recall/query tokens cannot reuse a pre-command currentness view.
    """

    revision = current_world_revision(db) + 1
    cognition_rows = db.execute(
        "SELECT c.id, c.content, c.content_type, c.confidence, c.cred_status, "
        "t.target_entity_id, t.perspective_entity_id FROM cognition c "
        "LEFT JOIN cognition_target t ON t.cognition_id = c.id "
        "WHERE c.archived_at IS NULL AND c.muted_at IS NULL "
        "AND c.invalid_at IS NULL AND NOT EXISTS ("
        "SELECT 1 FROM world_item_lifecycle l WHERE l.subject_id = c.subject_id "
        "AND l.object_kind = 'cognition' AND l.item_id = c.id "
        "AND (l.archived_at IS NOT NULL OR l.muted_at IS NOT NULL)) "
        "ORDER BY c.created_at, c.id"
    ).fetchall()
    cognitions = [
        {
            "id": str(row[0]),
            "content": str(row[1]),
            "content_type": str(row[2]),
            "confidence": int(row[3]),
            "cred_status": str(row[4]),
            "target_entity_id": None if row[5] is None else str(row[5]),
            "perspective_entity_id": None if row[6] is None else str(row[6]),
        }
        for row in cognition_rows
    ]
    entity_rows = db.execute(
        "SELECT e.id, e.canonical_name, e.kind FROM entity e "
        "WHERE e.invalid_at IS NULL AND NOT EXISTS ("
        "SELECT 1 FROM world_item_lifecycle l WHERE l.subject_id = e.world_id "
        "AND l.object_kind = 'entity' AND l.item_id = e.id "
        "AND (l.archived_at IS NOT NULL OR l.muted_at IS NOT NULL)) "
        "ORDER BY e.created_at, e.id"
    ).fetchall()
    entities = [
        {"id": str(row[0]), "canonical_name": str(row[1]), "kind": str(row[2])}
        for row in entity_rows
    ]
    relationship_rows = db.execute(
        "SELECT r.id, r.content, r.relation_type, r.confidence, r.cred_status "
        "FROM relationship r WHERE r.invalid_at IS NULL AND NOT EXISTS ("
        "SELECT 1 FROM world_item_lifecycle l WHERE l.subject_id = r.world_id "
        "AND l.object_kind = 'relationship' AND l.item_id = r.id "
        "AND (l.archived_at IS NOT NULL OR l.muted_at IS NOT NULL)) "
        "ORDER BY r.created_at, r.id"
    ).fetchall()
    relationships = [
        {
            "id": str(row[0]),
            "content": str(row[1]),
            "relation_type": str(row[2]),
            "confidence": int(row[3]),
            "cred_status": str(row[4]),
        }
        for row in relationship_rows
    ]
    event_rows = db.execute(
        "SELECT w.id, w.content, w.occurred_at, w.time_expression, "
        "w.participants_json, w.objects_json, w.confidence, w.cred_status "
        "FROM world_event w WHERE w.invalid_at IS NULL AND NOT EXISTS ("
        "SELECT 1 FROM world_item_lifecycle l WHERE l.subject_id = w.world_id "
        "AND l.object_kind = 'event' AND l.item_id = w.id "
        "AND (l.archived_at IS NOT NULL OR l.muted_at IS NOT NULL)) "
        "ORDER BY w.created_at, w.id"
    ).fetchall()
    events = [
        {
            "id": str(row[0]),
            "content": str(row[1]),
            "occurred_at": None if row[2] is None else str(row[2]),
            "time_expression": None if row[3] is None else str(row[3]),
            "participants": json.loads(str(row[4]) or "[]"),
            "objects": json.loads(str(row[5]) or "[]"),
            "confidence": int(row[6]),
            "cred_status": str(row[7]),
        }
        for row in event_rows
    ]
    snapshot_json = _canonical(
        {
            "schema_version": 5,
            "revision": revision,
            "cognitions": cognitions,
            "entities": entities,
            "relationships": relationships,
            "events": events,
        }
    )
    snapshot_hash = sha256(snapshot_json.encode("utf-8")).hexdigest()
    if current_world_revision(db) == 0 and db.execute(
        "SELECT 1 FROM memory_state WHERE singleton = 1"
    ).fetchone() is None:
        db.execute(
            "INSERT INTO memory_state (singleton, revision, snapshot_json, "
            "snapshot_hash) VALUES (1, ?, ?, ?)",
            (revision, snapshot_json, snapshot_hash),
        )
    else:
        db.execute(
            "UPDATE memory_state SET revision = ?, snapshot_json = ?, "
            "snapshot_hash = ? WHERE singleton = 1",
            (revision, snapshot_json, snapshot_hash),
        )
    return revision


@contextmanager
def coherent_revision_read(db_path: Path | str) -> Iterator[CoherentRevisionRead]:
    """Open one immutable SQLite view and roll it back on every exit path."""

    path = Path(db_path).resolve()
    try:
        db = sqlite3.connect(
            path.as_uri() + "?mode=ro", uri=True, isolation_level=None
        )
    except (OSError, sqlite3.Error) as exc:
        raise TrustQueryError("trust_database_unavailable") from exc
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only = ON")
        db.execute("BEGIN")
        row = db.execute(
            "SELECT revision FROM memory_state WHERE singleton = 1"
        ).fetchone()
        revision = 0 if row is None else int(row[0])
        if revision < 0:
            raise TrustQueryError("invalid_world_revision")
        yield CoherentRevisionRead(db=db, world_revision=revision)
    except TrustQueryError:
        raise
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise TrustQueryError("trust_query_failed") from exc
    finally:
        if db.in_transaction:
            try:
                db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        db.close()
