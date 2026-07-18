"""与 TypeScript event store 契约一致的 event 与 event_evidence 存储。"""
from __future__ import annotations

import sqlite3
import uuid
from typing import Optional

from ..clock import Clock, system_clock, to_iso_z
from ..types import Event, EventInput
from ._rows import row_all, row_one


def _from_row(r: sqlite3.Row) -> Event:
    return Event(
        id=r["id"],
        subject_id=r["subject_id"],
        summary=r["summary"],
        occurred_at=r["occurred_at"],
        created_at=r["created_at"],
    )


class SqliteEventStore:
    """使用 open_db 共享连接的事件存储。"""

    def __init__(self, db: sqlite3.Connection, clock: Clock = system_clock) -> None:
        self._db = db
        self._clock = clock

    def put(self, inp: EventInput) -> Event:
        now = to_iso_z(self._clock())
        ev = Event(
            id=str(uuid.uuid4()),
            subject_id=inp.subject_id,
            summary=inp.summary,
            occurred_at=inp.occurred_at,
            created_at=now,
        )
        self._db.execute(
            "INSERT INTO event (id, subject_id, summary, occurred_at, created_at) VALUES (?,?,?,?,?)",
            (ev.id, ev.subject_id, ev.summary, ev.occurred_at, ev.created_at),
        )
        for eid in inp.evidence_ids:
            self._db.execute("INSERT INTO event_evidence (event_id, evidence_id) VALUES (?,?)", (ev.id, eid))
        return ev

    def get(self, id: str) -> Optional[Event]:
        r = row_one(self._db, "SELECT * FROM event WHERE id = ?", (id,))
        return _from_row(r) if r is not None else None

    def all(self, subject_id: Optional[str] = None) -> list[Event]:
        if subject_id is not None:
            rows = row_all(self._db, "SELECT * FROM event WHERE subject_id = ? ORDER BY occurred_at ASC", (subject_id,))
        else:
            rows = row_all(self._db, "SELECT * FROM event ORDER BY occurred_at ASC")
        return [_from_row(r) for r in rows]

    def evidence_of(self, event_id: str) -> list[str]:
        rows = row_all(self._db, "SELECT evidence_id FROM event_evidence WHERE event_id = ?", (event_id,))
        return [r["evidence_id"] for r in rows]

    def covered_evidence_ids(self, subject_id: str) -> list[str]:
        # 子查询使用 IN；去重由调用方提供的 set 保证，无需 DISTINCT。
        rows = row_all(
            self._db,
            "SELECT evidence_id FROM event_evidence WHERE event_id IN (SELECT id FROM event WHERE subject_id = ?)",
            (subject_id,),
        )
        return [r["evidence_id"] for r in rows]

    def unconsolidated(self, subject_id: str) -> list[Event]:
        rows = row_all(
            self._db,
            "SELECT * FROM event WHERE subject_id = ? AND consolidated = 0 ORDER BY occurred_at ASC",
            (subject_id,),
        )
        return [_from_row(r) for r in rows]

    def mark_consolidated(self, ids: list[str]) -> None:
        if not ids:
            return
        for id in ids:
            self._db.execute("UPDATE event SET consolidated = 1 WHERE id = ?", (id,))

    def insert(self, event: Event, evidence_ids: list[str], *, consolidated: bool = False) -> None:
        self._db.execute(
            "INSERT INTO event (id, subject_id, summary, occurred_at, created_at, consolidated) VALUES (?,?,?,?,?,?)",
            (event.id, event.subject_id, event.summary, event.occurred_at, event.created_at, 1 if consolidated else 0),
        )
        for eid in evidence_ids:
            self._db.execute("INSERT INTO event_evidence (event_id, evidence_id) VALUES (?,?)", (event.id, eid))

    def remove(self, id: str) -> bool:
        self._db.execute("DELETE FROM event_evidence WHERE event_id = ?", (id,))
        cur = self._db.cursor()
        cur.execute("DELETE FROM event WHERE id = ?", (id,))
        return cur.rowcount > 0

    def remove_by_subject(self, subject_id: str) -> int:
        self._db.execute(
            "DELETE FROM event_evidence WHERE event_id IN (SELECT id FROM event WHERE subject_id = ?)",
            (subject_id,),
        )
        cur = self._db.cursor()
        cur.execute("DELETE FROM event WHERE subject_id = ?", (subject_id,))
        return cur.rowcount
