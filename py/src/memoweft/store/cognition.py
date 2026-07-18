"""与 TypeScript cognition store 契约一致的 cognition 与 cognition_evidence 存储层。

判断层:content/formed_by/confidence/cred_status/时态位。授权位在 evidence 层,不在这里。
"""
from __future__ import annotations

import sqlite3
import uuid
from typing import Optional

from ..clock import Clock, system_clock, to_iso_z
from ..types import Cognition, CognitionInput, CognitionPatch, EvidenceLink, _Unset
from ._rows import row_all, row_one


def _from_row(r: sqlite3.Row) -> Cognition:
    return Cognition(
        id=r["id"],
        subject_id=r["subject_id"],
        content=r["content"],
        content_type=r["content_type"],
        formed_by=r["formed_by"],
        confidence=r["confidence"],
        cred_status=r["cred_status"],
        scope=r["scope"],
        valid_at=r["valid_at"],
        invalid_at=r["invalid_at"],
        asked_at=r["asked_at"],
        archived_at=r["archived_at"],
        muted_at=r["muted_at"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


class SqliteCognitionStore:
    """使用 open_db 共享连接的认知存储。"""

    def __init__(self, db: sqlite3.Connection, clock: Clock = system_clock) -> None:
        self._db = db
        self._clock = clock

    def put(self, inp: CognitionInput) -> Cognition:
        now = to_iso_z(self._clock())
        cog = Cognition(
            id=str(uuid.uuid4()),
            subject_id=inp.subject_id,
            content=inp.content,
            content_type=inp.content_type,
            formed_by=inp.formed_by,
            confidence=inp.confidence,
            cred_status=inp.cred_status,
            scope=inp.scope,
            valid_at=inp.valid_at,
            invalid_at=inp.invalid_at,
            asked_at=None,  # 新建一律未问过(提问后由 proposeAsk 经 update 写)
            archived_at=None,  # 新建一律未归档
            muted_at=None,  # 新建一律未静音()
            created_at=now,
            updated_at=now,
        )
        self._insert_row(cog)
        for link in inp.evidence or []:
            self._db.execute(
                "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) VALUES (?,?,?)",
                (cog.id, link.evidence_id, link.relation),
            )
        return cog

    def _insert_row(self, cog: Cognition) -> None:
        self._db.execute(
            """INSERT INTO cognition (
              id, subject_id, content, content_type, formed_by,
              confidence, cred_status, scope, valid_at, invalid_at,
              asked_at, archived_at, muted_at, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cog.id, cog.subject_id, cog.content, cog.content_type, cog.formed_by,
                cog.confidence, cog.cred_status, cog.scope, cog.valid_at, cog.invalid_at,
                cog.asked_at, cog.archived_at, cog.muted_at, cog.created_at, cog.updated_at,
            ),
        )

    def get(self, id: str) -> Optional[Cognition]:
        r = row_one(self._db, "SELECT * FROM cognition WHERE id = ?", (id,))
        return _from_row(r) if r is not None else None

    def all(self, subject_id: Optional[str] = None) -> list[Cognition]:
        if subject_id is not None:
            rows = row_all(
                self._db,
                "SELECT * FROM cognition WHERE subject_id = ? ORDER BY confidence DESC, created_at ASC",
                (subject_id,),
            )
        else:
            rows = row_all(self._db, "SELECT * FROM cognition ORDER BY confidence DESC, created_at ASC")
        return [_from_row(r) for r in rows]

    def active(self, subject_id: str) -> list[Cognition]:
        # active = 未失效【且未归档】( 归档全面雪藏)。排序同 all。
        rows = row_all(
            self._db,
            "SELECT * FROM cognition WHERE subject_id = ? AND invalid_at IS NULL AND archived_at IS NULL "
            "ORDER BY confidence DESC, created_at ASC",
            (subject_id,),
        )
        return [_from_row(r) for r in rows]

    def sources_of(self, cognition_id: str) -> list[EvidenceLink]:
        rows = row_all(
            self._db,
            "SELECT evidence_id, relation FROM cognition_evidence WHERE cognition_id = ?",
            (cognition_id,),
        )
        return [EvidenceLink(evidence_id=r["evidence_id"], relation=r["relation"]) for r in rows]

    def update(self, id: str, patch: CognitionPatch) -> Optional[Cognition]:
        cur = self.get(id)
        if cur is None:
            return None
        # 两组语义:content/confidence/cred_status/formed_by 用 `?? cur`(None=保留);
        #   scope/invalid_at/asked_at/archived_at/muted_at 用 `=== undefined`(UNSET=保留、None=复位)。
        content = patch.content if patch.content is not None else cur.content
        confidence = patch.confidence if patch.confidence is not None else cur.confidence
        cred_status = patch.cred_status if patch.cred_status is not None else cur.cred_status
        formed_by = patch.formed_by if patch.formed_by is not None else cur.formed_by
        scope = cur.scope if isinstance(patch.scope, _Unset) else patch.scope
        invalid_at = cur.invalid_at if isinstance(patch.invalid_at, _Unset) else patch.invalid_at
        asked_at = cur.asked_at if isinstance(patch.asked_at, _Unset) else patch.asked_at
        archived_at = cur.archived_at if isinstance(patch.archived_at, _Unset) else patch.archived_at
        muted_at = cur.muted_at if isinstance(patch.muted_at, _Unset) else patch.muted_at
        updated_at = to_iso_z(self._clock())
        self._db.execute(
            "UPDATE cognition SET content=?, confidence=?, cred_status=?, formed_by=?, scope=?, "
            "invalid_at=?, asked_at=?, archived_at=?, muted_at=?, updated_at=? WHERE id=?",
            (content, confidence, cred_status, formed_by, scope, invalid_at, asked_at, archived_at, muted_at, updated_at, id),
        )
        return self.get(id)

    def add_evidence(self, cognition_id: str, links: list[EvidenceLink]) -> None:
        for link in links:
            self._db.execute(
                "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) VALUES (?,?,?)",
                (cognition_id, link.evidence_id, link.relation),
            )

    def insert(self, cognition: Cognition, sources: list[EvidenceLink]) -> None:
        self._insert_row(cognition)
        for link in sources:
            self._db.execute(
                "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) VALUES (?,?,?)",
                (cognition.id, link.evidence_id, link.relation),
            )

    def remove(self, id: str) -> bool:
        self._db.execute("DELETE FROM cognition_evidence WHERE cognition_id = ?", (id,))
        cur = self._db.cursor()
        cur.execute("DELETE FROM cognition WHERE id = ?", (id,))
        return cur.rowcount > 0

    def remove_by_subject(self, subject_id: str) -> int:
        self._db.execute(
            "DELETE FROM cognition_evidence WHERE cognition_id IN (SELECT id FROM cognition WHERE subject_id = ?)",
            (subject_id,),
        )
        cur = self._db.cursor()
        cur.execute("DELETE FROM cognition WHERE subject_id = ?", (subject_id,))
        return cur.rowcount
