/**
 * Narrow, local-only bridge from the 1.x testbench to the MemoWeft Next lab.
 *
 * The testbench remains the chat owner: it writes 1.x Evidence, produces the
 * sole assistant reply, and keeps its profile pipeline.  This module only
 * submits the recorded user Evidence plus context to Next for automatic World
 * formation.  It deliberately has no generic forward(path) escape hatch.
 */

const DEFAULT_NEXT_BASE_URL = 'http://127.0.0.1:7891';
const DEFAULT_STATUS_TIMEOUT_MS = 4_500;
const DEFAULT_OPERATION_TIMEOUT_MS = 300_000;
const MAX_NEXT_RESPONSE_BYTES = 512 * 1024;
// Adapter delivery is an operation receipt, not a World snapshot.  The
// browser reads the single World authority separately after it observes this
// receipt, so allowing a full graph here would make a committed Apply look
// transport-ambiguous solely because the graph grew large.
export const MAX_NEXT_ADAPTER_RECEIPT_BYTES = 128 * 1024;
const MAX_RECALL_QUERY_CHARS = 1_200;
const MAX_RECALL_MEMORIES = 8;
const MAX_RECALL_MEMORY_CHARS = 1_200;
const MAX_RECALL_CRED_STATUS_CHARS = 80;
const MAX_RECALL_TOTAL_CHARS = 6_000;
const MAX_ASK_CONTENT_CHARS = 4_000;
const MAX_ASK_QUESTION_CHARS = 12_000;
const MAX_ASK_EVIDENCE_ITEMS = 20;
const MAX_ASK_EVIDENCE_ID_CHARS = 200;
const MAX_ASK_EVIDENCE_SUMMARY_CHARS = 4_000;
const MAX_ASK_EVIDENCE_TOTAL_CHARS = 20_000;
// Python accepts at most 20 typed turns.  Nine previous 1.x records can
// contribute user+assistant context (18), leaving one final user Evidence.
const MAX_CONTEXT_RECORDS = 9;
const MAX_CARRY_FORWARD_EVIDENCE_IDS = 8;
const MAX_LEGACY_IMPORT_TURNS = 20;
const MAX_LEGACY_IMPORT_OPERATION_ID_CHARS = 200;
const MAX_LEGACY_IMPORT_TURN_ID_CHARS = 200;
const MAX_LEGACY_IMPORT_TURN_CONTENT_CHARS = 4_000;
const MAX_LEGACY_IMPORT_TOTAL_CONTENT_CHARS = 20_000;
const MAX_LEGACY_IMPORT_OCCURRED_AT_CHARS = 64;
const SYSTEM_EVIDENCE_KEYS = [
  'allowCloudRead',
  'allowInference',
  'allowLocalRead',
  'correctsEvidenceId',
  'hostId',
  'id',
  'occurredAt',
  'originId',
  'rawContent',
  'recordedAt',
  'sourceKind',
  'subjectId',
  'summary',
];

function safeString(value) {
  return typeof value === 'string' ? value : '';
}

function typedOccurredAt(value) {
  const text = safeString(value);
  if (text && Number.isFinite(Date.parse(text))) return new Date(text).toISOString();
  // A legacy malformed log must not expand the bridge surface or poison the
  // current Evidence.  The fixed epoch is explicit context-only provenance.
  return '1970-01-01T00:00:00.000Z';
}

function stableFallbackUserTurnId(sessionId, turn) {
  return `testbench:${sessionId}:turn:${Number.isInteger(turn) ? turn : 0}:user`;
}

function historicalUserTurnId(sessionId, record) {
  const evidenceId = record?.evidence?.find((item) => typeof item?.id === 'string' && item.id)?.id;
  return evidenceId || stableFallbackUserTurnId(sessionId, record?.turn);
}

function storedUserEvidenceId(record) {
  return record?.evidence?.find((item) => typeof item?.id === 'string' && item.id)?.id;
}

function assistantTurnId(userTurnId) {
  return `${userTurnId}:assistant`;
}

function optionalBoundedString(value, maxLength) {
  return value === null || (typeof value === 'string' && value.length <= maxLength);
}

/** Preserve the complete already-durable 1.x Evidence as provenance, not as a new authority. */
function sanitizeSystemEvidence(value) {
  if (!hasExactKeys(value, SYSTEM_EVIDENCE_KEYS)) {
    throw new Error('eligible Evidence must use the complete durable system Evidence shape');
  }
  for (const [name, maxLength] of [
    ['id', 200],
    ['subjectId', 200],
    ['hostId', 200],
  ]) {
    if (!isString(value[name], maxLength) || value[name] !== value[name].trim()) {
      throw new Error(`eligible Evidence ${name} is invalid`);
    }
  }
  if (value.sourceKind !== 'spoken') {
    throw new Error('eligible user Evidence must have sourceKind spoken');
  }
  if (
    !optionalBoundedString(value.originId, 1_000) ||
    !optionalBoundedString(value.correctsEvidenceId, 200) ||
    typeof value.rawContent !== 'string' ||
    !value.rawContent ||
    value.rawContent.length > 4_000 ||
    typeof value.summary !== 'string' ||
    value.summary.length > 4_000
  ) {
    throw new Error('eligible Evidence content or provenance is invalid');
  }
  if (
    !isString(value.occurredAt, 64) ||
    !hasExplicitTimezone(value.occurredAt) ||
    !Number.isFinite(Date.parse(value.occurredAt)) ||
    !isString(value.recordedAt, 64) ||
    !hasExplicitTimezone(value.recordedAt) ||
    !Number.isFinite(Date.parse(value.recordedAt))
  ) {
    throw new Error('eligible Evidence timestamps must be timezone-qualified ISO dates');
  }
  for (const name of ['allowLocalRead', 'allowCloudRead', 'allowInference']) {
    if (typeof value[name] !== 'boolean') throw new Error(`eligible Evidence ${name} is invalid`);
  }
  if (!value.allowLocalRead || !value.allowInference) {
    throw new Error('eligible Evidence must authorize local read and inference');
  }
  return {
    id: value.id,
    subjectId: value.subjectId,
    sourceKind: value.sourceKind,
    hostId: value.hostId,
    originId: value.originId,
    occurredAt: new Date(value.occurredAt).toISOString(),
    recordedAt: new Date(value.recordedAt).toISOString(),
    rawContent: value.rawContent,
    summary: value.summary,
    allowLocalRead: value.allowLocalRead,
    allowCloudRead: value.allowCloudRead,
    allowInference: value.allowInference,
    correctsEvidenceId: value.correctsEvidenceId,
  };
}

function nextRunFromRecord(record) {
  const nextMemory = record?.nextMemory;
  if (!nextMemory || typeof nextMemory !== 'object' || Array.isArray(nextMemory)) return null;
  const run = nextMemory.run;
  return run && typeof run === 'object' && !Array.isArray(run) ? run : null;
}

function unresolvedEvidenceIds(run) {
  const ids = new Set();
  if (!Array.isArray(run?.unresolvedReferences)) return ids;
  for (const reference of run.unresolvedReferences) {
    if (!reference || typeof reference !== 'object' || Array.isArray(reference)) continue;
    const evidenceIds = reference.evidence_ids;
    if (!Array.isArray(evidenceIds)) continue;
    for (const evidenceId of evidenceIds) {
      if (typeof evidenceId === 'string' && evidenceId) ids.add(evidenceId);
    }
  }
  return ids;
}

/**
 * Select only prior user Evidence that the Next service itself retained as an
 * unresolved, no-candidate adapter run.  This is deliberately structural:
 * message wording is never inspected.  A later run whose carry audit says the
 * source was consumed removes it from future selections, even though the 1.x
 * log keeps the original source-run snapshot immutable.
 */
export function selectCarryForwardEvidenceIds({ sessionId, record, previousRecords }) {
  if (typeof sessionId !== 'string' || !sessionId) return [];
  const currentEvidenceId = storedUserEvidenceId(record);
  if (!currentEvidenceId) return [];
  const records = Array.isArray(previousRecords) ? previousRecords : [];
  const consumed = new Set();

  for (const prior of records) {
    const carry = nextRunFromRecord(prior)?.carryForward;
    if (!carry || carry.status !== 'consumed' || !Array.isArray(carry.requestedEvidenceIds)) {
      continue;
    }
    for (const evidenceId of carry.requestedEvidenceIds) {
      if (typeof evidenceId === 'string' && evidenceId) consumed.add(evidenceId);
    }
  }

  const selected = [];
  const seen = new Set();
  for (const prior of records) {
    const evidenceId = storedUserEvidenceId(prior);
    const run = nextRunFromRecord(prior);
    const adapter = run?.adapter;
    if (
      !evidenceId ||
      evidenceId === currentEvidenceId ||
      seen.has(evidenceId) ||
      consumed.has(evidenceId) ||
      !['no-change', 'no-candidate'].includes(run?.state) ||
      !adapter ||
      adapter.sessionId !== sessionId ||
      adapter.currentUserTurnId !== evidenceId ||
      !unresolvedEvidenceIds(run).has(evidenceId)
    ) {
      continue;
    }
    seen.add(evidenceId);
    selected.push(evidenceId);
  }
  return selected.slice(-MAX_CARRY_FORWARD_EVIDENCE_IDS).sort();
}

/** Only an explicit http loopback origin can be used for the local Next lab. */
export function normalizeNextBaseUrl(value = process.env.MEMOWEFT_NEXT_LAB_URL) {
  const candidate = value || DEFAULT_NEXT_BASE_URL;
  let parsed;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new Error('MEMOWEFT_NEXT_LAB_URL 必须是本机 http 地址。');
  }
  if (
    parsed.protocol !== 'http:' ||
    !['127.0.0.1', 'localhost'].includes(parsed.hostname.toLowerCase()) ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== '/' ||
    parsed.search ||
    parsed.hash
  ) {
    throw new Error(
      'MEMOWEFT_NEXT_LAB_URL 只允许 http://127.0.0.1:<port> 或 http://localhost:<port>。',
    );
  }
  return parsed.origin;
}

/** Separate slow local model operations from cheap Lab status reads. */
export function readNextBridgeTimeouts({
  statusTimeoutMs = DEFAULT_STATUS_TIMEOUT_MS,
  operationTimeoutMs = process.env.MEMOWEFT_NEXT_OPERATION_TIMEOUT_MS ??
    DEFAULT_OPERATION_TIMEOUT_MS,
} = {}) {
  if (!Number.isInteger(statusTimeoutMs) || statusTimeoutMs < 100 || statusTimeoutMs > 30_000) {
    throw new Error('Next bridge 状态超时必须在 100 到 30000 毫秒之间。');
  }
  const operation = Number(operationTimeoutMs);
  if (!Number.isInteger(operation) || operation < 30_000 || operation > 600_000) {
    throw new Error('MEMOWEFT_NEXT_OPERATION_TIMEOUT_MS 必须是 30000 到 600000 的整数。');
  }
  return { statusTimeoutMs, operationTimeoutMs: operation };
}

/**
 * Build one typed adapter request from records that the 1.x server already
 * owns.  The current user turn is eligible, together with any explicit prior
 * unresolved Evidence IDs selected from recorded Next runs.  Every other
 * user/assistant message is context only.  In particular, the just-created
 * assistant reply is intentionally absent, preventing self-evidence.
 */
export function buildAdapterMemoryTurns({
  sessionId,
  record,
  previousRecords,
  evidenceRecords,
  carryForwardEvidenceIds = [],
  contextLimit = MAX_CONTEXT_RECORDS,
}) {
  const currentEvidenceId = storedUserEvidenceId(record);
  if (!currentEvidenceId) throw new Error('当前对话轮缺少已落库的用户 Evidence。');
  if (!Number.isInteger(contextLimit) || contextLimit < 0 || contextLimit > MAX_CONTEXT_RECORDS) {
    throw new Error(`contextLimit 必须是 0 到 ${MAX_CONTEXT_RECORDS} 的整数。`);
  }
  if (
    !Array.isArray(carryForwardEvidenceIds) ||
    carryForwardEvidenceIds.length > MAX_CARRY_FORWARD_EVIDENCE_IDS
  ) {
    throw new Error(`carryForwardEvidenceIds 最多允许 ${MAX_CARRY_FORWARD_EVIDENCE_IDS} 条。`);
  }
  const carried = [];
  const carriedSet = new Set();
  for (const evidenceId of carryForwardEvidenceIds) {
    if (
      typeof evidenceId !== 'string' ||
      !evidenceId ||
      evidenceId !== evidenceId.trim() ||
      evidenceId.length > 200 ||
      evidenceId === currentEvidenceId ||
      carriedSet.has(evidenceId)
    ) {
      throw new Error('carryForwardEvidenceIds 必须是唯一的先前 Evidence ID。');
    }
    carriedSet.add(evidenceId);
    carried.push(evidenceId);
  }
  carried.sort();
  if (carried.length > contextLimit) {
    throw new Error('contextLimit 不能小于显式续接的 Evidence 数量。');
  }

  const typedTurns = [];
  const usableHistory = (Array.isArray(previousRecords) ? previousRecords : []).filter(
    (item) =>
      item &&
      item.kind !== 'profile_update' &&
      item !== record &&
      item.turn !== record.turn &&
      typeof item.userInput === 'string',
  );
  const carriedOwners = new Map();
  for (const prior of usableHistory) {
    const evidenceId = storedUserEvidenceId(prior);
    if (!carriedSet.has(evidenceId)) continue;
    if (carriedOwners.has(evidenceId)) {
      throw new Error('显式续接的 Evidence ID 在历史记录中不唯一。');
    }
    carriedOwners.set(evidenceId, prior);
  }
  if (carried.some((evidenceId) => !carriedOwners.has(evidenceId))) {
    throw new Error('显式续接只允许引用先前已落库的用户 Evidence。');
  }

  const recent = new Set(usableHistory.slice(-contextLimit));
  let selectedHistory = usableHistory.filter(
    (prior) => recent.has(prior) || carriedSet.has(storedUserEvidenceId(prior)),
  );
  while (selectedHistory.length > contextLimit) {
    const removableIndex = selectedHistory.findIndex(
      (prior) => !carriedSet.has(storedUserEvidenceId(prior)),
    );
    if (removableIndex < 0) throw new Error('显式续接的 Evidence 超出 typed-turn 上限。');
    selectedHistory.splice(removableIndex, 1);
  }

  for (const prior of selectedHistory) {
    const userTurnId = historicalUserTurnId(sessionId, prior);
    typedTurns.push({
      turnId: userTurnId,
      role: 'user',
      content: prior.userInput,
      occurredAt: typedOccurredAt(prior.ts),
    });
    if (typeof prior.reply === 'string' && prior.reply) {
      typedTurns.push({
        turnId: assistantTurnId(userTurnId),
        role: 'assistant',
        content: prior.reply,
        occurredAt: typedOccurredAt(prior.ts),
      });
    }
  }

  typedTurns.push({
    turnId: currentEvidenceId,
    role: 'user',
    content: safeString(record.userInput),
    occurredAt: typedOccurredAt(record.ts),
  });

  if (!Array.isArray(evidenceRecords)) {
    throw new Error('adapter input must include the complete durable system Evidence records');
  }
  const eligibleIds = new Set([...carried, currentEvidenceId]);
  const sanitizedEvidence = evidenceRecords.map(sanitizeSystemEvidence);
  const evidenceById = new Map();
  for (const evidence of sanitizedEvidence) {
    if (evidenceById.has(evidence.id)) {
      throw new Error('eligible Evidence IDs must be unique');
    }
    evidenceById.set(evidence.id, evidence);
  }
  if (
    evidenceById.size !== eligibleIds.size ||
    [...eligibleIds].some((evidenceId) => !evidenceById.has(evidenceId))
  ) {
    throw new Error('eligible Evidence must exactly match current and carried Evidence IDs');
  }
  const typedUserById = new Map(
    typedTurns.filter((turn) => turn.role === 'user').map((turn) => [turn.turnId, turn]),
  );
  for (const evidence of sanitizedEvidence) {
    const turn = typedUserById.get(evidence.id);
    if (!turn || turn.content !== evidence.rawContent || turn.occurredAt !== evidence.occurredAt) {
      throw new Error('eligible Evidence must exactly match its typed user turn');
    }
  }
  sanitizedEvidence.sort((left, right) => left.id.localeCompare(right.id));

  return {
    operationId: `testbench:${sessionId}:${currentEvidenceId}`,
    sessionId,
    currentUserTurnId: currentEvidenceId,
    carryForwardEvidenceIds: carried,
    evidenceRecords: sanitizedEvidence,
    turns: typedTurns,
  };
}

/**
 * Re-validate a durable adapter request before a background worker delivers it.
 *
 * The operation ledger may outlive the process that originally built the
 * request.  Replaying its JSON must therefore cross the same narrow boundary
 * as a fresh request; a corrupt/local-tampered row never becomes a generic
 * proxy body.  The exact operation identity is derived from the already
 * stored Evidence rather than trusted as an arbitrary caller key.
 */
export function sanitizePreparedAdapterMemoryTurn(input) {
  if (
    !hasExactKeys(input, [
      'carryForwardEvidenceIds',
      'currentUserTurnId',
      'evidenceRecords',
      'operationId',
      'sessionId',
      'turns',
    ])
  ) {
    throw new Error('invalid prepared adapter memory turn');
  }
  const { operationId, sessionId, currentUserTurnId } = input;
  if (
    !isString(operationId, 200) ||
    !isString(sessionId, 200) ||
    !isString(currentUserTurnId, 200) ||
    operationId !== operationId.trim() ||
    sessionId !== sessionId.trim() ||
    currentUserTurnId !== currentUserTurnId.trim() ||
    operationId !== `testbench:${sessionId}:${currentUserTurnId}`
  ) {
    throw new Error('invalid prepared adapter operation identity');
  }
  if (
    !Array.isArray(input.carryForwardEvidenceIds) ||
    input.carryForwardEvidenceIds.length > MAX_CARRY_FORWARD_EVIDENCE_IDS
  ) {
    throw new Error('invalid prepared carry-forward Evidence');
  }
  const carried = [];
  const carriedSet = new Set();
  for (const evidenceId of input.carryForwardEvidenceIds) {
    if (
      !isString(evidenceId, 200) ||
      evidenceId !== evidenceId.trim() ||
      evidenceId === currentUserTurnId ||
      carriedSet.has(evidenceId)
    ) {
      throw new Error('invalid prepared carry-forward Evidence');
    }
    carriedSet.add(evidenceId);
    carried.push(evidenceId);
  }
  if (carried.some((value, index) => value !== [...carried].sort()[index])) {
    throw new Error('prepared carry-forward Evidence must be sorted');
  }
  if (!Array.isArray(input.turns) || input.turns.length < 1 || input.turns.length > 20) {
    throw new Error('invalid prepared adapter turns');
  }
  const turns = [];
  const turnIds = new Set();
  let totalCharacters = 0;
  for (const turn of input.turns) {
    if (!hasExactKeys(turn, ['content', 'occurredAt', 'role', 'turnId'])) {
      throw new Error('invalid prepared adapter turn');
    }
    if (
      !isString(turn.turnId, 200) ||
      turn.turnId !== turn.turnId.trim() ||
      turnIds.has(turn.turnId) ||
      !['user', 'assistant'].includes(turn.role) ||
      !isString(turn.content, 4_000) ||
      !isString(turn.occurredAt, 64) ||
      !Number.isFinite(Date.parse(turn.occurredAt))
    ) {
      throw new Error('invalid prepared adapter turn');
    }
    turnIds.add(turn.turnId);
    totalCharacters += turn.content.length;
    turns.push({
      turnId: turn.turnId,
      role: turn.role,
      content: turn.content,
      occurredAt: new Date(turn.occurredAt).toISOString(),
    });
  }
  if (totalCharacters > 24_000) throw new Error('prepared adapter turn content is too large');
  const current = turns.at(-1);
  if (current?.turnId !== currentUserTurnId || current.role !== 'user') {
    throw new Error('prepared current Evidence must be the final user turn');
  }
  for (const evidenceId of carried) {
    const index = turns.findIndex((turn) => turn.turnId === evidenceId);
    if (index < 0 || index === turns.length - 1 || turns[index].role !== 'user') {
      throw new Error('prepared carry-forward Evidence must reference a prior user turn');
    }
  }
  if (!Array.isArray(input.evidenceRecords)) {
    throw new Error('invalid prepared eligible Evidence');
  }
  const eligibleIds = new Set([...carried, currentUserTurnId]);
  const evidenceRecords = input.evidenceRecords.map(sanitizeSystemEvidence);
  const evidenceIds = new Set();
  const turnById = new Map(turns.map((turn) => [turn.turnId, turn]));
  for (const evidence of evidenceRecords) {
    if (evidenceIds.has(evidence.id)) throw new Error('prepared eligible Evidence must be unique');
    evidenceIds.add(evidence.id);
    const turn = turnById.get(evidence.id);
    if (
      !eligibleIds.has(evidence.id) ||
      !turn ||
      turn.role !== 'user' ||
      turn.content !== evidence.rawContent ||
      turn.occurredAt !== evidence.occurredAt
    ) {
      throw new Error('prepared eligible Evidence must exactly match its typed user turn');
    }
  }
  if (
    evidenceIds.size !== eligibleIds.size ||
    [...eligibleIds].some((evidenceId) => !evidenceIds.has(evidenceId)) ||
    evidenceRecords.some(
      (evidence, index) =>
        index > 0 && evidenceRecords[index - 1].id.localeCompare(evidence.id) >= 0,
    )
  ) {
    throw new Error('prepared eligible Evidence set is invalid or unsorted');
  }
  return {
    operationId,
    sessionId,
    currentUserTurnId,
    carryForwardEvidenceIds: carried,
    evidenceRecords,
    turns,
  };
}

function safeFailure(status, code) {
  return { status, code };
}

function isString(value, maxLength) {
  return typeof value === 'string' && value.length > 0 && value.length <= maxLength;
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function hasExactKeys(value, expected) {
  if (!isPlainObject(value)) return false;
  const keys = Object.keys(value).sort();
  return keys.length === expected.length && keys.every((key, index) => key === expected[index]);
}

function hasExplicitTimezone(value) {
  return /(?:Z|[+-]\d{2}:\d{2})$/i.test(value);
}

async function readBoundedJsonResponse(response, { maxBytes = MAX_NEXT_RESPONSE_BYTES } = {}) {
  const declaredLength = response.headers?.get?.('content-length');
  if (declaredLength !== undefined && declaredLength !== null) {
    if (!/^\d+$/.test(declaredLength) || Number(declaredLength) > maxBytes) {
      throw new Error('invalid Next response size');
    }
  }

  if (response.body && typeof response.body.getReader === 'function') {
    const reader = response.body.getReader();
    const chunks = [];
    let totalBytes = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (!(value instanceof Uint8Array)) throw new Error('invalid Next response chunk');
        totalBytes += value.byteLength;
        if (totalBytes > maxBytes) {
          await reader.cancel();
          throw new Error('Next response is too large');
        }
        chunks.push(value);
      }
      return JSON.parse(Buffer.concat(chunks.map((chunk) => Buffer.from(chunk))).toString('utf8'));
    } finally {
      reader.releaseLock();
    }
  }

  // Test doubles and older compatible fetch shims may not expose a body
  // stream. They still cross the same serialized-size boundary.
  if (typeof response.text === 'function') {
    const raw = await response.text();
    if (Buffer.byteLength(raw, 'utf8') > maxBytes) {
      throw new Error('Next response is too large');
    }
    return JSON.parse(raw);
  }
  const value = await response.json();
  if (Buffer.byteLength(JSON.stringify(value), 'utf8') > maxBytes) {
    throw new Error('Next response is too large');
  }
  return value;
}

async function readAdapterReceiptResponse(response) {
  const value = await readBoundedJsonResponse(response, {
    maxBytes: MAX_NEXT_ADAPTER_RECEIPT_BYTES,
  });
  if (!isPlainObject(value) || Object.prototype.hasOwnProperty.call(value, 'world')) {
    throw new Error('adapter response must be a bounded receipt without a World snapshot');
  }
  return value;
}

// A legacy import has no browser-visible review step.  A 2xx response is
// therefore useful only when it records one of the automatic product
// outcomes; old `{ staged: 2 }` / `candidate-ready` compatibility payloads
// must not be misreported as a completed migration.
const LEGACY_MIGRATION_TERMINAL_STATES = new Set([
  'applied',
  'no-change',
  'clarification-required',
  'out-of-scope',
  'failed',
]);

// A correction is an explicit operation, unlike a read-only query.  A 2xx
// response is useful only when it says the operation is already in one of
// the product's automatic terminal states.  In particular, do not let a
// pre-2.0 `candidate-ready`, `correction-pending`, or `staged` receipt reach
// the browser as a successful correction.
const CORRECTION_TERMINAL_STATES = LEGACY_MIGRATION_TERMINAL_STATES;

function correctionReceiptObjects(value) {
  const receipts = [];
  const seen = new Set();
  const visit = (item) => {
    if (!isPlainObject(item) || seen.has(item)) return;
    seen.add(item);
    receipts.push(item);
    // These are the only receipt wrappers emitted by the Next service.  Do
    // not traverse arbitrary proposal/world payloads merely because they may
    // contain an unrelated identifier.
    visit(item.receipt);
    visit(item.run);
    visit(item.adapter);
  };
  visit(value);
  return receipts;
}

function readCorrectionTerminal(value, operationId) {
  if (!isPlainObject(value)) throw new Error('invalid correction receipt');
  const receipts = correctionReceiptObjects(value);
  let hasTerminalState = false;
  for (const receipt of receipts) {
    if (Object.prototype.hasOwnProperty.call(receipt, 'operationId')) {
      if (receipt.operationId !== operationId) {
        throw new Error('correction receipt operationId does not match request');
      }
    }
    if (Object.prototype.hasOwnProperty.call(receipt, 'state')) {
      if (typeof receipt.state !== 'string' || !CORRECTION_TERMINAL_STATES.has(receipt.state)) {
        throw new Error('correction receipt has no automatic terminal state');
      }
      hasTerminalState = true;
    }
  }
  if (!hasTerminalState) throw new Error('correction receipt has no automatic terminal state');
  return value;
}

function readLegacyMigrationTerminal(value) {
  if (!isPlainObject(value)) throw new Error('invalid legacy migration receipt');
  const run = isPlainObject(value.run) ? value.run : null;
  const state = value.state ?? run?.state;
  if (typeof state !== 'string' || !LEGACY_MIGRATION_TERMINAL_STATES.has(state)) {
    throw new Error('legacy migration has no automatic terminal state');
  }
  return value;
}

/**
 * Legacy migration is intentionally narrower than the live typed-turn bridge.
 * It may stage only the original 1.x user Evidence text.  Derived Cognition,
 * profile text, assistant replies, and arbitrary source paths never cross this
 * boundary.
 */
function sanitizeLegacyMemoryImport(input) {
  if (!hasExactKeys(input, ['operationId', 'turns'])) throw new Error('invalid legacy import');
  if (!isString(input.operationId, MAX_LEGACY_IMPORT_OPERATION_ID_CHARS)) {
    throw new Error('invalid legacy operation');
  }
  if (
    !Array.isArray(input.turns) ||
    input.turns.length < 1 ||
    input.turns.length > MAX_LEGACY_IMPORT_TURNS
  ) {
    throw new Error('invalid legacy turns');
  }

  const seenTurnIds = new Set();
  let totalContentChars = 0;
  const turns = [];
  for (const turn of input.turns) {
    if (!hasExactKeys(turn, ['content', 'occurredAt', 'role', 'turnId'])) {
      throw new Error('invalid legacy turn');
    }
    if (
      !isString(turn.turnId, MAX_LEGACY_IMPORT_TURN_ID_CHARS) ||
      turn.role !== 'user' ||
      !isString(turn.content, MAX_LEGACY_IMPORT_TURN_CONTENT_CHARS) ||
      !isString(turn.occurredAt, MAX_LEGACY_IMPORT_OCCURRED_AT_CHARS) ||
      !hasExplicitTimezone(turn.occurredAt) ||
      !Number.isFinite(Date.parse(turn.occurredAt)) ||
      seenTurnIds.has(turn.turnId)
    ) {
      throw new Error('invalid legacy turn');
    }
    seenTurnIds.add(turn.turnId);
    totalContentChars += turn.content.length;
    if (totalContentChars > MAX_LEGACY_IMPORT_TOTAL_CONTENT_CHARS) {
      throw new Error('legacy content too large');
    }
    turns.push({
      turnId: turn.turnId,
      role: 'user',
      content: turn.content,
      occurredAt: turn.occurredAt,
    });
  }
  return { operationId: input.operationId, turns };
}

function sanitizeRecallQuery(query) {
  if (!isString(query?.trim(), MAX_RECALL_QUERY_CHARS)) {
    throw new Error('记忆召回查询不能为空且不能超过 1200 字。');
  }
  return query.trim();
}

/**
 * The pre-reply route is intentionally narrower than the general Next query:
 * only typed accepted cognitions may cross back into the 1.x reply prompt.
 * Reject every extra/malformed field so an experimental Lab response never
 * becomes an unbounded prompt or a browser-visible error body.
 */
export function validateRecallMemoryResponse(value) {
  if (!hasExactKeys(value, ['memories', 'status'])) return null;
  if (!['recalled', 'no_memory'].includes(value.status) || !Array.isArray(value.memories))
    return null;
  if (value.memories.length > MAX_RECALL_MEMORIES) return null;
  if (value.status === 'no_memory' && value.memories.length !== 0) return null;

  let totalChars = 0;
  const memories = [];
  for (const memory of value.memories) {
    if (!hasExactKeys(memory, ['confidence', 'content', 'credStatus'])) return null;
    if (
      !isString(memory.content, MAX_RECALL_MEMORY_CHARS) ||
      !Number.isInteger(memory.confidence) ||
      memory.confidence < 0 ||
      memory.confidence > 1000 ||
      !isString(memory.credStatus, MAX_RECALL_CRED_STATUS_CHARS)
    ) {
      return null;
    }
    totalChars += memory.content.length + memory.credStatus.length;
    if (totalChars > MAX_RECALL_TOTAL_CHARS) return null;
    memories.push({
      content: memory.content,
      confidence: memory.confidence,
      credStatus: memory.credStatus,
    });
  }
  return { status: value.status, memories };
}

function validateAskEvidence(value, seenIds) {
  if (!Array.isArray(value) || value.length > MAX_ASK_EVIDENCE_ITEMS) return null;
  const items = [];
  let totalChars = 0;
  for (const evidence of value) {
    if (
      !hasExactKeys(evidence, ['id', 'summary']) ||
      !isString(evidence.id, MAX_ASK_EVIDENCE_ID_CHARS) ||
      evidence.id !== evidence.id.trim() ||
      seenIds.has(evidence.id) ||
      !isString(evidence.summary, MAX_ASK_EVIDENCE_SUMMARY_CHARS)
    ) {
      return null;
    }
    totalChars += evidence.id.length + evidence.summary.length;
    if (totalChars > MAX_ASK_EVIDENCE_TOTAL_CHARS) return null;
    seenIds.add(evidence.id);
    items.push({ id: evidence.id, summary: evidence.summary });
  }
  return { items, totalChars };
}

/**
 * A proposed question is still only a read result.  Validate the entire
 * bounded projection before the host can decide to speak it, and keep the
 * low-confidence/conflict reasons coupled to their exact evidence shape.
 */
export function validateMemoryAskResponse(value) {
  if (!hasExactKeys(value, ['proposal', 'status'])) return null;
  if (value.status === 'none') {
    return value.proposal === null ? { status: 'none', proposal: null } : null;
  }
  if (value.status !== 'proposed' || !isPlainObject(value.proposal)) return null;
  const proposal = value.proposal;
  if (
    !hasExactKeys(proposal, [
      'cognitionId',
      'content',
      'contradictEvidence',
      'credStatus',
      'effectiveConfidence',
      'kind',
      'question',
      'reason',
      'storedConfidence',
      'supportEvidence',
    ]) ||
    !isString(proposal.cognitionId, 200) ||
    proposal.cognitionId !== proposal.cognitionId.trim() ||
    !isString(proposal.content, MAX_ASK_CONTENT_CHARS) ||
    !isString(proposal.question, MAX_ASK_QUESTION_CHARS) ||
    proposal.question !== proposal.question.trim() ||
    !Number.isInteger(proposal.storedConfidence) ||
    proposal.storedConfidence < 0 ||
    proposal.storedConfidence > 1000 ||
    !Number.isInteger(proposal.effectiveConfidence) ||
    proposal.effectiveConfidence < 0 ||
    proposal.effectiveConfidence > 1000 ||
    !isString(proposal.credStatus, MAX_RECALL_CRED_STATUS_CHARS)
  ) {
    return null;
  }
  const lowConfidence =
    proposal.kind === 'hypothesis' &&
    proposal.reason === 'low_confidence' &&
    ['candidate', 'low'].includes(proposal.credStatus);
  const unresolvedConflict =
    proposal.kind === 'conflict' &&
    proposal.reason === 'unresolved_conflict' &&
    proposal.credStatus === 'conflicted';
  if (!lowConfidence && !unresolvedConflict) return null;

  const seenIds = new Set();
  const support = validateAskEvidence(proposal.supportEvidence, seenIds);
  const contradict = validateAskEvidence(proposal.contradictEvidence, seenIds);
  if (
    !support ||
    !contradict ||
    support.items.length === 0 ||
    support.totalChars + contradict.totalChars > MAX_ASK_EVIDENCE_TOTAL_CHARS ||
    (lowConfidence && contradict.items.length !== 0) ||
    (unresolvedConflict && contradict.items.length === 0)
  ) {
    return null;
  }
  return {
    status: 'proposed',
    proposal: {
      cognitionId: proposal.cognitionId,
      kind: proposal.kind,
      reason: proposal.reason,
      content: proposal.content,
      question: proposal.question,
      supportEvidence: support.items,
      contradictEvidence: contradict.items,
      storedConfidence: proposal.storedConfidence,
      effectiveConfidence: proposal.effectiveConfidence,
      credStatus: proposal.credStatus,
    },
  };
}

/**
 * Keep the published 1.x TurnRecord shape while making actual 2.0 prompt
 * injection inspectable.  A 2.0 item deliberately has no invented score.
 */
export function mergeRecallForRecord(oneXRecall, twoXReplyMemory) {
  const merged = [];
  const seenContents = new Set();
  for (const item of Array.isArray(oneXRecall) ? oneXRecall : []) {
    if (!isPlainObject(item) || !isString(item.content, MAX_RECALL_MEMORY_CHARS)) continue;
    if (seenContents.has(item.content)) continue;
    seenContents.add(item.content);
    const logged = { summary: item.content, score: Math.round(Number(item.score) * 1000) };
    if (!Number.isFinite(logged.score)) delete logged.score;
    merged.push(logged);
  }
  for (const memory of Array.isArray(twoXReplyMemory) ? twoXReplyMemory : []) {
    if (!isPlainObject(memory) || !isString(memory.content, MAX_RECALL_MEMORY_CHARS)) continue;
    if (seenContents.has(memory.content)) continue;
    seenContents.add(memory.content);
    merged.push({ summary: `2.0 · ${memory.content}` });
  }
  return merged;
}

function sanitizeProxyBody(route, body) {
  const raw = body && typeof body === 'object' ? body : {};
  if (route === 'query') {
    if (!isString(raw.query?.trim(), 1200)) throw new Error('查询不能为空且不能超过 1200 字。');
    return { query: raw.query.trim() };
  }
  if (route === 'correction') {
    if (
      !isString(raw.operationId, 200) ||
      raw.operationId !== raw.operationId.trim() ||
      !isString(raw.cognitionId, 200) ||
      !isString(raw.correctionText?.trim(), 4000)
    ) {
      throw new Error('纠正需要稳定 operationId、有效的记忆 ID 与不超过 4000 字的内容。');
    }
    return {
      operationId: raw.operationId,
      cognitionId: raw.cognitionId,
      correctionText: raw.correctionText.trim(),
    };
  }
  throw new Error('不允许的 Next 请求。');
}

/**
 * A small deep module around the exact Next routes used by the upgraded
 * testbench.  It never forwards arbitrary route strings or opaque request
 * bodies, and failures expose only a stable local status/code to the UI.
 */
export function createNextBridge({
  baseUrl,
  fetchImpl = globalThis.fetch,
  statusTimeoutMs,
  operationTimeoutMs,
} = {}) {
  const origin = normalizeNextBaseUrl(baseUrl);
  if (typeof fetchImpl !== 'function') throw new Error('当前 Node 运行时不支持 fetch。');
  const timeouts = readNextBridgeTimeouts({ statusTimeoutMs, operationTimeoutMs });

  async function request(
    path,
    {
      method = 'GET',
      body,
      timeoutMs,
      headers: extraHeaders,
      responseReader = readBoundedJsonResponse,
    } = {},
  ) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const headers = { Origin: origin, ...extraHeaders };
      const init = { method, headers, signal: controller.signal };
      if (body !== undefined) {
        headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(body);
      }
      const response = await fetchImpl(`${origin}${path}`, init);
      if (!response?.ok) {
        const status = Number(response?.status);
        return Number.isInteger(status) && status >= 400 && status < 500
          ? safeFailure('failed', 'NEXT_HTTP_REQUEST_REJECTED')
          : safeFailure('unavailable', 'NEXT_HTTP_UNAVAILABLE');
      }
      try {
        return { status: 'ok', value: await responseReader(response) };
      } catch {
        // A 2xx response can arrive only after the remote handler has already
        // applied its World change.  If its body is truncated, oversized, or
        // otherwise unreadable, delivery is ambiguous rather than a safe
        // terminal rejection.  Retain and retry the exact operation request.
        return safeFailure('unavailable', 'NEXT_INVALID_RESPONSE');
      }
    } catch {
      return safeFailure('unavailable', 'NEXT_UNAVAILABLE');
    } finally {
      clearTimeout(timer);
    }
  }

  async function stagePreparedMemoryTurn(input) {
    let payload;
    try {
      payload = sanitizePreparedAdapterMemoryTurn(input);
    } catch {
      return safeFailure('failed', 'NEXT_INVALID_LOCAL_TURN');
    }
    return request('/api/adapter-memory-turns', {
      method: 'POST',
      body: payload,
      timeoutMs: timeouts.operationTimeoutMs,
      headers: { Accept: 'application/vnd.memoweft.adapter-receipt+json' },
      responseReader: readAdapterReceiptResponse,
    });
  }

  return Object.freeze({
    origin,
    statusTimeoutMs: timeouts.statusTimeoutMs,
    operationTimeoutMs: timeouts.operationTimeoutMs,
    async stageMemoryTurn(input) {
      let payload;
      try {
        payload = buildAdapterMemoryTurns(input);
      } catch {
        return safeFailure('failed', 'NEXT_INVALID_LOCAL_TURN');
      }
      return stagePreparedMemoryTurn(payload);
    },
    stagePreparedMemoryTurn,
    async stageLegacyMemory(input) {
      let body;
      try {
        body = sanitizeLegacyMemoryImport(input);
      } catch {
        return safeFailure('failed', 'NEXT_INVALID_LEGACY_IMPORT');
      }
      return request('/api/adapter-legacy-memory-imports', {
        method: 'POST',
        body,
        timeoutMs: timeouts.operationTimeoutMs,
        responseReader: async (response) =>
          readLegacyMigrationTerminal(await readBoundedJsonResponse(response)),
      });
    },
    async recallMemory(query) {
      let body;
      try {
        body = { query: sanitizeRecallQuery(query) };
      } catch {
        return safeFailure('failed', 'NEXT_INVALID_RECALL_QUERY');
      }
      const result = await request('/api/memory-recalls', {
        method: 'POST',
        body,
        // This read must not consume the long Extract/Correction model budget.
        timeoutMs: timeouts.statusTimeoutMs,
      });
      if (result.status !== 'ok') return result;
      const value = validateRecallMemoryResponse(result.value);
      return value
        ? { status: 'ok', value }
        : safeFailure('failed', 'NEXT_INVALID_RECALL_RESPONSE');
    },
    async proposeMemoryAsk(query) {
      let body;
      try {
        body = { query: sanitizeRecallQuery(query) };
      } catch {
        return safeFailure('failed', 'NEXT_INVALID_ASK_QUERY');
      }
      const result = await request('/api/memory-asks', {
        method: 'POST',
        body,
        timeoutMs: timeouts.statusTimeoutMs,
      });
      if (result.status !== 'ok') return result;
      const value = validateMemoryAskResponse(result.value);
      return value ? { status: 'ok', value } : safeFailure('failed', 'NEXT_INVALID_ASK_RESPONSE');
    },
    getStatus() {
      return request('/api/status', { timeoutMs: timeouts.statusTimeoutMs });
    },
    getMemoryWorld() {
      return request('/api/memory-world', { timeoutMs: timeouts.statusTimeoutMs });
    },
    getMemoryRuns() {
      return request('/api/memory-runs', { timeoutMs: timeouts.statusTimeoutMs });
    },
    queryMemory(body) {
      try {
        return request('/api/memory-queries', {
          method: 'POST',
          body: sanitizeProxyBody('query', body),
          timeoutMs: timeouts.operationTimeoutMs,
        });
      } catch {
        return Promise.resolve(safeFailure('failed', 'NEXT_INVALID_REQUEST'));
      }
    },
    correctMemory(body) {
      let payload;
      try {
        payload = sanitizeProxyBody('correction', body);
      } catch {
        return Promise.resolve(safeFailure('failed', 'NEXT_INVALID_REQUEST'));
      }
      return request('/api/memory-corrections', {
        method: 'POST',
        body: payload,
        timeoutMs: timeouts.operationTimeoutMs,
        responseReader: async (response) =>
          readCorrectionTerminal(await readBoundedJsonResponse(response), payload.operationId),
      });
    },
  });
}
