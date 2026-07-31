"""Portable 恢复专用的内部墓碑查询登记；不扩展公开 EvidenceStore 契约。"""
from __future__ import annotations

import sqlite3
from weakref import WeakKeyDictionary

_connections: WeakKeyDictionary[object, sqlite3.Connection] = WeakKeyDictionary()


def register_evidence_tombstone_reader(store: object, db: sqlite3.Connection) -> None:
    _connections[store] = db


def is_evidence_tombstoned(store: object, evidence_id: str) -> bool:
    db = _connections.get(store)
    if db is None:
        return False
    return db.execute(
        "SELECT 1 FROM evidence WHERE id = ? AND deleted_at IS NOT NULL",
        (evidence_id,),
    ).fetchone() is not None
