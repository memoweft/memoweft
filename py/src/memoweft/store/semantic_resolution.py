"""语义解析存储（v0.6），与 TypeScript 存储实现保持契约一致。

一条证据一条解析(evidence_id 关联,不冗余存 subject_id)。put 由  resolver 调;
resolved_content 是解释产物，不是 Evidence，永不进入 consolidate 的 support 白名单。
"""
from __future__ import annotations

import sqlite3
import uuid
from typing import Optional

from ..clock import Clock, system_clock, to_iso_z
from ..types import SemanticResolution, SemanticResolutionInput
from ._rows import row_all, row_one


def _from_row(r: sqlite3.Row) -> SemanticResolution:
    return SemanticResolution(
        id=r["id"],
        evidence_id=r["evidence_id"],
        resolved_content=r["resolved_content"],
        response_act=r["response_act"],
        prompt_act=r["prompt_act"],
        proposition_origin=r["proposition_origin"],
        assertion_strength=r["assertion_strength"],
        required_context=r["required_context"],
        resolver_version=r["resolver_version"],
        created_at=r["created_at"],
    )


class SqliteSemanticResolutionStore:
    """使用 open_db 共享连接的语义解析存储。"""

    def __init__(self, db: sqlite3.Connection, clock: Clock = system_clock) -> None:
        self._db = db
        self._clock = clock

    def put(self, inp: SemanticResolutionInput) -> SemanticResolution:
        res = SemanticResolution(
            id=str(uuid.uuid4()),
            evidence_id=inp.evidence_id,
            resolved_content=inp.resolved_content,
            response_act=inp.response_act,
            prompt_act=inp.prompt_act,
            proposition_origin=inp.proposition_origin,
            assertion_strength=inp.assertion_strength,
            required_context=inp.required_context,
            resolver_version=inp.resolver_version,
            created_at=to_iso_z(self._clock()),
        )
        self._insert_row(res)
        return res

    def _insert_row(self, res: SemanticResolution) -> None:
        self._db.execute(
            "INSERT INTO semantic_resolution (id, evidence_id, resolved_content, response_act, prompt_act, "
            "proposition_origin, assertion_strength, required_context, resolver_version, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                res.id, res.evidence_id, res.resolved_content, res.response_act, res.prompt_act,
                res.proposition_origin, res.assertion_strength, res.required_context,
                res.resolver_version, res.created_at,
            ),
        )

    def get(self, id: str) -> Optional[SemanticResolution]:
        r = row_one(self._db, "SELECT * FROM semantic_resolution WHERE id = ?", (id,))
        return _from_row(r) if r is not None else None

    def of_evidence(self, evidence_id: str) -> Optional[SemanticResolution]:
        # 1↔1：同一证据存在多条记录时，稳定选择 created_at、rowid 最早的一条。
        r = row_one(
            self._db,
            "SELECT * FROM semantic_resolution WHERE evidence_id = ? ORDER BY created_at ASC, rowid ASC LIMIT 1",
            (evidence_id,),
        )
        return _from_row(r) if r is not None else None

    def for_evidence_ids(self, evidence_ids: list[str]) -> list[SemanticResolution]:
        if not evidence_ids:
            return []
        placeholders = ",".join("?" for _ in evidence_ids)
        rows = row_all(
            self._db,
            f"SELECT * FROM semantic_resolution WHERE evidence_id IN ({placeholders}) ORDER BY created_at ASC, rowid ASC",
            tuple(evidence_ids),
        )
        return [_from_row(r) for r in rows]

    def insert(self, res: SemanticResolution) -> None:
        self._insert_row(res)

    def remove_by_evidence_ids(self, evidence_ids: list[str]) -> int:
        if not evidence_ids:
            return 0
        placeholders = ",".join("?" for _ in evidence_ids)
        cur = self._db.cursor()
        cur.execute(f"DELETE FROM semantic_resolution WHERE evidence_id IN ({placeholders})", tuple(evidence_ids))
        return cur.rowcount
