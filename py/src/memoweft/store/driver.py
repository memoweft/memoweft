"""SQLite driver：打开数据库、创建 schema、设置版本并探测 FTS5 能力。

异步取舍：SQLite 本质同步（TS 侧 nodeSqliteDriver 也保持全链同步），Python 使用 stdlib sqlite3。
  同步直调即可;跨表事务靠单连接(与 TS openStores 同)。
"""
from __future__ import annotations

from os.path import exists
import hashlib
import re
import sqlite3
from typing import Sequence

from .schema import (
    BASE_SCHEMA_SQL,
    BOUNDARY_EVIDENCE_CONTENT_COLUMNS,
    BOUNDARY_EVIDENCE_CONTENT_SCHEMA_SQL,
    CLARIFICATION_SCHEMA_SQL,
    COGNITION_TARGET_ALTER_V14_SQL,
    COGNITION_TARGET_COLUMNS,
    COGNITION_TARGET_SCHEMA_SQL,
    CURRENT_REQUIRED_SCHEMA_OBJECTS,
    CURRENT_SCHEMA_SQL,
    ENTITY_ALIAS_SCHEMA_SQL,
    ENTITY_COLUMNS,
    ENTITY_RELATIONSHIP_SCHEMA_SQL,
    MEMORY_WORLD_JOB_COLUMNS,
    PORTABLE_IMPORT_RECEIPT_SCHEMA_SQL,
    PYTHON_APPLICATION_ID,
    PYTHON_V6_REQUIRED_SCHEMA_OBJECTS,
    RELATIONSHIP_COLUMNS,
    RETRACTION_ALTER_V13_SQL,
    RETRACTION_COLUMNS,
    RETRACTION_SCHEMA_SQL,
    SCHEMA_VERSION,
    TERMINAL_OUTCOME_SCHEMA_SQL,
    TRUST_COMMAND_SCHEMA_SQL,
    TRUST_REJECTION_SCHEMA_SQL,
    WORLD_EVENT_COLUMNS,
    WORLD_EVENT_SCHEMA_SQL,
    WORLD_JOB_ALTER_V15_SQL,
    WORLD_JOB_SCHEMA_SQL,
    WORLD_SCHEMA_SQL,
)

#: 写锁被别的进程占着时最多等这么久再报 SQLITE_BUSY(对齐 TS store/busyTimeout.ts)。
BUSY_TIMEOUT_MS = 5000


class FtsUnavailableError(RuntimeError):
    """当前 SQLite 未编译 FTS5 → 关键词召回不可用(工厂应据此降级 NullRetriever)。对齐 TS FtsUnavailableError。"""


class IncompatibleSchemaError(RuntimeError):
    """数据库版本号与实际 Python-owned 物理 schema 不闭合。"""


def fts5_available(db: sqlite3.Connection) -> bool:
    """探测 FTS5 可用性:建临时虚表试探,抛错即不可用(对齐 KeywordRetriever 构造的探测点)。"""
    try:
        db.execute("CREATE VIRTUAL TABLE temp._memoweft_fts_probe USING fts5(x, tokenize='trigram')")
        db.execute("DROP TABLE temp._memoweft_fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def application_id(db: sqlite3.Connection) -> int:
    """读取 SQLite 文件级 Python-owned 标识。"""
    row = db.execute("PRAGMA application_id").fetchone()
    return int(row[0])


def _sha256_hex(raw: str) -> str:
    """SHA-256 of the exact UTF-8 bytes of a raw content string."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _schema_object_names(db: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        str(row[0])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
    )


_CREATE_OBJECT_NAME = re.compile(
    r"\ACREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def _expected_schema_sql(statements: Sequence[str]) -> dict[str, str]:
    """Return SQLite's persisted SQL text for the closed DDL source.

    SQLite removes ``IF NOT EXISTS`` when it records CREATE statements in
    ``sqlite_master``.  Everything else (columns, CHECK clauses, index order,
    partial predicates, and whitespace) remains a useful exact physical-shape
    fingerprint.
    """

    expected: dict[str, str] = {}
    for statement in statements:
        match = _CREATE_OBJECT_NAME.match(statement)
        if match is None:
            raise RuntimeError("MemoWeft schema contains an unsupported DDL statement")
        name = match.group(1)
        if name in expected:
            raise RuntimeError("MemoWeft schema contains a duplicate object name")
        expected[name] = re.sub(
            r"\s+IF\s+NOT\s+EXISTS",
            "",
            statement,
            count=1,
            flags=re.IGNORECASE,
        )
    return expected


_PYTHON_V6_EXPECTED_SCHEMA_SQL = _expected_schema_sql(
    BASE_SCHEMA_SQL + WORLD_SCHEMA_SQL
)
_CURRENT_EXPECTED_SCHEMA_SQL = _expected_schema_sql(CURRENT_SCHEMA_SQL)

if frozenset(_PYTHON_V6_EXPECTED_SCHEMA_SQL) != PYTHON_V6_REQUIRED_SCHEMA_OBJECTS:
    raise RuntimeError("Python v6 schema object manifest is inconsistent")
if frozenset(_CURRENT_EXPECTED_SCHEMA_SQL) != CURRENT_REQUIRED_SCHEMA_OBJECTS:
    raise RuntimeError("Current Python schema object manifest is inconsistent")


def _require_schema_objects(
    db: sqlite3.Connection,
    required: frozenset[str],
    *,
    label: str,
) -> None:
    missing = sorted(required - _schema_object_names(db))
    if missing:
        raise IncompatibleSchemaError(
            f"{label} database has an incompatible physical schema; missing: {', '.join(missing)}"
        )


def _require_schema_definitions(
    db: sqlite3.Connection,
    expected: dict[str, str],
    *,
    label: str,
) -> None:
    """Require every owned table/index to have the exact frozen definition."""

    _require_schema_objects(db, frozenset(expected), label=label)
    actual = {
        str(row[0]): row[1]
        for row in db.execute(
            "SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if str(row[0]) in expected
    }
    mismatched = sorted(
        name for name, sql in expected.items() if actual.get(name) != sql
    )
    if mismatched:
        raise IncompatibleSchemaError(
            f"{label} database has incompatible object definitions: "
            + ", ".join(mismatched)
        )


def _validate_python_v6(db: sqlite3.Connection) -> None:
    """在任何 v7 DDL 前识别真实 Python v6，拒绝 TS v6 或损坏库。"""
    if application_id(db) != 0:
        raise IncompatibleSchemaError(
            "Python v6 database has an unexpected application_id"
        )
    _require_schema_definitions(
        db,
        _PYTHON_V6_EXPECTED_SCHEMA_SQL,
        label="Python v6",
    )


def _validate_current_schema(db: sqlite3.Connection) -> None:
    """当前版本只校验，不补表：同一 user_version 不允许存在多个物理形状。"""
    if application_id(db) != PYTHON_APPLICATION_ID:
        raise IncompatibleSchemaError(
            "Current Python database has an incompatible application_id"
        )
    # ``entity``, ``retraction``, ``cognition_target`` and ``memory_world_job``
    # are excluded from the exact SQL-text fingerprint: their columns were
    # added via ALTER TABLE (v11/v13/v14/v15), and SQLite keeps the original
    # CREATE text in sqlite_master for ALTER-mutated tables, so a migrated
    # table can never match the fresh CREATE text.  Their closed contracts are
    # the column ORDER (checked below) plus object existence — both are still
    # enforced.
    _require_schema_definitions(
        db,
        {
            name: sql
            for name, sql in _CURRENT_EXPECTED_SCHEMA_SQL.items()
            if name
            not in ("entity", "retraction", "cognition_target", "memory_world_job")
        },
        label="Current Python",
    )
    columns = tuple(
        str(row[1]) for row in db.execute("PRAGMA table_info(memory_world_job)")
    )
    if columns != MEMORY_WORLD_JOB_COLUMNS:
        raise IncompatibleSchemaError(
            "Current Python database has an incompatible memory_world_job schema"
        )
    content_columns = tuple(
        str(row[1]) for row in db.execute("PRAGMA table_info(boundary_evidence_content)")
    )
    if content_columns != BOUNDARY_EVIDENCE_CONTENT_COLUMNS:
        raise IncompatibleSchemaError(
            "Current Python database has an incompatible boundary_evidence_content schema"
        )
    entity_columns = tuple(
        str(row[1]) for row in db.execute("PRAGMA table_info(entity)")
    )
    if entity_columns != ENTITY_COLUMNS:
        raise IncompatibleSchemaError(
            "Current Python database has an incompatible entity schema"
        )
    relationship_columns = tuple(
        str(row[1]) for row in db.execute("PRAGMA table_info(relationship)")
    )
    if relationship_columns != RELATIONSHIP_COLUMNS:
        raise IncompatibleSchemaError(
            "Current Python database has an incompatible relationship schema"
        )
    target_columns = tuple(
        str(row[1]) for row in db.execute("PRAGMA table_info(cognition_target)")
    )
    if target_columns != COGNITION_TARGET_COLUMNS:
        raise IncompatibleSchemaError(
            "Current Python database has an incompatible cognition_target schema"
        )
    retraction_columns = tuple(
        str(row[1]) for row in db.execute("PRAGMA table_info(retraction)")
    )
    if retraction_columns != RETRACTION_COLUMNS:
        raise IncompatibleSchemaError(
            "Current Python database has an incompatible retraction schema"
        )
    world_event_columns = tuple(
        str(row[1]) for row in db.execute("PRAGMA table_info(world_event)")
    )
    if world_event_columns != WORLD_EVENT_COLUMNS:
        raise IncompatibleSchemaError(
            "Current Python database has an incompatible world_event schema"
        )


def _migrate(db: sqlite3.Connection, current: int) -> None:
    """把已有库升到当前版本；每一版数据迁移独立事务，和 TS 迁移器同口径。"""
    for version in range(current + 1, SCHEMA_VERSION + 1):
        try:
            db.execute("BEGIN IMMEDIATE")
            # 另一个进程可能在本连接读取 ``current`` 后先完成了迁移。拿到
            # RESERVED lock 后必须重读版本，不能靠 IF NOT EXISTS 掩盖竞态。
            locked_current = user_version(db)
            if locked_current >= version:
                db.execute("COMMIT")
                continue
            if locked_current != version - 1:
                raise IncompatibleSchemaError(
                    "Database schema version changed non-sequentially during migration"
                )
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
            elif version == 3:
                for statement in WORLD_SCHEMA_SQL:
                    db.execute(statement)
            elif version == 4:
                # v4 stamps the closed world-evolution proposal payload
                # contract.  It needs no new table, but older code must refuse
                # to interpret the new proposal kind as a legacy correction.
                pass
            elif version == 5:
                # v5 stamps the closed product_bundle relationship successor
                # evolution payload contract. No DDL; rows are preserved.
                pass
            elif version == 6:
                # v6 stamps product-bundle-cognition-evidence-update-payload-contract:
                # a closed product_bundle same-cognition Evidence update. No DDL;
                # every existing row and payload byte is preserved.
                pass
            elif version == 7:
                # v7 is Python-owned and adds the durable Hermes boundary ->
                # Personal Memory World job/receipt/lease ledger.  Keep this
                # separate from frozen v3 WORLD_SCHEMA_SQL and TS v6 parity.
                for statement in WORLD_JOB_SCHEMA_SQL:
                    db.execute(statement)
                db.execute(f"PRAGMA application_id = {PYTHON_APPLICATION_ID}")
            elif version == 8:
                # v8 binds every boundary-accepted Evidence row to the SHA-256
                # of its exact raw content.  The formal batch compiler re-verifies
                # the binding before the model call and again before Apply.
                # Existing rows are backfilled deterministically so the binding
                # invariant holds for the whole ledger.
                for statement in BOUNDARY_EVIDENCE_CONTENT_SCHEMA_SQL:
                    db.execute(statement)
                for _eid, raw in db.execute(
                    "SELECT id, raw_content FROM evidence"
                ).fetchall():
                    db.execute(
                        "INSERT INTO boundary_evidence_content "
                        "(evidence_id, raw_content_hash) VALUES (?, ?)",
                        (_eid, _sha256_hex(raw)),
                    )
            elif version == 9:
                # v9 adds the first-class Entity + Relationship World objects
                # (V3 window).  Pure additive DDL: every existing row and payload
                # byte is preserved.
                for statement in ENTITY_RELATIONSHIP_SCHEMA_SQL:
                    db.execute(statement)
            elif version == 10:
                # v10 adds the targeted-cognition sidecar (third-party target
                # dimension; absent row == owner_self).  Pure additive DDL.
                for statement in COGNITION_TARGET_SCHEMA_SQL:
                    db.execute(statement)
            elif version == 11:
                # v11 adds the entity alias ledger column (alias merging).
                # DBs that pass through the v9 DDL after this change already
                # carry the column in the CREATE; only pre-change v9/v10
                # entity tables need the ALTER.
                columns = {
                    str(row[1])
                    for row in db.execute("PRAGMA table_info(entity)").fetchall()
                }
                if "aliases_json" not in columns:
                    for statement in ENTITY_ALIAS_SCHEMA_SQL:
                        db.execute(statement)
            elif version == 12:
                # v12 adds the retraction sidecar (retract = correct with no
                # replacement).  The CURRENT CREATE (already carrying the v13
                # prior_event_id column) is used so a stepwise migration's
                # stored SQL text converges with a fresh database; real v12
                # databases get the column via the guarded v13 ALTER instead.
                for statement in RETRACTION_SCHEMA_SQL:
                    db.execute(statement)
            elif version == 13:
                # v13 adds the first-class World Event object plus the event
                # retraction column.  retraction was ALTER-mutated, so the
                # column add is guarded (fresh v13 CREATE already carries it).
                columns = {
                    str(row[1])
                    for row in db.execute(
                        "PRAGMA table_info(retraction)"
                    ).fetchall()
                }
                if "prior_event_id" not in columns:
                    for statement in RETRACTION_ALTER_V13_SQL:
                        db.execute(statement)
                for statement in WORLD_EVENT_SCHEMA_SQL:
                    db.execute(statement)
            elif version == 14:
                # v14 adds the perspective-holder column to the targeted-
                # cognition sidecar (V5).  Fresh v10-step CREATEs already carry
                # it; only pre-v14 tables need the guarded ALTER.
                columns = {
                    str(row[1])
                    for row in db.execute(
                        "PRAGMA table_info(cognition_target)"
                    ).fetchall()
                }
                if "perspective_entity_id" not in columns:
                    for statement in COGNITION_TARGET_ALTER_V14_SQL:
                        db.execute(statement)
            elif version == 15:
                # v15 adds AUTHORITY §3 terminal observability to the World
                # Job (clarification_required/out_of_scope split from
                # no_change).  Fresh v15 CREATEs already carry the columns;
                # older tables get the guarded ALTER (column order closed:
                # both appended LAST).
                columns = {
                    str(row[1])
                    for row in db.execute(
                        "PRAGMA table_info(memory_world_job)"
                    ).fetchall()
                }
                if "terminal_state" not in columns:
                    for statement in WORLD_JOB_ALTER_V15_SQL:
                        db.execute(statement)
            elif version == 16:
                # v16 adds a separate immutable five-terminal outcome and its
                # independently recoverable host-delivery ledger.  This is
                # a forward-only cutover: pre-v16 terminal jobs stay byte-for-
                # byte intact and receive no fabricated outcome because their
                # exact terminal-time revision was not persisted. Every
                # terminal transition executed after v16 writes its outcome in
                # the owning transaction.
                for statement in TERMINAL_OUTCOME_SCHEMA_SQL:
                    db.execute(statement)
            elif version == 17:
                # v17 adds subject-bound Trust commands, immutable receipts,
                # and one cross-kind archive/mute lifecycle sidecar. Existing
                # World/Evidence bytes are preserved; no command is fabricated.
                for statement in TRUST_COMMAND_SCHEMA_SQL:
                    db.execute(statement)
            elif version == 18:
                # v18 adds the durable clarification request/answer/closure
                # lifecycle. Existing outcomes and World rows are preserved;
                # no historical clarification is fabricated.
                for statement in CLARIFICATION_SCHEMA_SQL:
                    db.execute(statement)
            elif version == 19:
                # v19 adds the immutable Portable v4 apply receipt. No World
                # row is backfilled and no historical import is fabricated.
                for statement in PORTABLE_IMPORT_RECEIPT_SCHEMA_SQL:
                    db.execute(statement)
            elif version == 20:
                # Widen the closed Trust operation set while preserving command
                # identities and immutable receipts. SQLite cannot ALTER CHECK.
                db.execute("ALTER TABLE trust_command RENAME TO trust_command_v19")
                db.execute("DROP INDEX ix_trust_command_subject")
                db.execute(TRUST_COMMAND_SCHEMA_SQL[0])
                db.execute("INSERT INTO trust_command SELECT * FROM trust_command_v19")
                db.execute("DROP TABLE trust_command_v19")
                db.execute(TRUST_COMMAND_SCHEMA_SQL[1])
                for statement in TRUST_REJECTION_SCHEMA_SQL:
                    name = statement.split("CREATE TABLE ", 1)[1].split(" (", 1)[0]
                    if db.execute(
                        "SELECT 1 FROM sqlite_master WHERE name = ?", (name,)
                    ).fetchone() is None:
                        db.execute(statement)
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
        if fresh:
            # DDL 与两个文件头标记一起提交；崩溃不会留下“v7 但缺表”的新库。
            db.execute("BEGIN IMMEDIATE")
            try:
                for stmt in CURRENT_SCHEMA_SQL:
                    db.execute(stmt)
                db.execute(f"PRAGMA application_id = {PYTHON_APPLICATION_ID}")
                db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                db.execute("COMMIT")
            except BaseException:
                try:
                    db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        elif current == SCHEMA_VERSION:
            # 绝不在相同 user_version 下 CREATE IF NOT EXISTS 自愈；损坏/混用直接拒绝。
            _validate_current_schema(db)
        else:
            if current == 6:
                _validate_python_v6(db)
            else:
                # 历史 v0-v5 仍沿用 1.x compatibility path；v6 起进入严格
                # Python-owned 物理边界。
                for stmt in BASE_SCHEMA_SQL:
                    db.execute(stmt)
            _migrate(db, current)
            _validate_current_schema(db)
        return db
    except BaseException:
        db.close()
        raise


def user_version(db: sqlite3.Connection) -> int:
    row = db.execute("PRAGMA user_version").fetchone()
    return int(row[0])
