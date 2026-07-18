"""与 TypeScript evidence store 契约一致的 SQLite 证据存储层。

职责:只做存储(写入/读取/时间窗查询),不做判断。授权位/双时态在 put 按 source_kind 规则补默认。
幂等:带 origin_id 的重复写入只存一次。schema 已由 store/schema.py 在 open_db 建全(fresh 库全列)。
异步取舍：SQLite 通过 stdlib sqlite3 同步直调，不引入 aiosqlite。
"""
from __future__ import annotations

import sqlite3
import uuid
from typing import Optional

from ..clock import Clock, system_clock, to_iso_z
from ..config import CONFIG, Config, cloud_read_default
from ..types import Evidence, EvidenceInput
from ._rows import row_all, row_one


def _from_row(r: sqlite3.Row) -> Evidence:
    # 只读 Evidence 的 13 列,preceding_ai_context 【故意不读】( 结构墙)——见 preceding_ai_context_of。
    return Evidence(
        id=r["id"],
        subject_id=r["subject_id"],
        source_kind=r["source_kind"],
        host_id=r["host_id"],
        origin_id=r["origin_id"],
        occurred_at=r["occurred_at"],
        recorded_at=r["recorded_at"],
        raw_content=r["raw_content"],
        summary=r["summary"],
        allow_local_read=r["allow_local_read"] == 1,
        allow_cloud_read=r["allow_cloud_read"] == 1,
        allow_inference=r["allow_inference"] == 1,
        corrects_evidence_id=r["corrects_evidence_id"],
    )


class SqliteEvidenceStore:
    """使用 open_db 共享连接的证据存储；cfg 为 put 提供默认授权。"""

    def __init__(self, db: sqlite3.Connection, cfg: Config = CONFIG, clock: Clock = system_clock) -> None:
        self._db = db
        self._cfg = cfg
        self._clock = clock

    def put(self, inp: EvidenceInput) -> Evidence:
        # origin_id 提供幂等性；已存在时返回原记录而不重复写入。
        if inp.origin_id:
            existing = self.find_by_origin(inp.origin_id)
            if existing is not None:
                return existing
        recorded_at = to_iso_z(self._clock())
        # 授权默认值按 source_kind 分流（与 evidence/store.ts 的存储边界一致）：
        #   observed/tool → 各自保守默认;spoken/inferred → evidence_defaults + cloud_read_default。显式永远优先。
        if inp.source_kind == "observed":
            local_default = self._cfg.observed_defaults.allow_local_read
            cloud_default = self._cfg.observed_defaults.allow_cloud_read
            infer_default = self._cfg.observed_defaults.allow_inference
        elif inp.source_kind == "tool":
            local_default = self._cfg.tool_defaults.allow_local_read
            cloud_default = self._cfg.tool_defaults.allow_cloud_read
            infer_default = self._cfg.tool_defaults.allow_inference
        else:
            local_default = self._cfg.evidence_defaults.allow_local_read
            cloud_default = cloud_read_default(self._cfg)
            infer_default = self._cfg.evidence_defaults.allow_inference
        ev = Evidence(
            id=str(uuid.uuid4()),
            subject_id=inp.subject_id,
            source_kind=inp.source_kind,
            host_id=inp.host_id,
            origin_id=inp.origin_id,
            occurred_at=inp.occurred_at if inp.occurred_at is not None else recorded_at,
            recorded_at=recorded_at,
            raw_content=inp.raw_content,
            summary=inp.summary if inp.summary is not None else inp.raw_content,  # 缺省摘要使用原文。
            allow_local_read=inp.allow_local_read if inp.allow_local_read is not None else local_default,
            allow_cloud_read=inp.allow_cloud_read if inp.allow_cloud_read is not None else cloud_default,
            allow_inference=inp.allow_inference if inp.allow_inference is not None else infer_default,
            corrects_evidence_id=inp.corrects_evidence_id,
        )
        self._insert_row(ev, inp.preceding_ai_context)
        return ev

    def _insert_row(self, ev: Evidence, preceding_ai_context: Optional[str]) -> None:
        # preceding_ai_context 作为额外列写入(不在 Evidence 读结构;insert 路径恒 None,导出已剥离)。
        self._db.execute(
            """INSERT INTO evidence (
              id, subject_id, source_kind, host_id, origin_id,
              occurred_at, recorded_at, raw_content, summary,
              allow_local_read, allow_cloud_read, allow_inference, corrects_evidence_id,
              preceding_ai_context
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ev.id, ev.subject_id, ev.source_kind, ev.host_id, ev.origin_id,
                ev.occurred_at, ev.recorded_at, ev.raw_content, ev.summary,
                1 if ev.allow_local_read else 0, 1 if ev.allow_cloud_read else 0,
                1 if ev.allow_inference else 0, ev.corrects_evidence_id, preceding_ai_context,
            ),
        )

    def get(self, id: str) -> Optional[Evidence]:
        r = row_one(self._db, "SELECT * FROM evidence WHERE id = ?", (id,))
        return _from_row(r) if r is not None else None

    def all(self) -> list[Evidence]:
        rows = row_all(self._db, "SELECT * FROM evidence ORDER BY recorded_at ASC, rowid ASC")
        return [_from_row(r) for r in rows]

    def by_time_range(self, from_iso: str, to_iso: str) -> list[Evidence]:
        rows = row_all(
            self._db,
            "SELECT * FROM evidence WHERE occurred_at >= ? AND occurred_at <= ? ORDER BY occurred_at ASC, rowid ASC",
            (from_iso, to_iso),
        )
        return [_from_row(r) for r in rows]

    def update(
        self,
        id: str,
        *,
        raw_content: Optional[str] = None,
        summary: Optional[str] = None,
        allow_cloud_read: Optional[bool] = None,
        allow_inference: Optional[bool] = None,
    ) -> Optional[Evidence]:
        # 未提供字段保持原值，对应 TS `?? cur`；授权位布尔值以 0/1 存储。
        cur = self.get(id)
        if cur is None:
            return None
        rc = raw_content if raw_content is not None else cur.raw_content
        sm = summary if summary is not None else cur.summary
        acr = allow_cloud_read if allow_cloud_read is not None else cur.allow_cloud_read
        ai = allow_inference if allow_inference is not None else cur.allow_inference
        self._db.execute(
            "UPDATE evidence SET raw_content = ?, summary = ?, allow_cloud_read = ?, allow_inference = ? WHERE id = ?",
            (rc, sm, 1 if acr else 0, 1 if ai else 0, id),
        )
        return self.get(id)

    def remove(self, id: str) -> bool:
        cur = self._db.cursor()
        cur.execute("DELETE FROM evidence WHERE id = ?", (id,))
        return cur.rowcount > 0

    def find_by_origin(self, origin_id: str) -> Optional[Evidence]:
        r = row_one(self._db, "SELECT * FROM evidence WHERE origin_id = ?", (origin_id,))
        return _from_row(r) if r is not None else None

    def preceding_ai_context_of(self, evidence_id: str) -> Optional[str]:
        # 仅查询该列，确保 AI 上下文不会进入 Evidence 读取结构。
        r = row_one(self._db, "SELECT preceding_ai_context FROM evidence WHERE id = ?", (evidence_id,))
        return r["preceding_ai_context"] if r is not None else None

    def insert(self, ev: Evidence) -> None:
        # 导入/恢复:Evidence 无 preceding_ai_context 字段 → 该列恒 None(AI 上文永不跨导出边界)。
        self._insert_row(ev, None)
