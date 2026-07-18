"""存储层(SQLite)—— 移植自 src/store / 各 store(1.3 · D-0042 · Phase 1b)。

parity:schema 结构对拍 shared/parity/schema.json;FTS5 trigram 排序对拍 shared/parity/fts.json。
CRUD 逐操作在 Phase 1c(便携包)按需补。
"""
from __future__ import annotations

from .cognition import SqliteCognitionStore
from .driver import BUSY_TIMEOUT_MS, FtsUnavailableError, fts5_available, open_db, user_version
from .event import SqliteEventStore
from .evidence import SqliteEvidenceStore
from .interaction_context import SqliteInteractionContextStore, hash_context
from .keyword import Hit, KeywordRetriever, to_match_query
from .schema import SCHEMA_SQL, SCHEMA_VERSION
from .semantic_resolution import SqliteSemanticResolutionStore

__all__ = [
    "BUSY_TIMEOUT_MS",
    "FtsUnavailableError",
    "fts5_available",
    "open_db",
    "user_version",
    "Hit",
    "KeywordRetriever",
    "to_match_query",
    "SCHEMA_SQL",
    "SCHEMA_VERSION",
    "SqliteEvidenceStore",
    "SqliteEventStore",
    "SqliteCognitionStore",
    "SqliteInteractionContextStore",
    "hash_context",
    "SqliteSemanticResolutionStore",
]
