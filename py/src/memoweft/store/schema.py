"""SQLite 持久化 schema。

共享 parity 资产只验证 Python 与 TypeScript 仍共享的 1.x 表结构：
  evidence            ← src/evidence/store.ts
  event/event_evidence← src/event/store.ts
  cognition/…evidence ← src/cognition/store.ts
  management_log      ← src/memory/managementLog.ts
  interaction_context ← src/interaction/interactionContextStore.ts
  semantic_resolution ← src/interaction/semanticResolutionStore.ts
  memory_state / evidence_ledger / proposals / cognition_transitions
                      ← memoweft.world.loop.MemoryLoop
  identity_state      ← memoweft.world.identity_store.SqliteIdentityStore

注:preceding_ai_context / asked_at / archived_at / muted_at 在 TS 里 fresh 库由 SCHEMA 常量直接带全,
  旧数据库由各 store 的 migrate() 补列；Python 新建数据库时直接按 SCHEMA 建立完整列集。

从 v7 起 Hermes 使用的 MemoWeft 库是 Python-owned：TypeScript/shared parity 仍停在 v6，
Python v7 以独立 ``application_id`` 和 durable World Job 物理表明确区分，不能混用。
"""
from __future__ import annotations

#: Python-owned SQLite 文件标识（ASCII ``MWPY``）。v6 旧库尚未设置，v7 migration 会原子盖入。
PYTHON_APPLICATION_ID = 0x4D575059

#: Python-owned ``PRAGMA user_version``。TypeScript/shared parity 的版本仍为 6。
SCHEMA_VERSION = 20

#: 幂等的建表与索引 DDL；shared/parity/schema.json 验证列序、NOT NULL、DEFAULT 与主键契约。
BASE_SCHEMA_SQL: tuple[str, ...] = (
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
  deleted_at           TEXT,
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
    # ── evidence_retraction（旧版软删撤回台账；v2 升级会清理其派生认知）──
    """CREATE TABLE IF NOT EXISTS evidence_retraction (
  cognition_id TEXT NOT NULL,
  evidence_id  TEXT NOT NULL,
  retracted_at TEXT NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS ix_evret_cog ON evidence_retraction(cognition_id)",
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

#: MemoWeft Next 2.0 在 1.0 主库上追加的持久化表。这里既是 fresh schema，
#: 也是 v3 migration 的 DDL 来源，MemoryLoop 与 identity store 复用同一形状。
MEMORY_LOOP_SCHEMA_SQL: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS memory_state (
  singleton    INTEGER PRIMARY KEY CHECK(singleton = 1),
  revision     INTEGER NOT NULL,
  snapshot_json TEXT    NOT NULL,
  snapshot_hash TEXT    NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS evidence_ledger (
  id           TEXT PRIMARY KEY,
  content      TEXT NOT NULL,
  payload_json TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS proposals (
  id                  TEXT PRIMARY KEY,
  kind                TEXT NOT NULL,
  base_revision       INTEGER NOT NULL,
  result_hash         TEXT NOT NULL,
  payload_json        TEXT NOT NULL,
  review_payload_json TEXT,
  status              TEXT NOT NULL CHECK(status IN ('pending', 'accept', 'reject'))
)""",
    # A terminal decision is an immutable historical fact.  ``proposals`` only
    # retains a mutable status, so it cannot by itself tell a retry which world
    # revision/hash the original decision committed after later decisions move
    # the current world forward.
    """CREATE TABLE IF NOT EXISTS proposal_decision_receipts (
  proposal_id          TEXT PRIMARY KEY,
  offered_result_hash  TEXT NOT NULL,
  effective_decision   TEXT NOT NULL CHECK(effective_decision IN ('accept', 'reject')),
  world_revision       INTEGER NOT NULL CHECK(world_revision >= 0),
  snapshot_hash        TEXT NOT NULL,
  decided_at           TEXT NOT NULL,
  receipt_hash         TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS cognition_transitions (
  id                         TEXT PRIMARY KEY,
  prior_cognition_id         TEXT NOT NULL UNIQUE,
  replacement_cognition_id   TEXT NOT NULL,
  reason                     TEXT NOT NULL,
  revision                   INTEGER NOT NULL
)""",
)

IDENTITY_SCHEMA_SQL: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS identity_state (
  singleton             INTEGER PRIMARY KEY CHECK(singleton = 1),
  identity_schema_version INTEGER NOT NULL,
  world_id              TEXT    NOT NULL,
  memory_revision       INTEGER NOT NULL CHECK(memory_revision >= 0),
  memory_snapshot_hash  TEXT    NOT NULL,
  identity_graph_hash   TEXT    NOT NULL,
  state_json            TEXT    NOT NULL,
  state_hash            TEXT    NOT NULL,
  storage_generation    INTEGER NOT NULL CHECK(storage_generation >= 1)
)""",
)

#: 2.0 的完整追加结构。
WORLD_SCHEMA_SQL: tuple[str, ...] = MEMORY_LOOP_SCHEMA_SQL + IDENTITY_SCHEMA_SQL

#: Python v7 durable World Job。它不属于 TypeScript parity，也不能追加进冻结的 v3
#: ``WORLD_SCHEMA_SQL`` migration 来源；v6 -> v7 只执行这一组 DDL。
WORLD_JOB_SCHEMA_SQL: tuple[str, ...] = (
    """CREATE TABLE memory_world_job (
  job_id                    TEXT    PRIMARY KEY,
  job_schema_version        INTEGER NOT NULL CHECK(job_schema_version = 1),
  boundary_event_id         TEXT    NOT NULL,
  boundary_payload_hash     TEXT    NOT NULL,
  boundary_schema_version   INTEGER NOT NULL CHECK(boundary_schema_version >= 1),
  provider_name             TEXT    NOT NULL,
  parent_session_id         TEXT    NOT NULL,
  result_session_id         TEXT    NOT NULL,
  boundary_mode             TEXT    NOT NULL CHECK(boundary_mode IN ('in_place', 'rotation')),
  formal_target_json        TEXT    NOT NULL,
  formal_target_hash        TEXT    NOT NULL,
  subject_id                TEXT    NOT NULL,
  host_id                   TEXT    NOT NULL,
  evidence_ids_json         TEXT    NOT NULL,
  state                     TEXT    NOT NULL CHECK(state IN (
                              'pending', 'processing', 'applied',
                              'no_change', 'retry', 'dead'
                            )),
  attempts                  INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
  next_attempt_at           TEXT,
  claim_owner               TEXT,
  claim_token               TEXT,
  claimed_at                TEXT,
  lease_expires_at          TEXT,
  heartbeat_at              TEXT,
  fencing_generation        INTEGER NOT NULL DEFAULT 0 CHECK(fencing_generation >= 0),
  model_dispatch_started_at TEXT,
  model_completed_at        TEXT,
  model_task                TEXT,
  model_provider            TEXT,
  model_name                TEXT,
  model_usage_json          TEXT,
  model_result_json         TEXT,
  model_result_hash         TEXT,
  world_result_json         TEXT,
  result_hash               TEXT,
  delivery_receipt_json     TEXT    NOT NULL,
  delivery_receipt_hash     TEXT    NOT NULL,
  created_at                TEXT    NOT NULL,
  completed_at              TEXT,
  last_error_type           TEXT,
  terminal_state            TEXT,
  terminal_detail           TEXT,
  CHECK (
    (
      state = 'processing'
      AND claim_owner IS NOT NULL
      AND claim_token IS NOT NULL
      AND claimed_at IS NOT NULL
      AND lease_expires_at IS NOT NULL
      AND heartbeat_at IS NOT NULL
    )
    OR
    (
      state <> 'processing'
      AND claim_owner IS NULL
      AND claim_token IS NULL
      AND lease_expires_at IS NULL
    )
  ),
  CHECK (
    (state IN ('applied', 'no_change', 'dead') AND completed_at IS NOT NULL)
    OR
    (state IN ('pending', 'processing', 'retry') AND completed_at IS NULL)
  )
)""",
    """CREATE UNIQUE INDEX ux_memory_world_job_boundary_event
ON memory_world_job(boundary_event_id)""",
    """CREATE INDEX ix_memory_world_job_ready
ON memory_world_job(next_attempt_at, created_at, job_id)
WHERE state IN ('pending', 'retry')""",
    """CREATE INDEX ix_memory_world_job_expired
ON memory_world_job(lease_expires_at, created_at, job_id)
WHERE state = 'processing'""",
)

#: 列序是 store/worker 与 schema validation 的闭合契约；JSON 字段由代码 canonicalize，
#: 不依赖所有部署环境都具备 SQLite JSON1。
MEMORY_WORLD_JOB_COLUMNS: tuple[str, ...] = (
    "job_id",
    "job_schema_version",
    "boundary_event_id",
    "boundary_payload_hash",
    "boundary_schema_version",
    "provider_name",
    "parent_session_id",
    "result_session_id",
    "boundary_mode",
    "formal_target_json",
    "formal_target_hash",
    "subject_id",
    "host_id",
    "evidence_ids_json",
    "state",
    "attempts",
    "next_attempt_at",
    "claim_owner",
    "claim_token",
    "claimed_at",
    "lease_expires_at",
    "heartbeat_at",
    "fencing_generation",
    "model_dispatch_started_at",
    "model_completed_at",
    "model_task",
    "model_provider",
    "model_name",
    "model_usage_json",
    "model_result_json",
    "model_result_hash",
    "world_result_json",
    "result_hash",
    "delivery_receipt_json",
    "delivery_receipt_hash",
    "created_at",
    "completed_at",
    "last_error_type",
    "terminal_state",
    "terminal_detail",
)

#: v15 adds AUTHORITY §3 terminal observability to the World Job: the transport
#: ``state`` machine stays closed, while ``terminal_state`` records the five
#: observable terminals (applied / no_change / clarification_required /
#: out_of_scope / failed) and ``terminal_detail`` carries the human-facing
#: clarification question or out-of-scope note.  Both columns are LAST in the
#: CREATE so ALTER-migrated tables have the identical column order.
WORLD_JOB_ALTER_V15_SQL: tuple[str, ...] = (
    "ALTER TABLE memory_world_job ADD COLUMN terminal_state TEXT",
    "ALTER TABLE memory_world_job ADD COLUMN terminal_detail TEXT",
)

#: Python v16 durable terminal outcome.  This is deliberately separate from
#: ``memory_world_job``: the latter is Core's formation/worker transport state,
#: while this table is the immutable business terminal plus independently
#: recoverable Hermes delivery state.  The row is created in the same caller-
#: owned transaction that commits a terminal job, so a visible terminal never
#: lacks an outcome and delivery failure can never rewrite the business result.
TERMINAL_OUTCOME_SCHEMA_SQL: tuple[str, ...] = (
    """CREATE TABLE terminal_outcome (
  outcome_id            TEXT    PRIMARY KEY,
  schema_version        INTEGER NOT NULL CHECK(schema_version = 1),
  job_id                TEXT    NOT NULL UNIQUE,
  boundary_event_id     TEXT    NOT NULL,
  provider_name         TEXT    NOT NULL,
  subject_id            TEXT    NOT NULL,
  parent_session_id     TEXT    NOT NULL,
  result_session_id     TEXT    NOT NULL,
  terminal_state        TEXT    NOT NULL CHECK(terminal_state IN (
                            'applied', 'no_change', 'clarification_required',
                            'out_of_scope', 'failed'
                          )),
  terminal_detail       TEXT,
  world_revision        INTEGER NOT NULL CHECK(world_revision >= 0),
  world_result_json     TEXT    NOT NULL,
  result_hash           TEXT    NOT NULL,
  occurred_at           TEXT    NOT NULL,
  delivery_state        TEXT    NOT NULL CHECK(delivery_state IN (
                            'pending', 'processing', 'delivered', 'retry', 'dead'
                          )),
  attempts              INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
  next_attempt_at       TEXT,
  claim_owner           TEXT,
  claim_token           TEXT,
  lease_expires_at      TEXT,
  heartbeat_at          TEXT,
  delivered_at          TEXT,
  last_error            TEXT,
  CHECK (
    (delivery_state = 'processing'
      AND claim_owner IS NOT NULL AND claim_token IS NOT NULL
      AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL
      AND next_attempt_at IS NULL AND delivered_at IS NULL)
    OR
    (delivery_state <> 'processing'
      AND claim_owner IS NULL AND claim_token IS NULL
      AND lease_expires_at IS NULL AND heartbeat_at IS NULL)
  ),
  CHECK (
    (delivery_state = 'pending'
      AND next_attempt_at IS NULL AND delivered_at IS NULL)
    OR (delivery_state = 'retry'
      AND next_attempt_at IS NOT NULL AND delivered_at IS NULL)
    OR (delivery_state = 'delivered'
      AND next_attempt_at IS NULL AND delivered_at IS NOT NULL)
    OR (delivery_state = 'dead'
      AND next_attempt_at IS NULL AND delivered_at IS NULL)
    OR delivery_state = 'processing'
  )
)""",
    """CREATE INDEX ix_terminal_outcome_ready
ON terminal_outcome(next_attempt_at, occurred_at, outcome_id)
WHERE delivery_state IN ('pending', 'retry')""",
    """CREATE INDEX ix_terminal_outcome_expired
ON terminal_outcome(lease_expires_at, occurred_at, outcome_id)
WHERE delivery_state = 'processing'""",
)

TERMINAL_OUTCOME_COLUMNS: tuple[str, ...] = (
    "outcome_id",
    "schema_version",
    "job_id",
    "boundary_event_id",
    "provider_name",
    "subject_id",
    "parent_session_id",
    "result_session_id",
    "terminal_state",
    "terminal_detail",
    "world_revision",
    "world_result_json",
    "result_hash",
    "occurred_at",
    "delivery_state",
    "attempts",
    "next_attempt_at",
    "claim_owner",
    "claim_token",
    "lease_expires_at",
    "heartbeat_at",
    "delivered_at",
    "last_error",
)

TERMINAL_OUTCOME_SCHEMA_OBJECTS = frozenset(
    {
        "terminal_outcome",
        "ix_terminal_outcome_ready",
        "ix_terminal_outcome_expired",
    }
)

#: Python v17 Trust Command ledger. A command row freezes the canonical
#: request identity; its one receipt is the durable idempotency/result fact.
#: ``world_item_lifecycle`` supplies one cross-kind archive/mute authority
#: without altering the already-shipped Entity/Relationship/Event tables.
TRUST_COMMAND_SCHEMA_SQL: tuple[str, ...] = (
    """CREATE TABLE trust_command (
  command_id               TEXT    PRIMARY KEY,
  schema_version           INTEGER NOT NULL CHECK(schema_version = 1),
  subject_id               TEXT    NOT NULL,
  actor                    TEXT    NOT NULL,
  expected_world_revision  INTEGER NOT NULL CHECK(expected_world_revision >= 0),
  operation                TEXT    NOT NULL CHECK(operation IN (
                               'update_evidence_permissions',
                               'correct_world_item', 'retract_world_item',
                               'forget_evidence', 'delete_evidence',
                               'delete_world_item', 'archive_world_item',
                               'mute_world_item'
                             )),
  target_kind              TEXT    NOT NULL CHECK(target_kind IN (
                               'evidence', 'entity', 'relationship',
                               'event', 'cognition'
                             )),
  target_id                TEXT    NOT NULL,
  payload_json             TEXT    NOT NULL,
  request_hash             TEXT    NOT NULL,
  submitted_at             TEXT    NOT NULL
)""",
    """CREATE INDEX ix_trust_command_subject
ON trust_command(subject_id, submitted_at, command_id)""",
    """CREATE TABLE trust_command_receipt (
  command_id        TEXT    PRIMARY KEY,
  schema_version    INTEGER NOT NULL CHECK(schema_version = 1),
  accepted          INTEGER NOT NULL CHECK(accepted IN (0, 1)),
  result_state      TEXT    NOT NULL CHECK(result_state IN (
                        'applied', 'no_change',
                        'revision_conflict', 'rejected'
                      )),
  before_revision   INTEGER NOT NULL CHECK(before_revision >= 0),
  after_revision    INTEGER NOT NULL CHECK(after_revision >= 0),
  affected_ids_json TEXT    NOT NULL,
  transition_ids_json TEXT  NOT NULL,
  result_hash       TEXT    NOT NULL,
  completed_at      TEXT    NOT NULL
)""",
    """CREATE TABLE world_item_lifecycle (
  subject_id   TEXT NOT NULL,
  object_kind  TEXT NOT NULL CHECK(object_kind IN (
                   'entity', 'relationship', 'event', 'cognition'
                 )),
  item_id      TEXT NOT NULL,
  archived_at  TEXT,
  muted_at     TEXT,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY(subject_id, object_kind, item_id)
)""",
    """CREATE INDEX ix_world_item_lifecycle_current
ON world_item_lifecycle(subject_id, object_kind, archived_at, muted_at, item_id)""",
)

# v20 keeps rejection reasons separate so v17 receipts remain byte-for-byte
# valid and their historical hashes need no reinterpretation.
TRUST_REJECTION_SCHEMA_SQL: tuple[str, ...] = (
    """CREATE TABLE trust_command_rejection (
  command_id TEXT PRIMARY KEY,
  reason_code TEXT NOT NULL
)""",
    """CREATE TABLE world_delete_marker (
  subject_id TEXT NOT NULL,
  object_kind TEXT NOT NULL,
  item_id TEXT NOT NULL,
  deleted_at TEXT NOT NULL,
  PRIMARY KEY(subject_id, object_kind, item_id)
)""",
    """CREATE TABLE trust_delete_storage_status (
  command_id TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK(state IN ('pending', 'complete')),
  detail_code TEXT NOT NULL
)""",
    """CREATE TABLE evidence_origin_history (
  evidence_id TEXT PRIMARY KEY,
  origin_id TEXT NOT NULL
)""",
    """CREATE TABLE hard_deleted_origin (
  origin_hash TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  evidence_id TEXT NOT NULL
)""",
)
TRUST_REJECTION_SCHEMA_OBJECTS = frozenset({
    "trust_command_rejection", "world_delete_marker", "trust_delete_storage_status",
    "evidence_origin_history", "hard_deleted_origin"
})

TRUST_COMMAND_COLUMNS: tuple[str, ...] = (
    "command_id",
    "schema_version",
    "subject_id",
    "actor",
    "expected_world_revision",
    "operation",
    "target_kind",
    "target_id",
    "payload_json",
    "request_hash",
    "submitted_at",
)

TRUST_COMMAND_RECEIPT_COLUMNS: tuple[str, ...] = (
    "command_id",
    "schema_version",
    "accepted",
    "result_state",
    "before_revision",
    "after_revision",
    "affected_ids_json",
    "transition_ids_json",
    "result_hash",
    "completed_at",
)

WORLD_ITEM_LIFECYCLE_COLUMNS: tuple[str, ...] = (
    "subject_id",
    "object_kind",
    "item_id",
    "archived_at",
    "muted_at",
    "updated_at",
)

TRUST_COMMAND_SCHEMA_OBJECTS = frozenset(
    {
        "trust_command",
        "ix_trust_command_subject",
        "trust_command_receipt",
        "world_item_lifecycle",
        "ix_world_item_lifecycle_current",
    }
)

#: Python v18 durable clarification lifecycle. The source terminal outcome is
#: immutable; one answer may atomically create one Evidence row and one
#: follow-up World Job. A repeated clarification is represented by a new row,
#: while the answered source row moves to ``resolved``.
CLARIFICATION_SCHEMA_SQL: tuple[str, ...] = (
    """CREATE TABLE clarification (
  clarification_id    TEXT PRIMARY KEY,
  source_job_id       TEXT NOT NULL UNIQUE,
  source_outcome_id   TEXT NOT NULL UNIQUE,
  subject_id          TEXT NOT NULL,
  result_session_id   TEXT NOT NULL,
  question            TEXT NOT NULL,
  target_hint         TEXT,
  state               TEXT NOT NULL CHECK(state IN ('open', 'answered', 'resolved')),
  answer_evidence_id  TEXT,
  follow_up_job_id    TEXT UNIQUE,
  opened_at           TEXT NOT NULL,
  answered_at         TEXT,
  resolved_at         TEXT,
  CHECK (
    (state = 'open'
      AND answer_evidence_id IS NULL AND follow_up_job_id IS NULL
      AND answered_at IS NULL AND resolved_at IS NULL)
    OR (state = 'answered'
      AND answer_evidence_id IS NOT NULL AND follow_up_job_id IS NOT NULL
      AND answered_at IS NOT NULL AND resolved_at IS NULL)
    OR (state = 'resolved'
      AND answer_evidence_id IS NOT NULL AND follow_up_job_id IS NOT NULL
      AND answered_at IS NOT NULL AND resolved_at IS NOT NULL)
  )
)""",
    """CREATE INDEX ix_clarification_session_state
ON clarification(subject_id, result_session_id, state, opened_at, clarification_id)""",
    """CREATE INDEX ix_clarification_follow_up
ON clarification(follow_up_job_id)""",
)

CLARIFICATION_COLUMNS: tuple[str, ...] = (
    "clarification_id",
    "source_job_id",
    "source_outcome_id",
    "subject_id",
    "result_session_id",
    "question",
    "target_hint",
    "state",
    "answer_evidence_id",
    "follow_up_job_id",
    "opened_at",
    "answered_at",
    "resolved_at",
)

CLARIFICATION_SCHEMA_OBJECTS = frozenset(
    {
        "clarification",
        "ix_clarification_session_state",
        "ix_clarification_follow_up",
    }
)

#: Python v19 Portable v4 apply receipt.  The importer writes this row in the
#: same transaction as all imported World/history rows and the target revision
#: advance.  A process restart can therefore return the immutable original
#: result by plan/command/receipt identity without replanning against a changed
#: target database.
PORTABLE_IMPORT_RECEIPT_SCHEMA_SQL: tuple[str, ...] = (
    """CREATE TABLE portable_import_receipt (
  receipt_id             TEXT    PRIMARY KEY,
  schema_version         INTEGER NOT NULL CHECK(schema_version = 1),
  command_id             TEXT    NOT NULL UNIQUE,
  plan_hash              TEXT    NOT NULL UNIQUE,
  bundle_id              TEXT    NOT NULL,
  source_subject_id      TEXT    NOT NULL,
  target_subject_id      TEXT    NOT NULL,
  target_world_revision  INTEGER NOT NULL CHECK(target_world_revision >= 0),
  target_snapshot_hash   TEXT    NOT NULL,
  after_world_revision   INTEGER NOT NULL CHECK(after_world_revision >= 0),
  result_state           TEXT    NOT NULL CHECK(result_state IN ('applied', 'no_change')),
  result_json            TEXT    NOT NULL,
  result_hash            TEXT    NOT NULL,
  completed_at           TEXT    NOT NULL
)""",
    """CREATE INDEX ix_portable_import_receipt_target
ON portable_import_receipt(target_subject_id, completed_at, receipt_id)""",
)

PORTABLE_IMPORT_RECEIPT_COLUMNS: tuple[str, ...] = (
    "receipt_id",
    "schema_version",
    "command_id",
    "plan_hash",
    "bundle_id",
    "source_subject_id",
    "target_subject_id",
    "target_world_revision",
    "target_snapshot_hash",
    "after_world_revision",
    "result_state",
    "result_json",
    "result_hash",
    "completed_at",
)

PORTABLE_IMPORT_RECEIPT_SCHEMA_OBJECTS = frozenset(
    {
        "portable_import_receipt",
        "ix_portable_import_receipt_target",
    }
)

#: v6 Python-owned 物理 marker。特别是 ``proposal_decision_receipts`` 不存在于 TS v6。
PYTHON_V6_REQUIRED_SCHEMA_OBJECTS = frozenset(
    {
        "evidence",
        "event",
        "event_evidence",
        "cognition",
        "cognition_evidence",
        "evidence_retraction",
        "management_log",
        "interaction_context",
        "semantic_resolution",
        "memory_state",
        "evidence_ledger",
        "proposals",
        "proposal_decision_receipts",
        "cognition_transitions",
        "identity_state",
        "ux_evidence_origin",
        "ix_evidence_occurred",
        "ix_event_subject",
        "ix_evev_event",
        "ix_cognition_subject",
        "ix_cogev_cog",
        "ix_evret_cog",
        "ix_mgmt_target",
        "ix_ictx_subject",
        "ix_ictx_conversation",
        "ix_ictx_hash",
        "ix_semres_evidence",
    }
)

WORLD_JOB_SCHEMA_OBJECTS = frozenset(
    {
        "memory_world_job",
        "ux_memory_world_job_boundary_event",
        "ix_memory_world_job_ready",
        "ix_memory_world_job_expired",
    }
)
#: Python v8 per-row raw-content hash binding for boundary evidence. Python-owned
#: (not TS parity), keeps the frozen 1.x ``evidence`` table untouched; the formal
#: batch compiler re-verifies the hash before the model call and again before
#: Apply, so a worker can never interpret text that differs from the bytes the
#: boundary actually accepted.
BOUNDARY_EVIDENCE_CONTENT_SCHEMA_SQL: tuple[str, ...] = (
    """CREATE TABLE boundary_evidence_content (
  evidence_id      TEXT    PRIMARY KEY,
  raw_content_hash TEXT    NOT NULL
)""",
)

#: 列序是 boundary_store/worker 与 schema validation 的闭合契约。
BOUNDARY_EVIDENCE_CONTENT_COLUMNS: tuple[str, ...] = (
    "evidence_id",
    "raw_content_hash",
)

BOUNDARY_EVIDENCE_CONTENT_SCHEMA_OBJECTS = frozenset(
    {
        "boundary_evidence_content",
    }
)

#: Python v9 first-class Entity + Relationship World objects (V3 window).
#: Python-owned (not TS parity); frozen 1.x tables and v3 WORLD_SCHEMA_SQL stay
#: untouched.  ``entity`` is the open-type mention→identity anchor (deterministic
#: id from canonical_name → same-name re-mention is idempotent, same-name
#: conflicts are structurally zero-write); ``relationship`` is a first-class
#: owner-perspective relationship row with its own evidence chain.
ENTITY_RELATIONSHIP_SCHEMA_SQL: tuple[str, ...] = (
    """CREATE TABLE entity (
  id             TEXT    PRIMARY KEY,
  world_id       TEXT    NOT NULL,
  kind           TEXT    NOT NULL,
  canonical_name TEXT    NOT NULL,
  invalid_at     TEXT,
  created_at     TEXT    NOT NULL,
  updated_at     TEXT    NOT NULL,
  aliases_json   TEXT    NOT NULL DEFAULT '[]'
)""",
    """CREATE UNIQUE INDEX ux_entity_world_name
ON entity(world_id, canonical_name) WHERE invalid_at IS NULL""",
    """CREATE TABLE relationship (
  id               TEXT    PRIMARY KEY,
  world_id         TEXT    NOT NULL,
  source_entity_id TEXT    NOT NULL,
  target_entity_id TEXT    NOT NULL,
  relation_type    TEXT    NOT NULL,
  content          TEXT    NOT NULL,
  formed_by        TEXT    NOT NULL,
  confidence       INTEGER NOT NULL,
  cred_status      TEXT    NOT NULL,
  invalid_at       TEXT,
  created_at       TEXT    NOT NULL,
  updated_at       TEXT    NOT NULL
)""",
    "CREATE INDEX ix_relationship_world ON relationship(world_id)",
    "CREATE INDEX ix_relationship_target ON relationship(target_entity_id)",
    """CREATE TABLE relationship_evidence (
  relationship_id TEXT NOT NULL,
  evidence_id     TEXT NOT NULL,
  relation        TEXT NOT NULL
)""",
    "CREATE INDEX ix_relev_rel ON relationship_evidence(relationship_id)",
)

#: 列序是 schema validation 的闭合契约。aliases_json 必须位于末尾：
#: 迁移路径用 ALTER ADD COLUMN（只能追加），新鲜 CREATE 与其列序一致。
ENTITY_COLUMNS: tuple[str, ...] = (
    "id",
    "world_id",
    "kind",
    "canonical_name",
    "invalid_at",
    "created_at",
    "updated_at",
    "aliases_json",
)

RELATIONSHIP_COLUMNS: tuple[str, ...] = (
    "id",
    "world_id",
    "source_entity_id",
    "target_entity_id",
    "relation_type",
    "content",
    "formed_by",
    "confidence",
    "cred_status",
    "invalid_at",
    "created_at",
    "updated_at",
)

ENTITY_RELATIONSHIP_SCHEMA_OBJECTS = frozenset(
    {
        "entity",
        "ux_entity_world_name",
        "relationship",
        "ix_relationship_world",
        "ix_relationship_target",
        "relationship_evidence",
        "ix_relev_rel",
    }
)

#: Python v10 targeted-cognition sidecar: the frozen 1.x ``cognition`` table
#: stays untouched; rows exist only for cognitions whose semantic target is a
#: third-party entity (absent row == owner_self target, preserving V1/V2/V3
#: semantics).  Mirrors the v8 content-binding sidecar precedent.  v14 adds
#: ``perspective_entity_id`` (V5 perspective-holder slice, Owner decision
#: 2026-08-16): absent == owner_self holder.  The column is LAST so
#: ALTER-migrated v10 tables have the identical column order.
COGNITION_TARGET_SCHEMA_SQL: tuple[str, ...] = (
    """CREATE TABLE cognition_target (
  cognition_id         TEXT PRIMARY KEY,
  target_entity_id     TEXT NOT NULL,
  perspective_entity_id TEXT
)""",
    "CREATE INDEX ix_cogtarget_target ON cognition_target(target_entity_id)",
)

COGNITION_TARGET_ALTER_V14_SQL: tuple[str, ...] = (
    "ALTER TABLE cognition_target ADD COLUMN perspective_entity_id TEXT",
)

COGNITION_TARGET_COLUMNS: tuple[str, ...] = (
    "cognition_id",
    "target_entity_id",
    "perspective_entity_id",
)

COGNITION_TARGET_SCHEMA_OBJECTS = frozenset(
    {
        "cognition_target",
        "ix_cogtarget_target",
    }
)

#: Python v11 alias-merge DDL: ``entity.aliases_json`` (v9 Python-owned table
#: — additive column migration, existing rows default to '[]').  The frozen
#: 1.x tables stay untouched.
ENTITY_ALIAS_SCHEMA_SQL: tuple[str, ...] = (
    "ALTER TABLE entity ADD COLUMN aliases_json TEXT NOT NULL DEFAULT '[]'",
)

#: Python v13 retraction shape (used by the v12 migration step AND fresh
#: creates): adds ``prior_event_id`` for event retracts (Owner decision
#: 2026-08-16: events support retract; correct stays later).  The column is
#: LAST in the CREATE so ALTER-migrated v12 tables have the identical column
#: order (ALTER only appends).
RETRACTION_SCHEMA_SQL: tuple[str, ...] = (
    """CREATE TABLE retraction (
  id                   TEXT PRIMARY KEY,
  prior_cognition_id   TEXT,
  prior_relationship_id TEXT,
  reason               TEXT NOT NULL,
  revision             INTEGER NOT NULL,
  created_at           TEXT NOT NULL,
  prior_event_id       TEXT
)""",
    "CREATE INDEX ix_retraction_prior ON retraction(prior_cognition_id, prior_relationship_id)",
)

RETRACTION_ALTER_V13_SQL: tuple[str, ...] = (
    "ALTER TABLE retraction ADD COLUMN prior_event_id TEXT",
)

RETRACTION_COLUMNS: tuple[str, ...] = (
    "id",
    "prior_cognition_id",
    "prior_relationship_id",
    "reason",
    "revision",
    "created_at",
    "prior_event_id",
)

RETRACTION_SCHEMA_OBJECTS = frozenset(
    {
        "retraction",
        "ix_retraction_prior",
    }
)

#: Python v13 first-class World Event (authority §2.3/§4.3): readable narrative
#: + queryable participants/objects/time/provenance.  The frozen 1.x ``event``
#: table stays untouched; this is a separate Python-owned object.
WORLD_EVENT_SCHEMA_SQL: tuple[str, ...] = (
    """CREATE TABLE world_event (
  id                TEXT    PRIMARY KEY,
  world_id          TEXT    NOT NULL,
  content           TEXT    NOT NULL,
  occurred_at       TEXT,
  time_expression   TEXT,
  participants_json TEXT    NOT NULL DEFAULT '[]',
  objects_json      TEXT    NOT NULL DEFAULT '[]',
  formed_by         TEXT    NOT NULL,
  confidence        INTEGER NOT NULL,
  cred_status       TEXT    NOT NULL,
  invalid_at        TEXT,
  created_at        TEXT    NOT NULL,
  updated_at        TEXT    NOT NULL
)""",
    "CREATE INDEX ix_world_event_world ON world_event(world_id)",
    "CREATE INDEX ix_world_event_occurred ON world_event(occurred_at)",
    """CREATE TABLE world_event_evidence (
  world_event_id TEXT NOT NULL,
  evidence_id    TEXT NOT NULL,
  relation       TEXT NOT NULL
)""",
    "CREATE INDEX ix_wev_evidence ON world_event_evidence(world_event_id)",
)

WORLD_EVENT_COLUMNS: tuple[str, ...] = (
    "id",
    "world_id",
    "content",
    "occurred_at",
    "time_expression",
    "participants_json",
    "objects_json",
    "formed_by",
    "confidence",
    "cred_status",
    "invalid_at",
    "created_at",
    "updated_at",
)

WORLD_EVENT_SCHEMA_OBJECTS = frozenset(
    {
        "world_event",
        "ix_world_event_world",
        "ix_world_event_occurred",
        "world_event_evidence",
        "ix_wev_evidence",
    }
)

CURRENT_REQUIRED_SCHEMA_OBJECTS = (
    PYTHON_V6_REQUIRED_SCHEMA_OBJECTS
    | WORLD_JOB_SCHEMA_OBJECTS
    | BOUNDARY_EVIDENCE_CONTENT_SCHEMA_OBJECTS
    | ENTITY_RELATIONSHIP_SCHEMA_OBJECTS
    | COGNITION_TARGET_SCHEMA_OBJECTS
    | RETRACTION_SCHEMA_OBJECTS
    | WORLD_EVENT_SCHEMA_OBJECTS
    | TERMINAL_OUTCOME_SCHEMA_OBJECTS
    | TRUST_COMMAND_SCHEMA_OBJECTS
    | TRUST_REJECTION_SCHEMA_OBJECTS
    | CLARIFICATION_SCHEMA_OBJECTS
    | PORTABLE_IMPORT_RECEIPT_SCHEMA_OBJECTS
)

#: Fresh Python schema。旧 v3/v6 DDL 保持冻结，v7 追加 WORLD_JOB_SCHEMA_SQL，
#: v8 追加 BOUNDARY_EVIDENCE_CONTENT_SCHEMA_SQL，v9 追加 ENTITY_RELATIONSHIP_SCHEMA_SQL，
#: v10 追加 COGNITION_TARGET_SCHEMA_SQL（v14 形状），v11 追加 ENTITY_ALIAS_SCHEMA_SQL，
#: v12 追加 RETRACTION_SCHEMA_SQL（v13 形状），v13 追加 RETRACTION_ALTER_V13_SQL +
#: WORLD_EVENT_SCHEMA_SQL，v14 追加 COGNITION_TARGET_ALTER_V14_SQL。
CURRENT_WORLD_SCHEMA_SQL: tuple[str, ...] = (
    WORLD_SCHEMA_SQL
    + WORLD_JOB_SCHEMA_SQL
    + BOUNDARY_EVIDENCE_CONTENT_SCHEMA_SQL
    + ENTITY_RELATIONSHIP_SCHEMA_SQL
    + COGNITION_TARGET_SCHEMA_SQL
    + RETRACTION_SCHEMA_SQL
    + WORLD_EVENT_SCHEMA_SQL
    + TERMINAL_OUTCOME_SCHEMA_SQL
    + TRUST_COMMAND_SCHEMA_SQL
    + TRUST_REJECTION_SCHEMA_SQL
    + CLARIFICATION_SCHEMA_SQL
    + PORTABLE_IMPORT_RECEIPT_SCHEMA_SQL
)
CURRENT_SCHEMA_SQL: tuple[str, ...] = BASE_SCHEMA_SQL + CURRENT_WORLD_SCHEMA_SQL

#: 兼容既有 import；它现在明确表示 Python 当前 schema，而不是 TS parity schema。
SCHEMA_SQL: tuple[str, ...] = CURRENT_SCHEMA_SQL
