-- Frozen synthetic-only MemoWeft Python v6 migration fixture.
-- Generated on 2026-08-14 by extracting the repository artifact below into a
-- fresh system TEMP site directory, importing it with Python -I, calling that
-- wheel's memoweft.store.driver.open_db(), inserting only the two deterministic
-- synthetic sentinel rows below, and serializing the connection with iterdump().
-- No HERMES_HOME or user database was opened while generating this fixture.
-- source-wheel: py/dist/memoweft-0.7.0.dev0-py3-none-any.whl
-- source-wheel-sha256: 1310510146f863e5cfa154eabc35cc4166adc6c4f0306018ffaac2e2d65ec551
-- imported-schema-module: <system-temp>/site/memoweft/store/schema.py
-- observed-schema-version: 6
-- observed-user-version: 6
-- observed-application-id: 0
-- observed-python-owner-marker: proposal_decision_receipts
-- observed-absent-v7-table: memory_world_job
-- fixture-payload-sha256: 43045acd9bb0c8c51ad319c16a01acc44c3c7b87543fa54da36946ac1bec5702
-- The payload hash covers every UTF-8 byte after the marker line, including
-- the final LF. The metadata deliberately stays outside that hash.
-- BEGIN FROZEN SQL PAYLOAD
BEGIN TRANSACTION;
CREATE TABLE cognition (
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
);
CREATE TABLE cognition_evidence (
  cognition_id TEXT NOT NULL,
  evidence_id  TEXT NOT NULL,
  relation     TEXT NOT NULL
);
CREATE TABLE cognition_transitions (
  id                         TEXT PRIMARY KEY,
  prior_cognition_id         TEXT NOT NULL UNIQUE,
  replacement_cognition_id   TEXT NOT NULL,
  reason                     TEXT NOT NULL,
  revision                   INTEGER NOT NULL
);
CREATE TABLE event (
  id           TEXT PRIMARY KEY,
  subject_id   TEXT NOT NULL,
  summary      TEXT NOT NULL,
  occurred_at  TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  consolidated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE event_evidence (
  event_id    TEXT NOT NULL,
  evidence_id TEXT NOT NULL
);
CREATE TABLE evidence (
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
);
INSERT INTO "evidence" VALUES('e:synthetic-v6','synthetic-owner','spoken','fixture','fixture:synthetic-v6','2026-08-14T00:00:00Z','2026-08-14T00:00:00Z','synthetic v6 evidence','synthetic v6 evidence',1,0,1,NULL,NULL,'synthetic assistant context');
CREATE TABLE evidence_ledger (
  id           TEXT PRIMARY KEY,
  content      TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE evidence_retraction (
  cognition_id TEXT NOT NULL,
  evidence_id  TEXT NOT NULL,
  retracted_at TEXT NOT NULL
);
CREATE TABLE identity_state (
  singleton             INTEGER PRIMARY KEY CHECK(singleton = 1),
  identity_schema_version INTEGER NOT NULL,
  world_id              TEXT    NOT NULL,
  memory_revision       INTEGER NOT NULL CHECK(memory_revision >= 0),
  memory_snapshot_hash  TEXT    NOT NULL,
  identity_graph_hash   TEXT    NOT NULL,
  state_json            TEXT    NOT NULL,
  state_hash            TEXT    NOT NULL,
  storage_generation    INTEGER NOT NULL CHECK(storage_generation >= 1)
);
CREATE TABLE interaction_context (
  id              TEXT PRIMARY KEY,
  subject_id      TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  episode_id      TEXT NOT NULL,
  context_json    TEXT NOT NULL,
  context_hash    TEXT NOT NULL,
  created_at      TEXT NOT NULL
);
CREATE TABLE management_log (
  op          TEXT NOT NULL,
  target_kind TEXT NOT NULL,
  target_id   TEXT NOT NULL,
  reason      TEXT NOT NULL,
  detail      TEXT,
  created_at  TEXT NOT NULL
);
CREATE TABLE memory_state (
  singleton    INTEGER PRIMARY KEY CHECK(singleton = 1),
  revision     INTEGER NOT NULL,
  snapshot_json TEXT    NOT NULL,
  snapshot_hash TEXT    NOT NULL
);
CREATE TABLE proposal_decision_receipts (
  proposal_id          TEXT PRIMARY KEY,
  offered_result_hash  TEXT NOT NULL,
  effective_decision   TEXT NOT NULL CHECK(effective_decision IN ('accept', 'reject')),
  world_revision       INTEGER NOT NULL CHECK(world_revision >= 0),
  snapshot_hash        TEXT NOT NULL,
  decided_at           TEXT NOT NULL,
  receipt_hash         TEXT NOT NULL
);
INSERT INTO "proposal_decision_receipts" VALUES('proposal:synthetic-v6','offered:synthetic-v6','reject',0,'snapshot:synthetic-v6','2026-08-14T00:00:00Z','receipt:synthetic-v6');
CREATE TABLE proposals (
  id                  TEXT PRIMARY KEY,
  kind                TEXT NOT NULL,
  base_revision       INTEGER NOT NULL,
  result_hash         TEXT NOT NULL,
  payload_json        TEXT NOT NULL,
  review_payload_json TEXT,
  status              TEXT NOT NULL CHECK(status IN ('pending', 'accept', 'reject'))
);
CREATE TABLE semantic_resolution (
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
);
CREATE UNIQUE INDEX ux_evidence_origin ON evidence(origin_id) WHERE origin_id IS NOT NULL;
CREATE INDEX ix_evidence_occurred ON evidence(occurred_at);
CREATE INDEX ix_event_subject ON event(subject_id);
CREATE INDEX ix_evev_event ON event_evidence(event_id);
CREATE INDEX ix_cognition_subject ON cognition(subject_id);
CREATE INDEX ix_cogev_cog ON cognition_evidence(cognition_id);
CREATE INDEX ix_evret_cog ON evidence_retraction(cognition_id);
CREATE INDEX ix_mgmt_target ON management_log(target_id);
CREATE INDEX ix_ictx_subject ON interaction_context(subject_id);
CREATE INDEX ix_ictx_conversation ON interaction_context(conversation_id);
CREATE INDEX ix_ictx_hash ON interaction_context(context_hash);
CREATE INDEX ix_semres_evidence ON semantic_resolution(evidence_id);
PRAGMA application_id = 0;
PRAGMA user_version = 6;
COMMIT;
