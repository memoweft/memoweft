"""行工厂 helper —— 用【局部 Row 游标】查询,不改共享连接的全局 row_factory(免扰 keyword.py 等)。

各 store 共用：按列名取值构造 dataclass，并保持与 TypeScript store 相同的查询契约。
"""
from __future__ import annotations

import sqlite3
from typing import Optional, cast


def row_one(db: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> Optional[sqlite3.Row]:
    cur = db.cursor()
    cur.row_factory = sqlite3.Row
    row = cur.execute(sql, params).fetchone()  # sqlite3.fetchone() 返回 Any
    return cast("Optional[sqlite3.Row]", row)


def row_all(db: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
    cur = db.cursor()
    cur.row_factory = sqlite3.Row
    return cur.execute(sql, params).fetchall()
