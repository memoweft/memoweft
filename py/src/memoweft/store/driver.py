"""SQLite driver：打开数据库、创建 schema、设置版本并探测 FTS5 能力。

异步取舍：SQLite 本质同步（TS 侧 nodeSqliteDriver 也保持全链同步），Python 使用 stdlib sqlite3。
  同步直调即可;跨表事务靠单连接(与 TS openStores 同)。
"""
from __future__ import annotations

from os.path import exists
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


def _migrate(db: sqlite3.Connection, current: int) -> None:
    """把已有库升到当前版本；每一版数据迁移独立事务，和 TS 迁移器同口径。"""
    for version in range(current + 1, SCHEMA_VERSION + 1):
        try:
            db.execute("BEGIN")
            if version == 2:
                # rc.1 的撤回台账意味着 cognition 已依赖被删证据。其 content 是不可拆分的
                # 派生文本，必须连同两类关系行整体清掉，不能只断一条 provenance 链。
                db.execute(
                    "DELETE FROM cognition_evidence WHERE cognition_id IN "
                    "(SELECT DISTINCT cognition_id FROM evidence_retraction)"
                )
                db.execute(
                    "DELETE FROM cognition WHERE id IN "
                    "(SELECT DISTINCT cognition_id FROM evidence_retraction)"
                )
                db.execute("DELETE FROM evidence_retraction")
            db.execute(f"PRAGMA user_version = {version}")
            db.execute("COMMIT")
        except BaseException as exc:
            try:
                db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise RuntimeError(f"Migration v{version} failed and was rolled back: {exc}") from exc


def open_db(path: str = ":memory:") -> sqlite3.Connection:
    """开库并升级 schema：新库直接盖当前版本，旧库按事务迁移，未来库拒绝打开。"""
    fresh = path == ":memory:" or not exists(path)
    db = sqlite3.connect(path)
    # autocommit(isolation_level=None):每条 DML 立即提交、无隐式 BEGIN,对齐 TS node:sqlite 的
    #   autocommit 语义;跨表事务由写路径显式 BEGIN/COMMIT/ROLLBACK 控制( transaction,同 openStores)。
    db.isolation_level = None
    db.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    current = user_version(db)
    if current > SCHEMA_VERSION:
        db.close()
        raise RuntimeError(
            f"Database schema version v{current} is higher than the v{SCHEMA_VERSION} supported by this memoweft"
        )
    try:
        # 新库和旧库都先保证当前表集合存在；数据升级仍只在 _migrate 的事务中执行。
        for stmt in SCHEMA_SQL:
            db.execute(stmt)
        if fresh:
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        else:
            _migrate(db, current)
        return db
    except BaseException:
        db.close()
        raise


def user_version(db: sqlite3.Connection) -> int:
    row = db.execute("PRAGMA user_version").fetchone()
    return int(row[0])
