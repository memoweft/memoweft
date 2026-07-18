"""交互上下文存储（v0.6），与 TypeScript 存储实现保持契约一致。

只存用户可见的非证据上下文快照，不产 Cognition、永不成为 Evidence。record 按 context_hash 查重幂等。
hash_context 用 json.dumps(ensure_ascii=False, separators) 复刻 JS JSON.stringify 字节。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from typing import Optional

from ..clock import Clock, system_clock, to_iso_z
from ..types import InteractionContext, InteractionContextInput, VisibleTurn
from ._rows import row_all, row_one


def _turns_to_payload(context: list[VisibleTurn]) -> list[dict[str, str]]:
    # 字段序 role,content —— 对齐 JS VisibleTurn 的插入序,保证 JSON 字节一致。
    return [{"role": t.role, "content": t.content} for t in context]


def hash_context(context: list[VisibleTurn]) -> str:
    """计算 sha256(JSON.stringify(context)) 内容指纹，并保持与 JS 相同的序列化字节。"""
    j = json.dumps(_turns_to_payload(context), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(j.encode("utf-8")).hexdigest()


def _context_to_json(context: list[VisibleTurn]) -> str:
    return json.dumps(_turns_to_payload(context), ensure_ascii=False, separators=(",", ":"))


def _context_from_json(s: str) -> list[VisibleTurn]:
    return [VisibleTurn(role=t["role"], content=t["content"]) for t in json.loads(s)]


def _from_row(r: sqlite3.Row) -> InteractionContext:
    return InteractionContext(
        id=r["id"],
        subject_id=r["subject_id"],
        conversation_id=r["conversation_id"],
        episode_id=r["episode_id"],
        context=_context_from_json(r["context_json"]),
        context_hash=r["context_hash"],
        created_at=r["created_at"],
    )


class SqliteInteractionContextStore:
    """使用 open_db 共享连接的交互上下文存储。"""

    def __init__(self, db: sqlite3.Connection, clock: Clock = system_clock) -> None:
        self._db = db
        self._clock = clock

    def record(self, inp: InteractionContextInput) -> InteractionContext:
        # 幂等:按 context_hash 查重(非 DB 唯一约束——避免便携包跨库导入撞车)。
        ch = hash_context(inp.context)
        existing = row_one(self._db, "SELECT * FROM interaction_context WHERE context_hash = ?", (ch,))
        if existing is not None:
            return _from_row(existing)
        ctx = InteractionContext(
            id=str(uuid.uuid4()),
            subject_id=inp.subject_id,
            conversation_id=inp.conversation_id,
            episode_id=inp.episode_id,
            context=inp.context,
            context_hash=ch,
            created_at=to_iso_z(self._clock()),
        )
        self._insert_row(ctx)
        return ctx

    def _insert_row(self, ctx: InteractionContext) -> None:
        self._db.execute(
            "INSERT INTO interaction_context (id, subject_id, conversation_id, episode_id, context_json, context_hash, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                ctx.id, ctx.subject_id, ctx.conversation_id, ctx.episode_id,
                _context_to_json(ctx.context), ctx.context_hash, ctx.created_at,
            ),
        )

    def get(self, id: str) -> Optional[InteractionContext]:
        r = row_one(self._db, "SELECT * FROM interaction_context WHERE id = ?", (id,))
        return _from_row(r) if r is not None else None

    def all(self, subject_id: Optional[str] = None) -> list[InteractionContext]:
        if subject_id is not None:
            rows = row_all(
                self._db,
                "SELECT * FROM interaction_context WHERE subject_id = ? ORDER BY created_at ASC, rowid ASC",
                (subject_id,),
            )
        else:
            rows = row_all(self._db, "SELECT * FROM interaction_context ORDER BY created_at ASC, rowid ASC")
        return [_from_row(r) for r in rows]

    def by_conversation(self, conversation_id: str) -> list[InteractionContext]:
        rows = row_all(
            self._db,
            "SELECT * FROM interaction_context WHERE conversation_id = ? ORDER BY created_at ASC, rowid ASC",
            (conversation_id,),
        )
        return [_from_row(r) for r in rows]

    def insert(self, ctx: InteractionContext) -> None:
        self._insert_row(ctx)

    def remove_by_subject(self, subject_id: str) -> int:
        cur = self._db.cursor()
        cur.execute("DELETE FROM interaction_context WHERE subject_id = ?", (subject_id,))
        return cur.rowcount
