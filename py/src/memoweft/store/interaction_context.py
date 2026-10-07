"""交互上下文存储（v0.6），与 TypeScript 存储实现保持契约一致。

只存用户可见的非证据上下文快照，不产 Cognition、永不成为 Evidence。record 按
subject_id + conversation_id + episode_id + context_hash 查重幂等，不能让相同文本跨用户/会话/episode 互相吞掉。
hash_context 用 json.dumps(ensure_ascii=False, separators) 复刻 JS JSON.stringify 字节。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Optional

from ..clock import Clock, system_clock, to_iso_z
from ..context_hash import hash_context as hash_context
from ..types import InteractionContext, InteractionContextInput, VisibleTurn
from ._rows import row_all, row_one


def _turns_to_payload(context: list[VisibleTurn]) -> list[dict[str, object]]:
    # 字段序 role,content —— 对齐 JS VisibleTurn 的插入序,保证 JSON 字节一致。
    payload: list[dict[str, object]] = []
    for turn in context:
        item: dict[str, object] = {"role": turn.role, "content": turn.content}
        for key in ("source_ref", "message_id", "timestamp", "model_context_dependencies"):
            value = getattr(turn, key)
            if value is not None:
                item[key] = value
        payload.append(item)
    return payload


def _context_to_json(context: list[VisibleTurn]) -> str:
    return json.dumps(
        _turns_to_payload(context), ensure_ascii=False, separators=(",", ":")
    )


def _context_from_json(s: str) -> list[VisibleTurn]:
    return [
        VisibleTurn(
            role=t["role"],
            content=t["content"],
            source_ref=t.get("source_ref"),
            message_id=t.get("message_id"),
            timestamp=t.get("timestamp"),
            model_context_dependencies=t.get("model_context_dependencies"),
        )
        for t in json.loads(s)
    ]


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
        # 幂等：同 subject + conversation + episode 内按 context_hash 查重。相同文本可合法出现在
        # 不同用户、会话和 episode，不能跨归属返回别的上下文记录。
        ch = hash_context(inp.context)
        existing = row_one(
            self._db,
            "SELECT * FROM interaction_context "
            "WHERE subject_id = ? AND conversation_id = ? AND episode_id = ? AND context_hash = ?",
            (inp.subject_id, inp.conversation_id, inp.episode_id, ch),
        )
        if existing is not None:
            return _from_row(existing)
        # A legacy boundary can be replayed after its assistant turn has been
        # causally linked.  The link intentionally changes context_hash; do not
        # let the old unlinked replay create a second interaction row.
        if all(turn.model_context_dependencies is None for turn in inp.context):
            prior_rows = row_all(
                self._db,
                "SELECT * FROM interaction_context WHERE subject_id = ? "
                "AND conversation_id = ? AND episode_id = ? ORDER BY rowid ASC",
                (inp.subject_id, inp.conversation_id, inp.episode_id),
            )
            for prior_row in prior_rows:
                prior = _from_row(prior_row)
                if len(prior.context) != len(inp.context):
                    continue
                if all(
                    (
                        left.role,
                        left.content,
                        left.source_ref,
                        left.message_id,
                        left.timestamp,
                    )
                    == (
                        right.role,
                        right.content,
                        right.source_ref,
                        right.message_id,
                        right.timestamp,
                    )
                    for left, right in zip(prior.context, inp.context)
                ):
                    return prior
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
                ctx.id,
                ctx.subject_id,
                ctx.conversation_id,
                ctx.episode_id,
                _context_to_json(ctx.context),
                ctx.context_hash,
                ctx.created_at,
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
            rows = row_all(
                self._db,
                "SELECT * FROM interaction_context ORDER BY created_at ASC, rowid ASC",
            )
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

    def link_dependencies(
        self,
        *,
        subject_id: str,
        conversation_id: str,
        user_message_id: str,
        assistant_message_id: str,
        expected_context_hash: str,
        dependencies: dict[str, object],
    ) -> tuple[str, str, str, str]:
        """CAS-link one exact user/assistant pair without changing its text.

        Returns ``(state, interaction_id, old_hash, current_hash)``.  Repeating
        the original request after a successful link is idempotent when the
        same dependency DTO is supplied; a different DTO always conflicts.
        """

        rows = row_all(
            self._db,
            "SELECT * FROM interaction_context WHERE subject_id = ? "
            "AND conversation_id = ? ORDER BY created_at ASC, rowid ASC",
            (subject_id, conversation_id),
        )
        candidates: list[tuple[InteractionContext, int]] = []
        for row in rows:
            context = _from_row(row)
            user_matches = [
                turn
                for turn in context.context
                if turn.role == "user" and turn.message_id == user_message_id
            ]
            assistant_matches = [
                index
                for index, turn in enumerate(context.context)
                if turn.role == "assistant"
                and turn.message_id == assistant_message_id
            ]
            if len(user_matches) == 1 and len(assistant_matches) == 1:
                candidates.append((context, assistant_matches[0]))
        if not candidates:
            return "not_found", "", "", ""

        exact: list[tuple[InteractionContext, int]] = []
        for context, index in candidates:
            turn = context.context[index]
            if context.context_hash == expected_context_hash:
                exact.append((context, index))
                continue
            if turn.model_context_dependencies == dependencies:
                unlinked = list(context.context)
                unlinked[index] = VisibleTurn(
                    role=turn.role,
                    content=turn.content,
                    source_ref=turn.source_ref,
                    message_id=turn.message_id,
                    timestamp=turn.timestamp,
                    model_context_dependencies=None,
                )
                if hash_context(unlinked) == expected_context_hash:
                    exact.append((context, index))
        if len(exact) != 1:
            context = candidates[0][0]
            return (
                "conflict",
                context.id,
                context.context_hash,
                context.context_hash,
            )

        current, index = exact[0]
        prior = current.context[index]
        if prior.model_context_dependencies == dependencies:
            return (
                "no_change",
                current.id,
                expected_context_hash,
                current.context_hash,
            )
        if prior.model_context_dependencies is not None:
            return (
                "conflict",
                current.id,
                current.context_hash,
                current.context_hash,
            )

        turns = list(current.context)
        turns[index] = VisibleTurn(
            role=prior.role,
            content=prior.content,
            source_ref=prior.source_ref,
            message_id=prior.message_id,
            timestamp=prior.timestamp,
            model_context_dependencies=dependencies,
        )
        next_hash = hash_context(turns)
        cursor = self._db.execute(
            "UPDATE interaction_context SET context_json = ?, context_hash = ? "
            "WHERE id = ? AND subject_id = ? AND conversation_id = ? "
            "AND context_hash = ?",
            (
                _context_to_json(turns),
                next_hash,
                current.id,
                subject_id,
                conversation_id,
                expected_context_hash,
            ),
        )
        if cursor.rowcount != 1:
            refreshed = self.get(current.id)
            refreshed_hash = (
                refreshed.context_hash if refreshed is not None else current.context_hash
            )
            return "conflict", current.id, refreshed_hash, refreshed_hash
        return "applied", current.id, current.context_hash, next_hash

    def remove_by_subject(self, subject_id: str) -> int:
        cur = self._db.cursor()
        cur.execute(
            "DELETE FROM interaction_context WHERE subject_id = ?", (subject_id,)
        )
        return cur.rowcount
