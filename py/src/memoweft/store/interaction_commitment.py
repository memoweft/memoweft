"""Interaction commitment store: tracks AI self-commitments, recommendations, and agreements.

Zero GPU, deterministic distillation on committed boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
import uuid
from typing import Literal, Optional, Sequence

from ..clock import Clock, system_clock, to_iso_z
from ._rows import row_all, row_one

CommitmentKind = Literal["recommendation", "commitment", "agreement"]
CommitmentStatus = Literal["active", "fulfilled", "superseded", "retracted"]

SCHEMA_SQL = (
    """CREATE TABLE IF NOT EXISTS interaction_commitment (
        id              TEXT PRIMARY KEY,
        subject_id      TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        episode_id      TEXT NOT NULL,
        assistant_message_id TEXT,
        kind            TEXT NOT NULL CHECK(kind IN ('recommendation', 'commitment', 'agreement')),
        content         TEXT NOT NULL,
        raw_quote       TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'fulfilled', 'superseded', 'retracted')),
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS ix_interaction_commitment_lookup
        ON interaction_commitment(subject_id, kind, status)""",
    """CREATE TABLE IF NOT EXISTS relationship_transitions (
        id                          TEXT PRIMARY KEY,
        prior_relationship_id       TEXT NOT NULL UNIQUE,
        replacement_relationship_id TEXT NOT NULL,
        reason                      TEXT NOT NULL,
        revision                    INTEGER NOT NULL
    )""",
)


@dataclass(frozen=True, slots=True)
class InteractionCommitment:
    id: str
    subject_id: str
    conversation_id: str
    episode_id: str
    assistant_message_id: Optional[str]
    kind: CommitmentKind
    content: str
    raw_quote: str
    status: CommitmentStatus
    created_at: str
    updated_at: str


class SqliteInteractionCommitmentStore:
    def __init__(self, db: sqlite3.Connection, clock: Clock = system_clock) -> None:
        self._db = db
        self._clock = clock
        self.ensure_schema()

    def ensure_schema(self) -> None:
        try:
            for stmt in SCHEMA_SQL:
                self._db.execute(stmt)
            columns = {
                str(row[1])
                for row in self._db.execute(
                    "PRAGMA table_info(interaction_commitment)"
                ).fetchall()
            }
            if "assistant_message_id" not in columns:
                self._db.execute(
                    "ALTER TABLE interaction_commitment "
                    "ADD COLUMN assistant_message_id TEXT"
                )
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "readonly" in msg or "read-only" in msg:
                return
            raise

    def record(
        self,
        *,
        subject_id: str,
        conversation_id: str,
        episode_id: str,
        kind: CommitmentKind,
        content: str,
        raw_quote: str,
        assistant_message_id: Optional[str] = None,
        status: CommitmentStatus = "active",
    ) -> InteractionCommitment:
        self.ensure_schema()
        existing = row_one(
            self._db,
            "SELECT * FROM interaction_commitment WHERE subject_id = ? "
            "AND conversation_id = ? AND episode_id = ? "
            "AND assistant_message_id IS ? AND content = ? AND status = ?",
            (
                subject_id,
                conversation_id,
                episode_id,
                assistant_message_id,
                content,
                status,
            ),
        )
        if existing is not None:
            return InteractionCommitment(
                id=str(existing["id"]),
                subject_id=str(existing["subject_id"]),
                conversation_id=str(existing["conversation_id"]),
                episode_id=str(existing["episode_id"]),
                assistant_message_id=(
                    None
                    if "assistant_message_id" not in existing.keys()
                    or existing["assistant_message_id"] is None
                    else str(existing["assistant_message_id"])
                ),
                kind=existing["kind"],
                content=str(existing["content"]),
                raw_quote=str(existing["raw_quote"]),
                status=existing["status"],
                created_at=str(existing["created_at"]),
                updated_at=str(existing["updated_at"]),
            )
        now_text = to_iso_z(self._clock())
        item_id = str(uuid.uuid4())
        self._db.execute(
            "INSERT INTO interaction_commitment (id, subject_id, conversation_id, "
            "episode_id, assistant_message_id, kind, content, raw_quote, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id,
                subject_id,
                conversation_id,
                episode_id,
                assistant_message_id,
                kind,
                content,
                raw_quote,
                status,
                now_text,
                now_text,
            ),
        )
        return InteractionCommitment(
            id=item_id,
            subject_id=subject_id,
            conversation_id=conversation_id,
            episode_id=episode_id,
            assistant_message_id=assistant_message_id,
            kind=kind,
            content=content,
            raw_quote=raw_quote,
            status=status,
            created_at=now_text,
            updated_at=now_text,
        )

    def query(
        self,
        subject_id: str,
        *,
        kind: Optional[CommitmentKind] = None,
        status: CommitmentStatus = "active",
        conversation_id: Optional[str] = None,
    ) -> list[InteractionCommitment]:
        query = "SELECT * FROM interaction_commitment WHERE subject_id = ? AND status = ?"
        params: list[object] = [subject_id, status]
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        if conversation_id is not None:
            query += " AND conversation_id = ?"
            params.append(conversation_id)
        query += " ORDER BY created_at DESC"
        try:
            rows = row_all(self._db, query, tuple(params))
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return []
            raise
        return [
            InteractionCommitment(
                id=str(r["id"]),
                subject_id=str(r["subject_id"]),
                conversation_id=str(r["conversation_id"]),
                episode_id=str(r["episode_id"]),
                assistant_message_id=(
                    None
                    if "assistant_message_id" not in r.keys()
                    or r["assistant_message_id"] is None
                    else str(r["assistant_message_id"])
                ),
                kind=r["kind"],
                content=str(r["content"]),
                raw_quote=str(r["raw_quote"]),
                status=r["status"],
                created_at=str(r["created_at"]),
                updated_at=str(r["updated_at"]),
            )
            for r in rows
        ]
