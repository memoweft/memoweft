/**
 * Durable, testbench-local ledger for one prepared Next adapter request.
 *
 * It intentionally knows nothing about HTTP, the bridge, or the world schema.
 * The caller owns execution; this store only reserves an exact request and
 * records its bounded terminal outcome on the same SQLite connection used by
 * openStores().
 */
import { createHash, randomUUID } from 'node:crypto';

export const MAX_OPERATION_REQUEST_BYTES = 128 * 1024;
export const MAX_OPERATION_RESULT_BYTES = 256 * 1024;
export const DEFAULT_OPERATION_LEASE_MS = 60_000;

const MAX_ID_CHARS = 200;
const MAX_FAILURE_CODE_CHARS = 80;
const MIN_OPERATION_LEASE_MS = 1_000;
const MAX_OPERATION_LEASE_MS = 900_000;
const LEGACY_RUNNING_GRACE_MS = 630_000;
const TERMINAL_STATUSES = new Set(['ready', 'failed']);
const ALL_STATUSES = new Set(['pending', 'running', 'ready', 'failed']);
const SAFE_NEXT_MEMORY_TERMINAL_STATES = new Set([
  'applied',
  'no-change',
  'no-candidate',
  'clarification-required',
  'out-of-scope',
  'failed',
]);
// The compiler accepts at most twelve formal claims in one Evidence turn.
// A durable receipt must carry every applied cognition update from that turn;
// the separate history projection may compact its display independently.
const MAX_COGNITION_EVIDENCE_CHANGES = 12;
const MAX_COGNITION_REPLACEMENTS = 12;
const MAX_TERMINAL_RESULT_TEXT_CHARS = 240;
// Cognition prose is derived from the bounded adapter Evidence and is not an
// identifier/enum. Keep an explicit, independent allowance for useful memory
// text while the canonical terminal receipt remains capped at 256 KiB.
const MAX_COGNITION_TEXT_CHARS = 4_000;
const SHA256_DIGEST = /^sha256:[a-f0-9]{64}$/;

function plainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** JSON serialization with object keys recursively sorted. */
export function canonicalJson(value) {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') {
    return JSON.stringify(value);
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value))
      throw new Error('operation JSON must not contain a non-finite number');
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(',')}]`;
  if (!plainObject(value)) throw new Error('operation JSON must contain only JSON values');
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
    .join(',')}}`;
}

function sha256(text) {
  return `sha256:${createHash('sha256').update(text, 'utf8').digest('hex')}`;
}

function assertId(value, label) {
  if (
    typeof value !== 'string' ||
    !value ||
    value !== value.trim() ||
    value.length > MAX_ID_CHARS
  ) {
    throw new Error(`${label} must be a non-empty trimmed string up to ${MAX_ID_CHARS} characters`);
  }
}

function exactPreparedPayload(payload) {
  if (!plainObject(payload)) throw new Error('prepared adapter payload must be an object');
  const expected = [
    'carryForwardEvidenceIds',
    'currentUserTurnId',
    'evidenceRecords',
    'operationId',
    'sessionId',
    'turns',
  ];
  const keys = Object.keys(payload).sort();
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) {
    throw new Error(
      'prepared adapter payload must contain exactly operationId, sessionId, currentUserTurnId, carryForwardEvidenceIds, evidenceRecords, turns',
    );
  }
  assertId(payload.operationId, 'operationId');
  assertId(payload.sessionId, 'sessionId');
  assertId(payload.currentUserTurnId, 'currentUserTurnId');
  if (!Array.isArray(payload.turns))
    throw new Error('prepared adapter payload turns must be an array');
  if (!Array.isArray(payload.carryForwardEvidenceIds)) {
    throw new Error('prepared adapter payload carryForwardEvidenceIds must be an array');
  }
  if (!Array.isArray(payload.evidenceRecords)) {
    throw new Error('prepared adapter payload evidenceRecords must be an array');
  }
  const requestJson = canonicalJson(payload);
  if (Buffer.byteLength(requestJson, 'utf8') > MAX_OPERATION_REQUEST_BYTES) {
    throw new Error('prepared adapter payload exceeds the bounded request size');
  }
  return { requestJson, requestHash: sha256(requestJson) };
}

function parseJson(raw, label) {
  try {
    return JSON.parse(String(raw));
  } catch {
    throw new Error(`stored ${label} is corrupt`);
  }
}

function assertBoundedTerminalText(value, label) {
  if (
    typeof value !== 'string' ||
    !value ||
    value !== value.trim() ||
    value.length > MAX_TERMINAL_RESULT_TEXT_CHARS
  ) {
    throw new Error(
      `${label} must be a non-empty trimmed string up to ${MAX_TERMINAL_RESULT_TEXT_CHARS} characters`,
    );
  }
}

function assertBoundedCognitionText(value, label) {
  if (
    typeof value !== 'string' ||
    !value ||
    value !== value.trim() ||
    value.length > MAX_COGNITION_TEXT_CHARS
  ) {
    throw new Error(
      `${label} must be a non-empty trimmed string up to ${MAX_COGNITION_TEXT_CHARS} characters`,
    );
  }
}

function assertCognitionSnapshot(value, label) {
  if (!plainObject(value)) throw new Error(`${label} must be a complete cognition snapshot`);
  for (const key of ['id', 'world_id', 'content_type', 'formed_by', 'cred_status']) {
    try {
      assertBoundedTerminalText(value[key], `${label} ${key}`);
    } catch {
      throw new Error(`${label} must be a complete cognition snapshot`);
    }
  }
  assertBoundedCognitionText(value.content, `${label} content`);
  if (!Number.isSafeInteger(value.confidence) || value.confidence < 0 || value.confidence > 1000) {
    throw new Error(`${label} must be a complete cognition snapshot`);
  }
  if (!plainObject(value.target)) {
    throw new Error(`${label} must be a complete cognition snapshot`);
  }
  assertBoundedTerminalText(value.target.kind, `${label} target kind`);
  assertBoundedTerminalText(value.target.id, `${label} target id`);
  if (!plainObject(value.perspective)) {
    throw new Error(`${label} must be a complete cognition snapshot`);
  }
  assertBoundedTerminalText(value.perspective.kind, `${label} perspective kind`);
  if (
    !Array.isArray(value.perspective.holder_entity_ids) ||
    value.perspective.holder_entity_ids.length > 12
  ) {
    throw new Error(`${label} must be a complete cognition snapshot`);
  }
  for (const holderId of value.perspective.holder_entity_ids) {
    assertBoundedTerminalText(holderId, `${label} perspective holder`);
  }
  // The Python World may accumulate more than one turn's support/contradict
  // provenance on a current cognition. Preserve the complete signed snapshot;
  // the enclosing terminal receipt is already bounded to 256 KiB.
  if (!Array.isArray(value.sources)) {
    throw new Error(`${label} must be a complete cognition snapshot`);
  }
  for (const source of value.sources) {
    if (!plainObject(source)) throw new Error(`${label} must be a complete cognition snapshot`);
    assertBoundedTerminalText(source.evidence_id, `${label} source evidence_id`);
    assertBoundedTerminalText(source.relation, `${label} source relation`);
  }
  if (!plainObject(value.structured_claim)) {
    throw new Error(`${label} must be a supported structured cognition`);
  }
  const claim = value.structured_claim;
  if (
    !['attribute', 'evaluation', 'relationship_statement', 'event_statement'].includes(
      claim.statement_kind,
    )
  ) {
    throw new Error(`${label} must be a supported structured cognition`);
  }
  for (const key of ['polarity', 'epistemic_status']) {
    assertBoundedTerminalText(claim[key], `${label} structured cognition ${key}`);
  }
  if (claim.statement_kind === 'attribute') {
    assertBoundedCognitionText(claim.value, `${label} structured cognition value`);
    assertBoundedCognitionText(claim.predicate, `${label} structured Attribute predicate`);
  } else if (['relationship_statement', 'event_statement'].includes(claim.statement_kind)) {
    if (claim.value !== null && claim.value !== undefined) {
      throw new Error(`${label} structured World statement value must be empty`);
    }
    assertBoundedCognitionText(claim.predicate, `${label} structured World statement predicate`);
  } else if (claim.predicate !== null && claim.predicate !== undefined) {
    assertBoundedCognitionText(claim.value, `${label} structured cognition value`);
    assertBoundedTerminalText(claim.predicate, `${label} structured cognition predicate`);
  } else {
    assertBoundedCognitionText(claim.value, `${label} structured cognition value`);
  }
}

function assertCognitionReplacements(value, currentEvidenceId) {
  if (!Array.isArray(value) || value.length > MAX_COGNITION_REPLACEMENTS) {
    throw new Error('stored cognitionReplacements exceeds its bounded terminal shape');
  }
  const priorIds = new Set();
  const successorIds = new Set();
  const bindings = [];
  for (const replacement of value) {
    if (!plainObject(replacement)) {
      throw new Error('stored cognitionReplacements item must be an object');
    }
    assertBoundedTerminalText(
      replacement.priorCognitionId,
      'stored cognitionReplacements priorCognitionId',
    );
    assertBoundedTerminalText(
      replacement.successorCognitionId,
      'stored cognitionReplacements successorCognitionId',
    );
    assertBoundedTerminalText(replacement.evidenceId, 'stored cognitionReplacements evidenceId');
    if (replacement.relation !== 'corrects') {
      throw new Error('stored cognitionReplacements relation is invalid');
    }
    if (replacement.evidenceId !== currentEvidenceId) {
      throw new Error('stored cognitionReplacements evidenceId is not the current Evidence');
    }
    assertCognitionSnapshot(replacement.before, 'stored cognitionReplacements before');
    assertCognitionSnapshot(replacement.after, 'stored cognitionReplacements after');
    if (
      replacement.before.id !== replacement.priorCognitionId ||
      replacement.after.id !== replacement.successorCognitionId
    ) {
      throw new Error('stored cognitionReplacements snapshots do not match their cognition IDs');
    }
    if (replacement.priorCognitionId === replacement.successorCognitionId) {
      throw new Error('stored cognitionReplacements requires distinct cognition IDs');
    }
    if (
      priorIds.has(replacement.priorCognitionId) ||
      successorIds.has(replacement.successorCognitionId)
    ) {
      throw new Error('stored cognitionReplacements contains a duplicate cognition transition');
    }
    priorIds.add(replacement.priorCognitionId);
    successorIds.add(replacement.successorCognitionId);
    if (
      replacement.before.world_id !== replacement.after.world_id ||
      canonicalJson(replacement.before.target) !== canonicalJson(replacement.after.target) ||
      canonicalJson(replacement.before.perspective) !==
        canonicalJson(replacement.after.perspective) ||
      replacement.before.content_type !== replacement.after.content_type
    ) {
      throw new Error(
        'stored cognitionReplacements snapshots must keep the same world, target, perspective, and content_type',
      );
    }
    if (replacement.before.structured_claim.value === replacement.after.structured_claim.value) {
      throw new Error(
        'stored cognitionReplacements requires different structured cognition values',
      );
    }
    if (
      replacement.before.structured_claim.statement_kind !==
        replacement.after.structured_claim.statement_kind ||
      (replacement.before.structured_claim.statement_kind === 'attribute' &&
        replacement.before.structured_claim.predicate !==
          replacement.after.structured_claim.predicate)
    ) {
      throw new Error(
        'stored cognitionReplacements must preserve its structured statement kind and Attribute predicate',
      );
    }
    if (
      !replacement.after.sources.some(
        (source) => source.evidence_id === replacement.evidenceId && source.relation === 'support',
      )
    ) {
      throw new Error('stored cognitionReplacements after snapshot lacks current Evidence support');
    }
    bindings.push({
      subject: replacement.before.target,
      predecessorId: replacement.priorCognitionId,
      successorId: replacement.successorCognitionId,
      evidenceId: replacement.evidenceId,
    });
  }
  return bindings;
}

function correctionBindingKey({ subject, predecessorId, successorId, evidenceId }) {
  return canonicalJson({ evidenceId, predecessorId, subject, successorId });
}

function singletonEvolutionId(value, label) {
  if (!Array.isArray(value) || value.length !== 1) {
    throw new Error(`stored cognition correction evolution step ${label} must contain one ID`);
  }
  assertBoundedTerminalText(value[0], `stored cognition correction evolution step ${label}`);
  return value[0];
}

function assertCognitionCorrectionBindings(replacementBindings, evolutionSteps, currentEvidenceId) {
  if (!Array.isArray(evolutionSteps)) {
    throw new Error('stored proposal evolutionSteps must be an array');
  }
  const correctsSteps = evolutionSteps.filter(
    (step) => plainObject(step) && step.relation === 'corrects',
  );
  if (replacementBindings.length !== correctsSteps.length) {
    throw new Error(
      'stored cognitionReplacements and cognition_change/corrects evolutionSteps must be one-to-one',
    );
  }

  const stepKeys = new Set();
  for (const step of correctsSteps) {
    if (step.kind !== 'cognition_change' || !plainObject(step.subject)) {
      throw new Error('stored cognition correction evolution step is incomplete or invalid');
    }
    assertBoundedTerminalText(
      step.subject.kind,
      'stored cognition correction evolution subject kind',
    );
    assertBoundedTerminalText(step.subject.id, 'stored cognition correction evolution subject id');
    const binding = {
      subject: step.subject,
      predecessorId: singletonEvolutionId(step.predecessor_ids, 'predecessor_ids'),
      successorId: singletonEvolutionId(step.successor_ids, 'successor_ids'),
      evidenceId: singletonEvolutionId(step.evidence_ids, 'evidence_ids'),
    };
    if (binding.evidenceId !== currentEvidenceId) {
      throw new Error(
        'stored cognition correction evolution step is not bound to current Evidence',
      );
    }
    const key = correctionBindingKey(binding);
    if (stepKeys.has(key)) {
      throw new Error('stored cognition correction evolutionSteps contain a duplicate transition');
    }
    stepKeys.add(key);
  }

  const replacementKeys = new Set(replacementBindings.map(correctionBindingKey));
  if (
    replacementKeys.size !== replacementBindings.length ||
    replacementKeys.size !== stepKeys.size ||
    [...replacementKeys].some((key) => !stepKeys.has(key))
  ) {
    throw new Error(
      'stored cognitionReplacements and cognition_change/corrects evolutionSteps must be one-to-one',
    );
  }
}

/**
 * The local operation result is only a bounded observation receipt.  Do not
 * duplicate Python's full response schema here; validate the few fields that
 * make a persisted terminal receipt safe to replay and display.
 */
function assertSafeNextMemory(value) {
  if (!plainObject(value)) throw new Error('stored safe nextMemory must be an object');
  if (!SAFE_NEXT_MEMORY_TERMINAL_STATES.has(value.state)) {
    throw new Error('stored safe nextMemory has no valid terminal state');
  }
  if (value.memoryProposal === undefined) return;
  if (!plainObject(value.memoryProposal)) {
    throw new Error('stored safe nextMemory memoryProposal must be an object');
  }
  const proposal = value.memoryProposal;
  if (proposal.resultHash !== undefined) {
    assertBoundedTerminalText(proposal.resultHash, 'stored proposal resultHash');
  }
  if (proposal.cognitionEvidenceChanges !== undefined) {
    if (
      !Array.isArray(proposal.cognitionEvidenceChanges) ||
      proposal.cognitionEvidenceChanges.length > MAX_COGNITION_EVIDENCE_CHANGES
    ) {
      throw new Error('stored cognitionEvidenceChanges exceeds its bounded terminal shape');
    }
    for (const change of proposal.cognitionEvidenceChanges) {
      if (!plainObject(change))
        throw new Error('stored cognitionEvidenceChanges item must be an object');
      assertBoundedTerminalText(change.cognitionId, 'stored cognitionEvidenceChanges cognitionId');
      assertBoundedTerminalText(change.evidenceId, 'stored cognitionEvidenceChanges evidenceId');
      if (!['contradicts', 'reaffirms'].includes(change.relation)) {
        throw new Error('stored cognitionEvidenceChanges relation is invalid');
      }
      if (!plainObject(change.before) || !plainObject(change.after)) {
        throw new Error('stored cognitionEvidenceChanges snapshots must be objects');
      }
      if (change.before.id !== change.cognitionId || change.after.id !== change.cognitionId) {
        throw new Error('stored cognitionEvidenceChanges snapshots do not match cognitionId');
      }
      const hasStructuredSnapshot =
        change.before.structured_claim !== undefined || change.after.structured_claim !== undefined;
      if (hasStructuredSnapshot) {
        assertCognitionSnapshot(change.before, 'stored cognitionEvidenceChanges before');
        assertCognitionSnapshot(change.after, 'stored cognitionEvidenceChanges after');
        if (
          change.before.world_id !== change.after.world_id ||
          canonicalJson(change.before.target) !== canonicalJson(change.after.target) ||
          canonicalJson(change.before.perspective) !== canonicalJson(change.after.perspective) ||
          change.before.content !== change.after.content ||
          change.before.content_type !== change.after.content_type ||
          change.before.formed_by !== change.after.formed_by ||
          canonicalJson(change.before.structured_claim) !==
            canonicalJson(change.after.structured_claim)
        ) {
          throw new Error(
            'stored cognitionEvidenceChanges must preserve its structured proposition',
          );
        }
        const expectedSourceRelation = change.relation === 'contradicts' ? 'contradict' : 'support';
        if (
          change.after.sources.length !== change.before.sources.length + 1 ||
          canonicalJson(change.after.sources.slice(0, -1)) !==
            canonicalJson(change.before.sources) ||
          canonicalJson(change.after.sources.at(-1)) !==
            canonicalJson({ evidence_id: change.evidenceId, relation: expectedSourceRelation })
        ) {
          throw new Error(
            'stored cognitionEvidenceChanges after snapshot must append the current Evidence relation',
          );
        }
      }
    }
  }
  let replacementBindings = [];
  if (proposal.cognitionReplacements !== undefined) {
    if (value.state !== 'applied') {
      throw new Error('stored cognitionReplacements requires an applied terminal state');
    }
    assertBoundedTerminalText(
      proposal.currentEvidenceId,
      'stored cognitionReplacements currentEvidenceId',
    );
    replacementBindings = assertCognitionReplacements(
      proposal.cognitionReplacements,
      proposal.currentEvidenceId,
    );
  }
  assertCognitionCorrectionBindings(
    replacementBindings,
    proposal.evolutionSteps ?? [],
    proposal.currentEvidenceId,
  );
}

function safeNextMemoryFromRow(row) {
  const value = parseJson(row.safe_next_memory_json, 'safe nextMemory');
  assertSafeNextMemory(value);
  const json = canonicalJson(value);
  if (Buffer.byteLength(json, 'utf8') > MAX_OPERATION_RESULT_BYTES) {
    throw new Error('stored safe nextMemory exceeds the bounded result size');
  }
  const expectedHash = sha256(json);
  const storedHash = row.safe_next_memory_hash;
  if (storedHash === null || storedHash === undefined) {
    throw new Error('stored ready operation has no durable safe nextMemory hash');
  }
  if (typeof storedHash !== 'string' || !SHA256_DIGEST.test(storedHash)) {
    throw new Error('stored safe nextMemory hash is invalid');
  }
  if (storedHash !== expectedHash) {
    throw new Error('stored safe nextMemory does not match its durable hash');
  }
  return { value, json, hash: expectedHash };
}

function rowToOperation(row) {
  if (!row) return null;
  const status = String(row.status);
  if (!ALL_STATUSES.has(status)) throw new Error('stored operation has an invalid status');
  const operation = {
    operationId: String(row.operation_id),
    sessionId: String(row.session_id),
    currentUserTurnId: String(row.current_user_turn_id),
    status,
    requestHash: String(row.request_hash),
    createdAt: String(row.created_at),
    updatedAt: String(row.updated_at),
  };
  if (status === 'running') {
    const claimOwner = row.claim_owner;
    const claimToken = row.claim_token;
    const claimExpiresAtMs = Number(row.claim_expires_at_ms);
    if (
      typeof claimOwner !== 'string' ||
      !claimOwner ||
      claimOwner.length > MAX_ID_CHARS ||
      typeof claimToken !== 'string' ||
      !claimToken ||
      claimToken.length > MAX_ID_CHARS ||
      !Number.isSafeInteger(claimExpiresAtMs) ||
      claimExpiresAtMs <= 0
    ) {
      throw new Error('stored running operation has an invalid worker lease');
    }
    operation.claimOwner = claimOwner;
    operation.claimToken = claimToken;
    operation.claimExpiresAtMs = claimExpiresAtMs;
  }
  if (row.request_json !== null && row.request_json !== undefined) {
    const request = parseJson(row.request_json, 'request');
    const verified = exactPreparedPayload(request);
    if (
      verified.requestHash !== operation.requestHash ||
      request.operationId !== operation.operationId ||
      request.sessionId !== operation.sessionId ||
      request.currentUserTurnId !== operation.currentUserTurnId
    ) {
      throw new Error('stored request does not match its operation identity or request hash');
    }
    operation.request = request;
  }
  const hasSafeNextMemory =
    row.safe_next_memory_json !== null && row.safe_next_memory_json !== undefined;
  if (status === 'ready' && !hasSafeNextMemory) {
    throw new Error('stored ready operation has no safe nextMemory');
  }
  if (status !== 'ready' && hasSafeNextMemory) {
    throw new Error('stored non-ready operation carries safe nextMemory');
  }
  if (hasSafeNextMemory) {
    operation.safeNextMemory = safeNextMemoryFromRow(row).value;
  }
  if (row.failure_code !== null && row.failure_code !== undefined)
    operation.failureCode = String(row.failure_code);
  return operation;
}

function nowIso() {
  return new Date().toISOString();
}

/**
 * @param {{exec(sql: string): void, prepare(sql: string): {get(...args: any[]): any, all(...args: any[]): any[], run(...args: any[]): {changes: number|bigint}}}} db
 */
export class NextAdapterOperationStore {
  constructor(
    db,
    {
      clock = nowIso,
      workerId = `next-worker:${randomUUID()}`,
      tokenFactory = randomUUID,
      defaultLeaseMs = DEFAULT_OPERATION_LEASE_MS,
    } = {},
  ) {
    if (!db || typeof db.exec !== 'function' || typeof db.prepare !== 'function') {
      throw new Error('NextAdapterOperationStore requires an open SQLite connection');
    }
    this.db = db;
    this.clock = clock;
    assertId(workerId, 'workerId');
    if (typeof tokenFactory !== 'function') {
      throw new Error('tokenFactory must be a function');
    }
    this.workerId = workerId;
    this.tokenFactory = tokenFactory;
    this.defaultLeaseMs = this._validateLeaseMs(defaultLeaseMs);
    let schemaTransactionOpen = false;
    try {
      // Serialize the one-time outbox extension against every older or newer
      // server process.  Once COMMIT releases the write lock, the triggers
      // below already fence pre-lease SQL as well as current token holders.
      db.exec('BEGIN IMMEDIATE');
      schemaTransactionOpen = true;
      db.exec(`
        CREATE TABLE IF NOT EXISTS next_adapter_operation (
          operation_id TEXT PRIMARY KEY NOT NULL,
          session_id TEXT NOT NULL,
          current_user_turn_id TEXT NOT NULL,
          status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'ready', 'failed')),
          request_hash TEXT NOT NULL,
          request_json TEXT,
          safe_next_memory_json TEXT,
          safe_next_memory_hash TEXT,
          failure_code TEXT,
          claim_owner TEXT,
          claim_token TEXT,
          claim_expires_at_ms INTEGER,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          CHECK (
            (status IN ('pending', 'running') AND request_json IS NOT NULL AND safe_next_memory_json IS NULL AND safe_next_memory_hash IS NULL AND failure_code IS NULL)
            OR (status = 'ready' AND request_json IS NULL AND safe_next_memory_json IS NOT NULL AND safe_next_memory_hash IS NOT NULL AND failure_code IS NULL)
            OR (status = 'failed' AND request_json IS NULL AND safe_next_memory_json IS NULL AND safe_next_memory_hash IS NULL AND failure_code IS NOT NULL)
          ),
          UNIQUE (session_id, current_user_turn_id)
        );
        CREATE INDEX IF NOT EXISTS next_adapter_operation_recoverable
          ON next_adapter_operation(status, updated_at);
      `);
      const readColumns = () =>
        new Set(
          db
            .prepare(`PRAGMA table_info(next_adapter_operation)`)
            .all()
            .map((column) => String(column.name)),
        );
      const columns = readColumns();
      const ensureColumn = (name, declaration) => {
        if (columns.has(name)) return;
        db.exec(`ALTER TABLE next_adapter_operation ADD COLUMN ${declaration}`);
        columns.add(name);
      };
      ensureColumn('claim_owner', 'claim_owner TEXT');
      ensureColumn('claim_expires_at_ms', 'claim_expires_at_ms INTEGER');
      ensureColumn('claim_token', 'claim_token TEXT');
      ensureColumn('safe_next_memory_hash', 'safe_next_memory_hash TEXT');
      const legacyNow = this._time();

      // A ready receipt without its original durable hash is not replayable:
      // its current JSON could already have been altered before this binary
      // arrived. Keep the old table shape upgrade, but isolate every such row
      // instead of retroactively signing bytes we cannot authenticate.
      const readyRows = db
        .prepare(
          `SELECT * FROM next_adapter_operation WHERE status = 'ready' ORDER BY operation_id`,
        )
        .all();
      for (const readyRow of readyRows) {
        try {
          safeNextMemoryFromRow(readyRow);
        } catch {
          db.prepare(
            `UPDATE next_adapter_operation
             SET status = 'failed', request_json = NULL, safe_next_memory_json = NULL,
                 safe_next_memory_hash = NULL, failure_code = 'NEXT_OPERATION_STATE_INVALID',
                 claim_owner = NULL, claim_token = NULL, claim_expires_at_ms = NULL, updated_at = ?
             WHERE operation_id = ? AND status = 'ready'`,
          ).run(legacyNow.iso, readyRow.operation_id);
        }
      }

      // Rows left by a pre-lease binary may belong to a still-live model call.
      // Preserve them for the old maximum operation budget.  Its later SQL is
      // fenced by the triggers, then a current worker replays the same stable
      // operation after this grace expires.
      db.prepare(
        `
        UPDATE next_adapter_operation
        SET claim_owner = 'legacy-unowned', claim_token = 'legacy-unfenced',
            claim_expires_at_ms = ?, updated_at = ?
        WHERE status = 'running'
          AND (
            claim_owner IS NULL OR claim_owner = '' OR length(claim_owner) > ${MAX_ID_CHARS}
            OR claim_token IS NULL OR claim_token = '' OR length(claim_token) > ${MAX_ID_CHARS}
            OR typeof(claim_expires_at_ms) <> 'integer' OR claim_expires_at_ms <= 0
          )
      `,
      ).run(legacyNow.ms + LEGACY_RUNNING_GRACE_MS, legacyNow.iso);
      db.exec(`
        UPDATE next_adapter_operation
        SET claim_owner = NULL, claim_token = NULL, claim_expires_at_ms = NULL
        WHERE status <> 'running';

        DROP TRIGGER IF EXISTS next_adapter_operation_lease_insert_guard;
        DROP TRIGGER IF EXISTS next_adapter_operation_lease_update_guard;
        DROP TRIGGER IF EXISTS next_adapter_operation_terminal_integrity_insert_guard;
        DROP TRIGGER IF EXISTS next_adapter_operation_terminal_integrity_update_guard;
        CREATE TRIGGER next_adapter_operation_lease_insert_guard
        BEFORE INSERT ON next_adapter_operation
        WHEN NOT (
          (
            NEW.status = 'running'
            AND typeof(NEW.claim_owner) = 'text'
            AND length(NEW.claim_owner) BETWEEN 1 AND ${MAX_ID_CHARS}
            AND typeof(NEW.claim_token) = 'text'
            AND length(NEW.claim_token) BETWEEN 1 AND ${MAX_ID_CHARS}
            AND typeof(NEW.claim_expires_at_ms) = 'integer'
            AND NEW.claim_expires_at_ms > 0
          )
          OR (
            NEW.status <> 'running'
            AND NEW.claim_owner IS NULL
            AND NEW.claim_token IS NULL
            AND NEW.claim_expires_at_ms IS NULL
          )
        )
        BEGIN
          SELECT RAISE(ABORT, 'next operation lease state invalid');
        END;

        CREATE TRIGGER next_adapter_operation_lease_update_guard
        BEFORE UPDATE ON next_adapter_operation
        WHEN NOT (
          (
            NEW.status = 'running'
            AND typeof(NEW.claim_owner) = 'text'
            AND length(NEW.claim_owner) BETWEEN 1 AND ${MAX_ID_CHARS}
            AND typeof(NEW.claim_token) = 'text'
            AND length(NEW.claim_token) BETWEEN 1 AND ${MAX_ID_CHARS}
            AND typeof(NEW.claim_expires_at_ms) = 'integer'
            AND NEW.claim_expires_at_ms > 0
          )
          OR (
            NEW.status <> 'running'
            AND NEW.claim_owner IS NULL
            AND NEW.claim_token IS NULL
            AND NEW.claim_expires_at_ms IS NULL
          )
        )
        BEGIN
          SELECT RAISE(ABORT, 'next operation lease state invalid');
        END;

        CREATE TRIGGER next_adapter_operation_terminal_integrity_insert_guard
        BEFORE INSERT ON next_adapter_operation
        WHEN NOT (
          (
            NEW.status IN ('pending', 'running')
            AND NEW.request_json IS NOT NULL
            AND NEW.safe_next_memory_json IS NULL
            AND NEW.safe_next_memory_hash IS NULL
            AND NEW.failure_code IS NULL
          )
          OR (
            NEW.status = 'ready'
            AND NEW.request_json IS NULL
            AND NEW.safe_next_memory_json IS NOT NULL
            AND typeof(NEW.safe_next_memory_hash) = 'text'
            AND length(NEW.safe_next_memory_hash) = 71
            AND substr(NEW.safe_next_memory_hash, 1, 7) = 'sha256:'
            AND substr(NEW.safe_next_memory_hash, 8) NOT GLOB '*[^0-9a-f]*'
            AND NEW.failure_code IS NULL
          )
          OR (
            NEW.status = 'failed'
            AND NEW.request_json IS NULL
            AND NEW.safe_next_memory_json IS NULL
            AND NEW.safe_next_memory_hash IS NULL
            AND NEW.failure_code IS NOT NULL
          )
        )
        BEGIN
          SELECT RAISE(ABORT, 'next operation terminal state invalid');
        END;

        CREATE TRIGGER next_adapter_operation_terminal_integrity_update_guard
        BEFORE UPDATE ON next_adapter_operation
        WHEN NOT (
          (
            NEW.status IN ('pending', 'running')
            AND NEW.request_json IS NOT NULL
            AND NEW.safe_next_memory_json IS NULL
            AND NEW.safe_next_memory_hash IS NULL
            AND NEW.failure_code IS NULL
          )
          OR (
            NEW.status = 'ready'
            AND NEW.request_json IS NULL
            AND NEW.safe_next_memory_json IS NOT NULL
            AND typeof(NEW.safe_next_memory_hash) = 'text'
            AND length(NEW.safe_next_memory_hash) = 71
            AND substr(NEW.safe_next_memory_hash, 1, 7) = 'sha256:'
            AND substr(NEW.safe_next_memory_hash, 8) NOT GLOB '*[^0-9a-f]*'
            AND NEW.failure_code IS NULL
          )
          OR (
            NEW.status = 'failed'
            AND NEW.request_json IS NULL
            AND NEW.safe_next_memory_json IS NULL
            AND NEW.safe_next_memory_hash IS NULL
            AND NEW.failure_code IS NOT NULL
          )
        )
        BEGIN
          SELECT RAISE(ABORT, 'next operation terminal state invalid');
        END;

        CREATE INDEX IF NOT EXISTS next_adapter_operation_claim_expiry
          ON next_adapter_operation(status, claim_expires_at_ms);
      `);
      db.exec('COMMIT');
      schemaTransactionOpen = false;
    } catch (error) {
      if (schemaTransactionOpen) {
        try {
          db.exec('ROLLBACK');
        } catch {
          // Preserve the migration error; rollback failure must not mask it.
        }
      }
      throw error;
    }
  }

  _time() {
    const iso = this.clock();
    const ms = Date.parse(iso);
    if (typeof iso !== 'string' || !Number.isFinite(ms)) {
      throw new Error('operation clock must return a valid ISO timestamp');
    }
    return { iso, ms };
  }

  _validateLeaseMs(leaseMs) {
    if (
      !Number.isInteger(leaseMs) ||
      leaseMs < MIN_OPERATION_LEASE_MS ||
      leaseMs > MAX_OPERATION_LEASE_MS
    ) {
      throw new Error(
        `operation lease must be an integer from ${MIN_OPERATION_LEASE_MS} to ${MAX_OPERATION_LEASE_MS} milliseconds`,
      );
    }
    return leaseMs;
  }

  _getRow(operationId, sessionId) {
    return this.db
      .prepare(`SELECT * FROM next_adapter_operation WHERE operation_id = ? AND session_id = ?`)
      .get(operationId, sessionId);
  }

  _quarantineInvalidRow(row) {
    if (!row || typeof row.operation_id !== 'string' || !row.operation_id) return;
    // This is a local delivery ledger, not a World authority. Removing its
    // unreadable receipt cannot alter the already-applied World; it only
    // prevents a corrupt terminal projection from being replayed as truth.
    this.db
      .prepare(
        `
        UPDATE next_adapter_operation
        SET status = 'failed', request_json = NULL, safe_next_memory_json = NULL,
            safe_next_memory_hash = NULL, failure_code = 'NEXT_OPERATION_STATE_INVALID',
            claim_owner = NULL, claim_token = NULL, claim_expires_at_ms = NULL, updated_at = ?
        WHERE operation_id = ? AND status <> 'failed'
      `,
      )
      .run(this.clock(), row.operation_id);
  }

  _decodeRow(row) {
    if (!row) return null;
    try {
      return rowToOperation(row);
    } catch (error) {
      try {
        this._quarantineInvalidRow(row);
      } catch {
        // Preserve the original decode error. A transient quarantine failure
        // is still fail-closed because this caller receives no terminal data.
      }
      throw error;
    }
  }

  get(operationId, sessionId) {
    assertId(operationId, 'operationId');
    assertId(sessionId, 'sessionId');
    return this._decodeRow(this._getRow(operationId, sessionId));
  }

  reserve(payload) {
    const { requestJson, requestHash } = exactPreparedPayload(payload);
    const { operationId, sessionId, currentUserTurnId } = payload;
    const sameOperation = this.db
      .prepare(`SELECT * FROM next_adapter_operation WHERE operation_id = ?`)
      .get(operationId);
    if (sameOperation) {
      if (
        String(sameOperation.session_id) !== sessionId ||
        String(sameOperation.request_hash) !== requestHash ||
        String(sameOperation.current_user_turn_id) !== currentUserTurnId
      ) {
        throw new Error('operationId is already bound to a different prepared payload');
      }
      // The request bytes were already discarded after terminal persistence,
      // but their canonical hash and Evidence identity remain.  Returning the
      // stored terminal row makes a lost HTTP response an idempotent replay;
      // it never re-enters execution.
      return this._decodeRow(sameOperation);
    }
    const conflictingEvidence = this.db
      .prepare(
        `SELECT operation_id FROM next_adapter_operation WHERE session_id = ? AND current_user_turn_id = ?`,
      )
      .get(sessionId, currentUserTurnId);
    if (conflictingEvidence)
      throw new Error('session and currentUserTurnId are already reserved by another operation');

    const timestamp = this._time().iso;
    try {
      this.db
        .prepare(
          `
          INSERT INTO next_adapter_operation (
            operation_id, session_id, current_user_turn_id, status, request_hash, request_json,
            safe_next_memory_json, safe_next_memory_hash, failure_code, created_at, updated_at
          ) VALUES (?, ?, ?, 'pending', ?, ?, NULL, NULL, NULL, ?, ?)
        `,
        )
        .run(
          operationId,
          sessionId,
          currentUserTurnId,
          requestHash,
          requestJson,
          timestamp,
          timestamp,
        );
    } catch (error) {
      // A second worker may have won between the reads and INSERT.  Re-read
      // and keep exact-payload idempotency, never silently accepting a mismatch.
      const raced = this.db
        .prepare(`SELECT * FROM next_adapter_operation WHERE operation_id = ?`)
        .get(operationId);
      if (
        raced &&
        String(raced.session_id) === sessionId &&
        String(raced.current_user_turn_id) === currentUserTurnId &&
        String(raced.request_hash) === requestHash
      ) {
        return this._decodeRow(raced);
      }
      throw error;
    }
    return this.get(operationId, sessionId);
  }

  listRecoverable(sessionId = undefined) {
    if (sessionId !== undefined) assertId(sessionId, 'sessionId');
    const rows =
      sessionId === undefined
        ? this.db
            .prepare(
              `SELECT * FROM next_adapter_operation WHERE status = 'pending' ORDER BY created_at, operation_id`,
            )
            .all()
        : this.db
            .prepare(
              `SELECT * FROM next_adapter_operation WHERE status = 'pending' AND session_id = ? ORDER BY created_at, operation_id`,
            )
            .all(sessionId);
    const recoverable = [];
    for (const row of rows) {
      try {
        recoverable.push(this._decodeRow(row));
      } catch {
        // One corrupt request must fail closed without starving unrelated
        // sessions forever.  Its bytes are no longer safe to deliver, so
        // quarantine only that row under a stable terminal code.
        this.db
          .prepare(
            `
            UPDATE next_adapter_operation
            SET status = 'failed', request_json = NULL, safe_next_memory_json = NULL,
                safe_next_memory_hash = NULL, failure_code = 'NEXT_OPERATION_STATE_INVALID', claim_owner = NULL,
                claim_token = NULL, claim_expires_at_ms = NULL, updated_at = ?
            WHERE operation_id = ? AND status = 'pending'
          `,
          )
          .run(this.clock(), row.operation_id);
      }
    }
    return recoverable;
  }

  recoverExpiredRunning(sessionId = undefined) {
    if (sessionId !== undefined) assertId(sessionId, 'sessionId');
    const timestamp = this._time();
    const changed =
      sessionId === undefined
        ? this.db
            .prepare(
              `
              UPDATE next_adapter_operation
              SET status = 'pending', claim_owner = NULL, claim_token = NULL,
                  claim_expires_at_ms = NULL, updated_at = ?
              WHERE status = 'running'
                AND (
                  claim_owner IS NULL OR claim_owner = '' OR length(claim_owner) > ${MAX_ID_CHARS}
                  OR claim_token IS NULL OR claim_token = '' OR length(claim_token) > ${MAX_ID_CHARS}
                  OR typeof(claim_expires_at_ms) <> 'integer' OR claim_expires_at_ms <= 0
                  OR claim_expires_at_ms <= ?
                )
            `,
            )
            .run(timestamp.iso, timestamp.ms)
        : this.db
            .prepare(
              `
              UPDATE next_adapter_operation
              SET status = 'pending', claim_owner = NULL, claim_token = NULL,
                  claim_expires_at_ms = NULL, updated_at = ?
              WHERE status = 'running' AND session_id = ?
                AND (
                  claim_owner IS NULL OR claim_owner = '' OR length(claim_owner) > ${MAX_ID_CHARS}
                  OR claim_token IS NULL OR claim_token = '' OR length(claim_token) > ${MAX_ID_CHARS}
                  OR typeof(claim_expires_at_ms) <> 'integer' OR claim_expires_at_ms <= 0
                  OR claim_expires_at_ms <= ?
                )
            `,
            )
            .run(timestamp.iso, sessionId, timestamp.ms);
    return Number(changed.changes);
  }

  millisecondsUntilNextLeaseExpiry(sessionId = undefined) {
    if (sessionId !== undefined) assertId(sessionId, 'sessionId');
    const row =
      sessionId === undefined
        ? this.db
            .prepare(
              `SELECT MIN(claim_expires_at_ms) AS expires_at FROM next_adapter_operation WHERE status = 'running'`,
            )
            .get()
        : this.db
            .prepare(
              `SELECT MIN(claim_expires_at_ms) AS expires_at FROM next_adapter_operation WHERE status = 'running' AND session_id = ?`,
            )
            .get(sessionId);
    if (row?.expires_at === null || row?.expires_at === undefined) return null;
    const expiresAt = Number(row.expires_at);
    if (!Number.isSafeInteger(expiresAt) || expiresAt <= 0) {
      throw new Error('stored running operation has an invalid worker lease');
    }
    return Math.max(0, expiresAt - this._time().ms);
  }

  markRunning(operationId, sessionId, { leaseMs = this.defaultLeaseMs } = {}) {
    const operation = this.get(operationId, sessionId);
    if (!operation) throw new Error('operation does not exist in this session');
    if (TERMINAL_STATUSES.has(operation.status))
      throw new Error('terminal operation cannot be replayed');
    if (operation.status === 'running') throw new Error('operation is already claimed by a worker');
    const duration = this._validateLeaseMs(leaseMs);
    const timestamp = this._time();
    const claimToken = this.tokenFactory();
    assertId(claimToken, 'claimToken');
    const changed = this.db
      .prepare(
        `
        UPDATE next_adapter_operation
        SET status = 'running', claim_owner = ?, claim_token = ?, claim_expires_at_ms = ?, updated_at = ?
        WHERE operation_id = ? AND session_id = ? AND status = 'pending'
      `,
      )
      .run(
        this.workerId,
        claimToken,
        timestamp.ms + duration,
        timestamp.iso,
        operationId,
        sessionId,
      );
    if (Number(changed.changes) !== 1)
      throw new Error('operation state changed before it could start');
    return this.get(operationId, sessionId);
  }

  renewLease(operationId, sessionId, claimToken, { leaseMs = this.defaultLeaseMs } = {}) {
    assertId(operationId, 'operationId');
    assertId(sessionId, 'sessionId');
    assertId(claimToken, 'claimToken');
    const duration = this._validateLeaseMs(leaseMs);
    const timestamp = this._time();
    const changed = this.db
      .prepare(
        `
        UPDATE next_adapter_operation
        SET claim_expires_at_ms = ?, updated_at = ?
        WHERE operation_id = ? AND session_id = ? AND status = 'running'
          AND claim_owner = ? AND claim_token = ? AND claim_expires_at_ms > ?
      `,
      )
      .run(
        timestamp.ms + duration,
        timestamp.iso,
        operationId,
        sessionId,
        this.workerId,
        claimToken,
        timestamp.ms,
      );
    if (Number(changed.changes) !== 1) {
      throw new Error('operation lease expired or changed before it could be renewed');
    }
    return this.get(operationId, sessionId);
  }

  markPending(operationId, sessionId, { claimToken } = {}) {
    const operation = this.get(operationId, sessionId);
    if (!operation) throw new Error('operation does not exist in this session');
    if (TERMINAL_STATUSES.has(operation.status)) {
      throw new Error('terminal operation cannot be replayed');
    }
    if (operation.status === 'pending') {
      if (claimToken !== undefined) throw new Error('operation worker lease is no longer current');
      return operation;
    }
    if (operation.claimOwner !== this.workerId || operation.claimToken !== claimToken) {
      throw new Error('running operation is owned by another worker');
    }
    const timestamp = this._time();
    const changed = this.db
      .prepare(
        `
        UPDATE next_adapter_operation
        SET status = 'pending', claim_owner = NULL, claim_token = NULL,
            claim_expires_at_ms = NULL, updated_at = ?
        WHERE operation_id = ? AND session_id = ? AND status = 'running'
          AND claim_owner = ? AND claim_token = ? AND claim_expires_at_ms > ?
      `,
      )
      .run(timestamp.iso, operationId, sessionId, this.workerId, claimToken, timestamp.ms);
    if (Number(changed.changes) !== 1) {
      throw new Error('operation state changed before it could return to pending');
    }
    return this.get(operationId, sessionId);
  }

  markReady(operationId, sessionId, safeNextMemory, { claimToken } = {}) {
    const operation = this.get(operationId, sessionId);
    if (!operation) throw new Error('operation does not exist in this session');
    if (TERMINAL_STATUSES.has(operation.status))
      throw new Error('terminal operation cannot be changed');
    if (!plainObject(safeNextMemory)) throw new Error('safe nextMemory must be a JSON object');
    const resultJson = canonicalJson(safeNextMemory);
    if (Buffer.byteLength(resultJson, 'utf8') > MAX_OPERATION_RESULT_BYTES) {
      throw new Error('safe nextMemory exceeds the bounded result size');
    }
    assertSafeNextMemory(safeNextMemory);
    if (
      operation.status === 'running' &&
      (operation.claimOwner !== this.workerId || operation.claimToken !== claimToken)
    ) {
      throw new Error('running operation is owned by another worker');
    }
    if (operation.status === 'pending' && claimToken !== undefined) {
      throw new Error('operation worker lease is no longer current');
    }
    if (operation.status === 'pending' && claimToken === undefined) {
      throw new Error('operation must be claimed before it can become ready');
    }
    const timestamp = this._time();
    const changed = this.db
      .prepare(
        `
        UPDATE next_adapter_operation
        SET status = 'ready', request_json = NULL, safe_next_memory_json = ?, safe_next_memory_hash = ?, failure_code = NULL,
            claim_owner = NULL, claim_token = NULL, claim_expires_at_ms = NULL, updated_at = ?
        WHERE operation_id = ? AND session_id = ?
          AND status = 'running' AND claim_owner = ? AND claim_token = ?
          AND claim_expires_at_ms > ?
      `,
      )
      .run(
        resultJson,
        sha256(resultJson),
        timestamp.iso,
        operationId,
        sessionId,
        this.workerId,
        claimToken ?? null,
        timestamp.ms,
      );
    if (Number(changed.changes) !== 1)
      throw new Error('operation state changed before it could become ready');
    return this.get(operationId, sessionId);
  }

  markFailed(operationId, sessionId, failureCode, { claimToken } = {}) {
    const operation = this.get(operationId, sessionId);
    if (!operation) throw new Error('operation does not exist in this session');
    if (TERMINAL_STATUSES.has(operation.status))
      throw new Error('terminal operation cannot be changed');
    if (
      operation.status === 'running' &&
      (operation.claimOwner !== this.workerId || operation.claimToken !== claimToken)
    ) {
      throw new Error('running operation is owned by another worker');
    }
    if (operation.status === 'pending' && claimToken !== undefined) {
      throw new Error('operation worker lease is no longer current');
    }
    if (
      typeof failureCode !== 'string' ||
      !/^[A-Z][A-Z0-9_]*$/.test(failureCode) ||
      failureCode.length > MAX_FAILURE_CODE_CHARS
    ) {
      throw new Error('failureCode must be a stable uppercase underscore code');
    }
    const timestamp = this._time();
    const changed = this.db
      .prepare(
        `
        UPDATE next_adapter_operation
        SET status = 'failed', request_json = NULL, safe_next_memory_json = NULL,
            safe_next_memory_hash = NULL, failure_code = ?,
            claim_owner = NULL, claim_token = NULL, claim_expires_at_ms = NULL, updated_at = ?
        WHERE operation_id = ? AND session_id = ?
          AND (
            (status = 'pending' AND ? IS NULL)
            OR (
              status = 'running' AND claim_owner = ? AND claim_token = ?
              AND claim_expires_at_ms > ?
            )
          )
      `,
      )
      .run(
        failureCode,
        timestamp.iso,
        operationId,
        sessionId,
        claimToken ?? null,
        this.workerId,
        claimToken ?? null,
        timestamp.ms,
      );
    if (Number(changed.changes) !== 1)
      throw new Error('operation state changed before it could fail');
    return this.get(operationId, sessionId);
  }
}
