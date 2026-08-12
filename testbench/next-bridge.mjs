/**
 * Narrow, local-only bridge from the 1.x testbench to the MemoWeft Next lab.
 *
 * The testbench remains the chat owner: it writes 1.x Evidence, produces the
 * sole assistant reply, and keeps its profile pipeline.  This module only
 * submits the recorded user Evidence plus context to Next for a candidate
 * memory proposal.  It deliberately has no generic forward(path) escape hatch.
 */

const DEFAULT_NEXT_BASE_URL = 'http://127.0.0.1:7891';
const DEFAULT_STATUS_TIMEOUT_MS = 4_500;
const DEFAULT_OPERATION_TIMEOUT_MS = 300_000;
const MAX_NEXT_RESPONSE_BYTES = 512 * 1024;
const MAX_RECALL_QUERY_CHARS = 1_200;
const MAX_RECALL_MEMORIES = 8;
const MAX_RECALL_MEMORY_CHARS = 1_200;
const MAX_RECALL_CRED_STATUS_CHARS = 80;
const MAX_RECALL_TOTAL_CHARS = 6_000;
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
      run?.state !== 'no-candidate' ||
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

  return {
    operationId: `testbench:${sessionId}:${currentEvidenceId}`,
    sessionId,
    currentUserTurnId: currentEvidenceId,
    carryForwardEvidenceIds: carried,
    turns: typedTurns,
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

async function readBoundedJsonResponse(response) {
  const declaredLength = response.headers?.get?.('content-length');
  if (declaredLength !== undefined && declaredLength !== null) {
    if (!/^\d+$/.test(declaredLength) || Number(declaredLength) > MAX_NEXT_RESPONSE_BYTES) {
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
        if (totalBytes > MAX_NEXT_RESPONSE_BYTES) {
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
    if (Buffer.byteLength(raw, 'utf8') > MAX_NEXT_RESPONSE_BYTES) {
      throw new Error('Next response is too large');
    }
    return JSON.parse(raw);
  }
  const value = await response.json();
  if (Buffer.byteLength(JSON.stringify(value), 'utf8') > MAX_NEXT_RESPONSE_BYTES) {
    throw new Error('Next response is too large');
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
  if (route === 'decision') {
    if (
      !isString(raw.reviewId, 200) ||
      !isString(raw.runId, 200) ||
      !isString(raw.resultHash, 256) ||
      !['accept', 'reject'].includes(raw.decision)
    ) {
      throw new Error('无效的候选记忆决定。');
    }
    return {
      reviewId: raw.reviewId,
      runId: raw.runId,
      resultHash: raw.resultHash,
      decision: raw.decision,
    };
  }
  if (route === 'query') {
    if (!isString(raw.query?.trim(), 1200)) throw new Error('查询不能为空且不能超过 1200 字。');
    return { query: raw.query.trim() };
  }
  if (route === 'correction') {
    if (!isString(raw.cognitionId, 200) || !isString(raw.correctionText?.trim(), 4000)) {
      throw new Error('纠正需要有效的记忆 ID 与不超过 4000 字的内容。');
    }
    return { cognitionId: raw.cognitionId, correctionText: raw.correctionText.trim() };
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

  async function request(path, { method = 'GET', body, timeoutMs } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const headers = { Origin: origin };
      const init = { method, headers, signal: controller.signal };
      if (body !== undefined) {
        headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(body);
      }
      const response = await fetchImpl(`${origin}${path}`, init);
      if (!response?.ok) return safeFailure('unavailable', 'NEXT_HTTP_UNAVAILABLE');
      try {
        return { status: 'ok', value: await readBoundedJsonResponse(response) };
      } catch {
        return safeFailure('failed', 'NEXT_INVALID_RESPONSE');
      }
    } catch {
      return safeFailure('unavailable', 'NEXT_UNAVAILABLE');
    } finally {
      clearTimeout(timer);
    }
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
      return request('/api/adapter-memory-turns', {
        method: 'POST',
        body: payload,
        timeoutMs: timeouts.operationTimeoutMs,
      });
    },
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
    getStatus() {
      return request('/api/status', { timeoutMs: timeouts.statusTimeoutMs });
    },
    getMemoryWorld() {
      return request('/api/memory-world', { timeoutMs: timeouts.statusTimeoutMs });
    },
    getMemoryRuns() {
      return request('/api/memory-runs', { timeoutMs: timeouts.statusTimeoutMs });
    },
    decideMemory(body) {
      try {
        return request('/api/memory-decisions', {
          method: 'POST',
          body: sanitizeProxyBody('decision', body),
          timeoutMs: timeouts.operationTimeoutMs,
        });
      } catch {
        return Promise.resolve(safeFailure('failed', 'NEXT_INVALID_REQUEST'));
      }
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
      try {
        return request('/api/memory-corrections', {
          method: 'POST',
          body: sanitizeProxyBody('correction', body),
          timeoutMs: timeouts.operationTimeoutMs,
        });
      } catch {
        return Promise.resolve(safeFailure('failed', 'NEXT_INVALID_REQUEST'));
      }
    },
  });
}
