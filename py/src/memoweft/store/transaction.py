"""与 TypeScript store 契约一致的可重入事务执行器。

把一段【同步】写包成一个 SQLite 事务:全成或全滚。可重入:已在事务里再调只直接跑,不嵌套 BEGIN。
⚠️ 只能包同步写:LLM 已在外 await/调完,此闭包内不含网络调用。autocommit(isolation_level=None)下手动 BEGIN/COMMIT/ROLLBACK。
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable

#: 将一组同步写入包装为全成或全滚的事务。
Transaction = Callable[[Callable[[], Any]], Any]


def noop_transaction(fn: Callable[[], Any]) -> Any:
    """直接执行操作而不创建事务，用于各自持有连接的测试场景。"""
    return fn()


def make_transaction(db: sqlite3.Connection) -> Transaction:
    """创建绑定连接的可重入事务器；最外层执行 BEGIN/COMMIT/ROLLBACK，嵌套层直接运行。"""
    depth = [0]

    def transaction(fn: Callable[[], Any]) -> Any:
        if depth[0] > 0:
            return fn()  # 已在事务里 → 直接跑(SQLite 不支持嵌套 BEGIN)
        db.execute("BEGIN")
        # 只有 BEGIN 成功后才进入深度。外部事务令 BEGIN 失败时不能毒化后续调用，
        # 否则它们会被误判为嵌套事务并在 autocommit 中直接执行。
        depth[0] += 1
        try:
            r = fn()
            db.execute("COMMIT")
            return r
        except BaseException:
            try:
                db.execute("ROLLBACK")  # 任一步抛错 → 整段回滚
            except sqlite3.Error:
                # 保留 callback / COMMIT 的原始异常；rollback 异常不应覆盖它。
                pass
            raise
        finally:
            depth[0] -= 1

    return transaction
