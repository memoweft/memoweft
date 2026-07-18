"""SQLite 持久化 schema，与 TypeScript store 的完整新库结构保持一致。

共享 parity 资产验证 fresh :memory: 库与 openStores 的等效结构：
  evidence            ← src/evidence/store.ts
  event/event_evidence← src/event/store.ts
  cognition/…evidence ← src/cognition/store.ts
  management_log      ← src/memory/managementLog.ts
  interaction_context ← src/interaction/interactionContextStore.ts
  semantic_resolution ← src/interaction/semanticResolutionStore.ts

注:preceding_ai_context / asked_at / archived_at / muted_at 在 TS 里 fresh 库由 SCHEMA 常量直接带全,
  旧数据库由各 store 的 migrate() 补列；Python 新建数据库时直接按 SCHEMA 建立完整列集。
LATEST_SCHEMA_VERSION = 1(store/migrations.ts);新库 user_version 盖 1。
"""
from __future__ import annotations

#: PRAGMA user_version，与 TS LATEST_SCHEMA_VERSION 保持一致。
SCHEMA_VERSION = 1

#: 幂等的建表与索引 DDL；shared/parity/schema.json 验证列序、NOT NULL、DEFAULT 与主键契约。
SCHEMA_SQL: tuple[str, ...] = (
    # ── evidence(唯一真相层)──
    """CREATE TABLE IF NOT EXISTS evidence (
  id                   TEXT    PRIMARY KEY,
  subject_id           TEXT    NOT NULL,
  source_kind          TEXT    NOT NULL,
  host_id              TEXT    NOT NULL,
  origin_id            TEXT,
  occurred_at          TEXT    NOT NULL,
  recorded_at          TEXT    NOT NULL,
  raw_content          TEXT    NOT NULL,
  summary              TEXT    NOT NULL,
  allow_local_read     INTEGER NOT NULL,
  allow_cloud_read     INTEGER NOT NULL,
  allow_inference      INTEGER NOT NULL,
  corrects_evidence_id TEXT,
  preceding_ai_context TEXT
)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_evidence_origin ON evidence(origin_id) WHERE origin_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_evidence_occurred ON evidence(occurred_at)",
    # ── event ──
    """CREATE TABLE IF NOT EXISTS event (
  id           TEXT PRIMARY KEY,
  subject_id   TEXT NOT NULL,
  summary      TEXT NOT NULL,
  occurred_at  TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  consolidated INTEGER NOT NULL DEFAULT 0
)""",
    "CREATE INDEX IF NOT EXISTS ix_event_subject ON event(subject_id)",
    """CREATE TABLE IF NOT EXISTS event_evidence (
  event_id    TEXT NOT NULL,
  evidence_id TEXT NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS ix_evev_event ON event_evidence(event_id)",
    # ── cognition(判断层)──
    """CREATE TABLE IF NOT EXISTS cognition (
  id           TEXT    PRIMARY KEY,
  subject_id   TEXT    NOT NULL,
  content      TEXT    NOT NULL,
  content_type TEXT    NOT NULL,
  formed_by    TEXT    NOT NULL,
  confidence   INTEGER NOT NULL,
  cred_status  TEXT    NOT NULL,
  scope        TEXT,
  valid_at     TEXT,
  invalid_at   TEXT,
  asked_at     TEXT,
  archived_at  TEXT,
  muted_at     TEXT,
  created_at   TEXT    NOT NULL,
  updated_at   TEXT    NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS ix_cognition_subject ON cognition(subject_id)",
    """CREATE TABLE IF NOT EXISTS cognition_evidence (
  cognition_id TEXT NOT NULL,
  evidence_id  TEXT NOT NULL,
  relation     TEXT NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS ix_cogev_cog ON cognition_evidence(cognition_id)",
    # ── management_log(审计,无 PK) ──
    """CREATE TABLE IF NOT EXISTS management_log (
  op          TEXT NOT NULL,
  target_kind TEXT NOT NULL,
  target_id   TEXT NOT NULL,
  reason      TEXT NOT NULL,
  detail      TEXT,
  created_at  TEXT NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS ix_mgmt_target ON management_log(target_id)",
    # ── interaction_context（v0.6） ──
    """CREATE TABLE IF NOT EXISTS interaction_context (
  id              TEXT PRIMARY KEY,
  subject_id      TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  episode_id      TEXT NOT NULL,
  context_json    TEXT NOT NULL,
  context_hash    TEXT NOT NULL,
  created_at      TEXT NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS ix_ictx_subject ON interaction_context(subject_id)",
    "CREATE INDEX IF NOT EXISTS ix_ictx_conversation ON interaction_context(conversation_id)",
    "CREATE INDEX IF NOT EXISTS ix_ictx_hash ON interaction_context(context_hash)",
    # ── semantic_resolution（v0.6） ──
    """CREATE TABLE IF NOT EXISTS semantic_resolution (
  id                 TEXT PRIMARY KEY,
  evidence_id        TEXT NOT NULL,
  resolved_content   TEXT NOT NULL,
  response_act       TEXT,
  prompt_act         TEXT,
  proposition_origin TEXT,
  assertion_strength TEXT,
  required_context   TEXT,
  resolver_version   TEXT NOT NULL,
  created_at         TEXT NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS ix_semres_evidence ON semantic_resolution(evidence_id)",
)
