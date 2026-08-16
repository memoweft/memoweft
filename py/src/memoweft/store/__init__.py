"""与 TypeScript store 实现共享契约的 SQLite 存储层。

shared/parity/schema.json 验证 schema 结构，shared/parity/fts.json 验证 FTS5 trigram 排序。
"""
from __future__ import annotations

from .cognition import SqliteCognitionStore
from .driver import (
    BUSY_TIMEOUT_MS,
    FtsUnavailableError,
    IncompatibleSchemaError,
    application_id,
    fts5_available,
    open_db,
    user_version,
)
from .event import SqliteEventStore
from .evidence import SqliteEvidenceStore
from .interaction_context import SqliteInteractionContextStore, hash_context
from .keyword import Hit, KeywordRetriever, to_match_query
from .schema import (
    BASE_SCHEMA_SQL,
    CURRENT_SCHEMA_SQL,
    CURRENT_WORLD_SCHEMA_SQL,
    IDENTITY_SCHEMA_SQL,
    MEMORY_WORLD_JOB_COLUMNS,
    MEMORY_LOOP_SCHEMA_SQL,
    PYTHON_APPLICATION_ID,
    SCHEMA_SQL,
    SCHEMA_VERSION,
    WORLD_JOB_SCHEMA_SQL,
    WORLD_SCHEMA_SQL,
)
from .semantic_resolution import SqliteSemanticResolutionStore
from .transaction import Transaction, make_transaction, noop_transaction

__all__ = [
    "BUSY_TIMEOUT_MS",
    "FtsUnavailableError",
    "IncompatibleSchemaError",
    "application_id",
    "fts5_available",
    "open_db",
    "user_version",
    "Hit",
    "KeywordRetriever",
    "to_match_query",
    "SCHEMA_SQL",
    "SCHEMA_VERSION",
    "BASE_SCHEMA_SQL",
    "CURRENT_SCHEMA_SQL",
    "CURRENT_WORLD_SCHEMA_SQL",
    "IDENTITY_SCHEMA_SQL",
    "MEMORY_WORLD_JOB_COLUMNS",
    "MEMORY_LOOP_SCHEMA_SQL",
    "PYTHON_APPLICATION_ID",
    "WORLD_JOB_SCHEMA_SQL",
    "WORLD_SCHEMA_SQL",
    "SqliteEvidenceStore",
    "SqliteEventStore",
    "SqliteCognitionStore",
    "SqliteInteractionContextStore",
    "hash_context",
    "SqliteSemanticResolutionStore",
    "Transaction",
    "make_transaction",
    "noop_transaction",
]
