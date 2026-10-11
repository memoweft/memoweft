"""Optional, idempotent schema extension; PRAGMA user_version is unchanged.

Triggers erase derived bytes in the SAME transaction as the authoritative
mutation. The durable work queue contains identifiers only. Encoding is outside
write transactions; publication rechecks content and eligibility under a lock.
"""
from __future__ import annotations

from hashlib import sha256
import sqlite3
from typing import Any, cast

from ..clock import system_clock, to_iso_z
from ..integrations.trust.currentness import world_item_visible
from .model import DIMENSIONS, MODEL_ID

TABLES = {"cognition": ("cognition", "subject_id", "cognition_evidence", "cognition_id"),
          "relationship": ("relationship", "world_id", "relationship_evidence", "relationship_id"),
          "event": ("world_event", "world_id", "world_event_evidence", "world_event_id")}


def content_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def install(db: sqlite3.Connection) -> None:
    """Caller owns transaction. Safe on an existing v21 database and reentry."""
    db.execute("CREATE TABLE IF NOT EXISTS semantic_vector (subject_id TEXT NOT NULL, kind TEXT NOT NULL, item_id TEXT NOT NULL, content_hash TEXT NOT NULL, model_id TEXT NOT NULL, dimensions INTEGER NOT NULL, vector BLOB NOT NULL, expires_at TEXT, PRIMARY KEY(subject_id,kind,item_id))")
    db.execute("CREATE TABLE IF NOT EXISTS semantic_pending (kind TEXT NOT NULL, item_id TEXT NOT NULL, PRIMARY KEY(kind,item_id))")
    db.execute("CREATE INDEX IF NOT EXISTS semantic_vector_expiry ON semantic_vector(expires_at) WHERE expires_at IS NOT NULL")
    # World item IDs are unique within each kind. A UNIQUE index also keeps
    # SQLite from choosing the model-wide index for one-item queue checks.
    for index in db.execute("PRAGMA index_list(semantic_vector)").fetchall():
        if index[1] == "semantic_vector_item" and not index[2]:
            db.execute("DROP INDEX semantic_vector_item")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS semantic_vector_item ON semantic_vector(kind,item_id)")
    db.execute("CREATE INDEX IF NOT EXISTS semantic_vector_model ON semantic_vector(model_id)")
    db.execute("CREATE TABLE IF NOT EXISTS semantic_epoch (singleton INTEGER PRIMARY KEY CHECK(singleton=1), value INTEGER NOT NULL)")
    db.execute("INSERT OR IGNORE INTO semantic_epoch VALUES(1,0)")
    for action in ("INSERT", "UPDATE", "DELETE"):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS semantic_epoch_{action.lower()} AFTER {action} ON semantic_vector BEGIN UPDATE semantic_epoch SET value=value+1 WHERE singleton=1; END")
    for kind, (table, _, link, column) in TABLES.items():
        for action in ("INSERT", "UPDATE", "DELETE"):
            ref = "OLD" if action == "DELETE" else "NEW"
            queue = (f"DELETE FROM semantic_pending WHERE kind='{kind}' AND item_id=OLD.id;" if action == "DELETE" else
                     f"INSERT OR IGNORE INTO semantic_pending VALUES('{kind}',{ref}.id);")
            db.execute(f"CREATE TRIGGER IF NOT EXISTS semantic_{kind}_{action.lower()} AFTER {action} ON {table} BEGIN DELETE FROM semantic_vector WHERE kind='{kind}' AND item_id={ref}.id; {queue} END")
        for action in ("INSERT", "UPDATE", "DELETE"):
            refs = ("OLD", "NEW") if action == "UPDATE" else ("OLD",) if action == "DELETE" else ("NEW",)
            statements = " ".join(f"DELETE FROM semantic_vector WHERE kind='{kind}' AND item_id={ref}.{column}; INSERT OR IGNORE INTO semantic_pending VALUES('{kind}',{ref}.{column});" for ref in refs)
            db.execute(f"CREATE TRIGGER IF NOT EXISTS semantic_{kind}_source_{action.lower()} AFTER {action} ON {link} BEGIN {statements} END")
    for action in ("UPDATE", "DELETE"):
        statements = " ".join(f"DELETE FROM semantic_vector WHERE kind='{kind}' AND item_id IN (SELECT {column} FROM {link} WHERE evidence_id=OLD.id); INSERT OR IGNORE INTO semantic_pending SELECT '{kind}',{column} FROM {link} WHERE evidence_id=OLD.id;" for kind, (_, _, link, column) in TABLES.items())
        db.execute(f"CREATE TRIGGER IF NOT EXISTS semantic_evidence_{action.lower()} BEFORE {action} ON evidence BEGIN {statements} END")
    for action in ("INSERT", "UPDATE", "DELETE"):
        refs = ("OLD", "NEW") if action == "UPDATE" else ("OLD",) if action == "DELETE" else ("NEW",)
        statements = " ".join(f"DELETE FROM semantic_vector WHERE kind='{kind}' AND item_id IN (SELECT {column} FROM {link} WHERE evidence_id={ref}.evidence_id); INSERT OR IGNORE INTO semantic_pending SELECT '{kind}',{column} FROM {link} WHERE evidence_id={ref}.evidence_id;" for ref in refs for kind, (_, _, link, column) in TABLES.items())
        db.execute(f"CREATE TRIGGER IF NOT EXISTS semantic_observed_{action.lower()} BEFORE {action} ON observed_source BEGIN {statements} END")
    for action in ("INSERT", "UPDATE", "DELETE"):
        ref = "OLD" if action == "DELETE" else "NEW"
        db.execute(f"CREATE TRIGGER IF NOT EXISTS semantic_lifecycle_{action.lower()} AFTER {action} ON world_item_lifecycle BEGIN DELETE FROM semantic_vector WHERE subject_id={ref}.subject_id AND kind={ref}.object_kind AND item_id={ref}.item_id; INSERT OR IGNORE INTO semantic_pending VALUES({ref}.object_kind,{ref}.item_id); END")
    for kind in ("cognition", "relationship"):
        table = kind + "_transitions"
        if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            db.execute(f"CREATE INDEX IF NOT EXISTS semantic_{kind}_successor ON {table}(replacement_{kind}_id)")
            db.execute(f"CREATE INDEX IF NOT EXISTS semantic_{kind}_prior ON {table}(prior_{kind}_id)")
            for action in ("INSERT", "UPDATE"):
                db.execute(f"CREATE TRIGGER IF NOT EXISTS semantic_{kind}_corrected_{action.lower()} AFTER {action} ON {table} BEGIN DELETE FROM semantic_vector WHERE kind='{kind}' AND item_id=NEW.prior_{kind}_id; DELETE FROM semantic_pending WHERE kind='{kind}' AND item_id=NEW.prior_{kind}_id; INSERT OR IGNORE INTO semantic_pending VALUES('{kind}',NEW.replacement_{kind}_id); END")
    for action in ("INSERT", "UPDATE"):
        statements = " ".join(f"DELETE FROM semantic_vector WHERE kind='{kind}' AND item_id=NEW.prior_{kind}_id; DELETE FROM semantic_pending WHERE kind='{kind}' AND item_id=NEW.prior_{kind}_id;" for kind in TABLES)
        db.execute(f"CREATE TRIGGER IF NOT EXISTS semantic_retraction_{action.lower()} AFTER {action} ON retraction BEGIN {statements} END")


def eligible_record(db: sqlite3.Connection, kind: str, item_id: str) -> tuple[str, str, str | None] | None:
    if kind not in TABLES:
        return None
    table, owner, link, column = TABLES[kind]
    row = db.execute(f"SELECT {owner},content FROM {table} WHERE id=?", (item_id,)).fetchone()
    if row is None or not world_item_visible(db, str(row[0]), cast(Any, kind), item_id, surface="recall", model_tier="local"):
        return None
    if kind in {"cognition", "relationship"} and db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (kind + "_transitions",)).fetchone():
        if db.execute(f"SELECT 1 FROM {kind}_transitions WHERE prior_{kind}_id=? LIMIT 1", (item_id,)).fetchone():
            return None
    if db.execute(f"SELECT 1 FROM retraction WHERE prior_{kind}_id=? LIMIT 1", (item_id,)).fetchone():
        return None
    deadline = db.execute(f"SELECT min(o.valid_until) FROM observed_source o JOIN {link} l ON l.evidence_id=o.evidence_id WHERE l.{column}=?", (item_id,)).fetchone()
    return str(row[0]), str(row[1]), None if deadline is None or deadline[0] is None else str(deadline[0])


def expire(db: sqlite3.Connection) -> int:
    result = db.execute("DELETE FROM semantic_vector WHERE expires_at IS NOT NULL AND expires_at<=?", (to_iso_z(system_clock()),))
    return result.rowcount


def enqueue_backfill(db: sqlite3.Connection, kind: str, after: str, batch: int) -> tuple[str, bool]:
    table, _, _, _ = TABLES[kind]
    rows = db.execute(f"SELECT id FROM {table} WHERE id>? ORDER BY id LIMIT ?", (after, batch)).fetchall()
    for row in rows:
        db.execute("INSERT OR IGNORE INTO semantic_pending SELECT ?,? WHERE NOT EXISTS(SELECT 1 FROM semantic_vector WHERE kind=? AND item_id=? AND model_id=?)", (kind, row[0], kind, row[0], MODEL_ID))
    return (str(rows[-1][0]) if rows else after), len(rows) < batch


def publish(db: sqlite3.Connection, kind: str, item_id: str, expected_hash: str, vector: bytes) -> bool:
    record = eligible_record(db, kind, item_id)
    if record is None or content_hash(record[1]) != expected_hash:
        return False
    if len(vector) != DIMENSIONS * 4:
        raise ValueError("invalid semantic vector dimensions")
    db.execute("INSERT OR REPLACE INTO semantic_vector VALUES(?,?,?,?,?,?,?,?)", (record[0], kind, item_id, expected_hash, MODEL_ID, DIMENSIONS, vector, record[2]))
    return True
