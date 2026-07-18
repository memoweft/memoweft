"""SQLite driver：打开数据库、创建 schema、设置版本并探测 FTS5 能力。

异步取舍：SQLite 本质同步（TS 侧 nodeSqliteDriver 也保持全链同步），Python 使用 stdlib sqlite3。
  同步直调即可;跨表事务靠单连接(与 TS openStores 同)。
"""
from __future__ import annotations

import sqlite3

from .schema import SCHEMA_SQL, SCHEMA_VERSION

#: 写锁被别的进程占着时最多等这么久再报 SQLITE_BUSY(对齐 TS store/busyTimeout.ts)。
BUSY_TIMEOUT_MS = 5000


class FtsUnavailableError(RuntimeError):
    """当前 SQLite 未编译 FTS5 → 关键词召回不可用(工厂应据此降级 NullRetriever)。对齐 TS FtsUnavailableError。"""


def fts5_available(db: sqlite3.Connection) -> bool:
    """探测 FTS5 可用性:建临时虚表试探,抛错即不可用(对齐 KeywordRetriever 构造的探测点)。"""
    try:
        db.execute("CREATE VIRTUAL TABLE temp._memoweft_fts_probe USING fts5(x, tokenize='trigram')")
        db.execute("DROP TABLE temp._memoweft_fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def open_db(path: str = ":memory:") -> sqlite3.Connection:
    """开库:设 busy_timeout、建 fresh schema(全列)、盖 user_version。返回单条共享连接。

    只处理 fresh 库（新文件 / :memory:）；老库升级（runMigrations 从旧 user_version 升）由宿主负责。
    """
    db = sqlite3.connect(path)
    # autocommit(isolation_level=None):每条 DML 立即提交、无隐式 BEGIN,对齐 TS node:sqlite 的
    #   autocommit 语义;跨表事务由写路径显式 BEGIN/COMMIT/ROLLBACK 控制( transaction,同 openStores)。
    db.isolation_level = None
    db.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    for stmt in SCHEMA_SQL:
        db.execute(stmt)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    db.commit()
    return db


def user_version(db: sqlite3.Connection) -> int:
    row = db.execute("PRAGMA user_version").fetchone()
    return int(row[0])
