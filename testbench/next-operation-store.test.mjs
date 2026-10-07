import assert from 'node:assert/strict';
import test from 'node:test';
import { openStores } from '../src/store/openStores.ts';
import {
  NextAdapterOperationStore,
  canonicalJson,
  MAX_OPERATION_RESULT_BYTES,
} from './next-operation-store.mjs';

function prepared(overrides = {}) {
  return {
    operationId: 'op-001',
    sessionId: 'session-a',
    currentUserTurnId: 'evidence-001',
    evidenceRecords: [
      {
        id: 'evidence-001',
        subjectId: 'owner-test',
        sourceKind: 'spoken',
        hostId: 'operation-store-test',
        originId: 'origin:evidence-001',
        occurredAt: '2026-08-12T00:00:00.000Z',
        recordedAt: '2026-08-12T00:00:00.000Z',
        rawContent: 'hello',
        summary: 'hello',
        allowLocalRead: true,
        allowCloudRead: false,
        allowInference: true,
        correctsEvidenceId: null,
      },
    ],
    turns: [
      {
        turnId: 'evidence-001',
        role: 'user',
        content: 'hello',
        occurredAt: '2026-08-12T00:00:00.000Z',
      },
    ],
    carryForwardEvidenceIds: ['evidence-previous'],
    ...overrides,
  };
}

function makeStore(t) {
  const stores = openStores(':memory:');
  t.after(() => stores.close());
  return { stores, operations: new NextAdapterOperationStore(stores.db) };
}

function cognitionReplacement(overrides = {}) {
  const evidenceId = overrides.evidenceId ?? 'evidence-001';
  const target = { kind: 'relationship', id: 'relationship:lihua-xinggang' };
  const perspective = { kind: 'entity', holder_entity_ids: ['entity:owner'] };
  const before = {
    id: 'cognition:evaluation:prior',
    world_id: 'world:owner',
    target,
    content: '我觉得这段支持很可靠',
    content_type: 'fact',
    formed_by: 'stated',
    confidence: 760,
    cred_status: 'limited',
    perspective,
    sources: [{ evidence_id: 'evidence:prior', relation: 'support' }],
    scope: '李华支持星港项目',
    valid_at: null,
    invalid_at: null,
    structured_claim: {
      statement_kind: 'evaluation',
      predicate: null,
      value: '很可靠',
      polarity: 'assert',
      epistemic_status: 'asserted',
    },
  };
  const after = {
    ...before,
    id: 'cognition:evaluation:replacement',
    content: '我觉得这段支持不可靠',
    sources: [{ evidence_id: evidenceId, relation: 'support' }],
    structured_claim: { ...before.structured_claim, value: '不可靠' },
  };
  return {
    priorCognitionId: before.id,
    successorCognitionId: after.id,
    relation: 'corrects',
    evidenceId,
    before,
    after,
    ...overrides,
  };
}

function attributeCognitionReplacement(overrides = {}) {
  const evidenceId = overrides.evidenceId ?? 'evidence-001';
  const target = { kind: 'entity', id: 'entity:lihua' };
  const perspective = { kind: 'entity', holder_entity_ids: ['entity:owner'] };
  const before = {
    id: 'cognition:attribute:prior',
    world_id: 'world:owner',
    target,
    content: '李华的发型是短发。',
    content_type: 'fact',
    formed_by: 'stated',
    confidence: 600,
    cred_status: 'limited',
    perspective,
    sources: [{ evidence_id: 'evidence:prior', relation: 'support' }],
    scope: null,
    valid_at: null,
    invalid_at: null,
    structured_claim: {
      statement_kind: 'attribute',
      predicate: '发型',
      value: '短发',
      polarity: 'assert',
      epistemic_status: 'asserted',
    },
  };
  const after = {
    ...before,
    id: 'cognition:attribute:replacement',
    content: '更正：李华的发型是长发。',
    sources: [{ evidence_id: evidenceId, relation: 'support' }],
    structured_claim: { ...before.structured_claim, value: '长发' },
  };
  return {
    priorCognitionId: before.id,
    successorCognitionId: after.id,
    relation: 'corrects',
    evidenceId,
    before,
    after,
    ...overrides,
  };
}

function relationshipStatementEvidenceChange(overrides = {}) {
  const evidenceId = overrides.evidenceId ?? 'evidence-001';
  const before = {
    id: 'cognition:relationship:statement',
    world_id: 'world:owner',
    target: { kind: 'relationship', id: 'relationship:lihua-xinggang' },
    content: '李华支持星港项目',
    content_type: 'fact',
    formed_by: 'stated',
    confidence: 600,
    cred_status: 'limited',
    perspective: { kind: 'entity', holder_entity_ids: ['entity:owner'] },
    sources: [{ evidence_id: 'evidence:prior', relation: 'support' }],
    scope: null,
    valid_at: null,
    invalid_at: null,
    structured_claim: {
      statement_kind: 'relationship_statement',
      predicate: '支持',
      value: null,
      polarity: 'assert',
      epistemic_status: 'asserted',
    },
  };
  const after = {
    ...before,
    confidence: 480,
    cred_status: 'conflicted',
    sources: [...before.sources, { evidence_id: evidenceId, relation: 'contradict' }],
  };
  return {
    cognitionId: before.id,
    evidenceId,
    relation: 'contradicts',
    before,
    after,
    ...overrides,
  };
}

function eventStatementEvidenceChange(overrides = {}) {
  const evidenceId = overrides.evidenceId ?? 'evidence-001';
  const before = {
    id: 'cognition:event:statement',
    world_id: 'world:owner',
    target: { kind: 'event', id: 'event:xinggang-meeting' },
    content: '我和李华在星港会议室开会',
    content_type: 'fact',
    formed_by: 'stated',
    confidence: 600,
    cred_status: 'limited',
    perspective: { kind: 'entity', holder_entity_ids: ['entity:owner'] },
    sources: [{ evidence_id: 'evidence:prior', relation: 'support' }],
    scope: null,
    valid_at: null,
    invalid_at: null,
    structured_claim: {
      statement_kind: 'event_statement',
      predicate: '开会',
      value: null,
      polarity: 'assert',
      epistemic_status: 'asserted',
    },
  };
  const after = {
    ...before,
    confidence: 480,
    cred_status: 'conflicted',
    sources: [...before.sources, { evidence_id: evidenceId, relation: 'contradict' }],
  };
  return {
    cognitionId: before.id,
    evidenceId,
    relation: 'contradicts',
    before,
    after,
    ...overrides,
  };
}

function correctionEvolutionStep(replacement = cognitionReplacement(), overrides = {}) {
  return {
    id: 'evolution:cognition-correction',
    kind: 'cognition_change',
    relation: 'corrects',
    subject: replacement.before?.target ?? cognitionReplacement().before.target,
    predecessor_ids: [replacement.priorCognitionId],
    successor_ids: [replacement.successorCognitionId],
    effective_at: '2026-08-12T00:00:00.000Z',
    evidence_ids: [replacement.evidenceId],
    ...overrides,
  };
}

test('canonical JSON is key-order invariant and exact concurrent-style reservations are idempotent', (t) => {
  const { operations } = makeStore(t);
  assert.equal(
    canonicalJson({ z: [2, { b: 1, a: 2 }], a: true }),
    '{"a":true,"z":[2,{"a":2,"b":1}]}',
  );
  const one = operations.reserve(prepared());
  const two = operations.reserve({
    carryForwardEvidenceIds: ['evidence-previous'],
    turns: prepared().turns,
    evidenceRecords: prepared().evidenceRecords,
    currentUserTurnId: 'evidence-001',
    sessionId: 'session-a',
    operationId: 'op-001',
  });
  assert.equal(one.status, 'pending');
  assert.deepEqual(two, one);
  assert.equal(operations.listRecoverable().length, 1);
});

test('payload mismatch and a second operation for one session Evidence are rejected', (t) => {
  const { operations } = makeStore(t);
  operations.reserve(prepared());
  assert.throws(
    () => operations.reserve(prepared({ carryForwardEvidenceIds: [] })),
    /different prepared payload/,
  );
  assert.throws(
    () => operations.reserve(prepared({ operationId: 'op-002' })),
    /already reserved by another operation/,
  );
  assert.throws(
    () =>
      operations.reserve(
        prepared({
          evidenceRecords: [
            { ...prepared().evidenceRecords[0], summary: 'same text, changed provenance' },
          ],
        }),
      ),
    /different prepared payload/,
  );
});

test('state evolution keeps request only while recoverable and bounds the ready result', (t) => {
  const { stores, operations } = makeStore(t);
  operations.reserve(prepared());
  const running = operations.markRunning('op-001', 'session-a');
  assert.equal(running.status, 'running');
  assert.equal(
    operations.markPending('op-001', 'session-a', { claimToken: running.claimToken }).status,
    'pending',
  );
  const interrupted = operations.markRunning('op-001', 'session-a', { leaseMs: 1_000 });
  assert.equal(operations.listRecoverable('session-a').length, 0);
  assert.equal(operations.recoverExpiredRunning('session-a'), 0);
  assert.ok(Number.isSafeInteger(interrupted.claimExpiresAtMs));
  stores.db
    .prepare(`UPDATE next_adapter_operation SET claim_expires_at_ms = 1 WHERE operation_id = ?`)
    .run('op-001');
  assert.equal(operations.recoverExpiredRunning('session-a'), 1);
  const restarted = operations.listRecoverable('session-a');
  assert.equal(restarted.length, 1);
  assert.equal(restarted[0].status, 'pending');
  assert.deepEqual(restarted[0].request, prepared());
  const readyClaim = operations.markRunning('op-001', 'session-a');
  const ready = operations.markReady(
    'op-001',
    'session-a',
    {
      status: 'ok',
      state: 'applied',
      applied: { revision: 1, changeHash: 'sha256:change-1' },
    },
    { claimToken: readyClaim.claimToken },
  );
  assert.equal(ready.status, 'ready');
  assert.deepEqual(ready.safeNextMemory, {
    applied: { changeHash: 'sha256:change-1', revision: 1 },
    state: 'applied',
    status: 'ok',
  });
  assert.equal('request' in ready, false);
  const raw = stores.db
    .prepare(
      'SELECT request_json, safe_next_memory_json FROM next_adapter_operation WHERE operation_id = ?',
    )
    .get('op-001');
  assert.equal(raw.request_json, null);
  assert.ok(raw.safe_next_memory_json);
  assert.throws(
    () =>
      operations.markReady('op-001', 'session-a', {
        value: 'x'.repeat(MAX_OPERATION_RESULT_BYTES),
      }),
    /terminal operation cannot be changed/,
  );
});

test('a live leased operation is not adoptable by a second store during overlapping startup', (t) => {
  const root = openStores(':memory:');
  t.after(() => root.close());
  const first = new NextAdapterOperationStore(root.db, { workerId: 'worker-a' });
  first.reserve(prepared());
  first.markRunning('op-001', 'session-a');
  const second = new NextAdapterOperationStore(root.db, { workerId: 'worker-b' });
  assert.equal(second.recoverExpiredRunning(), 0);
  assert.equal(second.listRecoverable().length, 0);
  assert.throws(() => second.markRunning('op-001', 'session-a'), /already claimed/);
});

test('SQLite lease guards fence a pre-lease binary throughout an overlapping upgrade', (t) => {
  const root = openStores(':memory:');
  t.after(() => root.close());
  root.db.exec(`
    CREATE TABLE next_adapter_operation (
      operation_id TEXT PRIMARY KEY NOT NULL,
      session_id TEXT NOT NULL,
      current_user_turn_id TEXT NOT NULL,
      status TEXT NOT NULL,
      request_hash TEXT NOT NULL,
      request_json TEXT,
      safe_next_memory_json TEXT,
      failure_code TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE (session_id, current_user_turn_id)
    );
    INSERT INTO next_adapter_operation (
      operation_id, session_id, current_user_turn_id, status, request_hash, request_json,
      safe_next_memory_json, failure_code, created_at, updated_at
    ) VALUES (
      'legacy-running', 'legacy-session', 'legacy-evidence', 'running', 'legacy-hash', '{}',
      NULL, NULL, '2026-08-12T00:00:00.000Z', '2026-08-12T00:00:00.000Z'
    );
  `);
  const operations = new NextAdapterOperationStore(root.db, {
    clock: () => '2026-08-12T00:00:01.000Z',
    workerId: 'current-worker',
  });
  const migrated = root.db
    .prepare(
      `SELECT status, claim_owner, claim_token, claim_expires_at_ms
       FROM next_adapter_operation WHERE operation_id = 'legacy-running'`,
    )
    .get();
  assert.equal(migrated.status, 'running');
  assert.equal(migrated.claim_owner, 'legacy-unowned');
  assert.equal(migrated.claim_token, 'legacy-unfenced');
  assert.ok(Number(migrated.claim_expires_at_ms) > Date.parse('2026-08-12T00:00:01.000Z'));

  // A callback from the already-loaded old binary would leave lease columns
  // untouched.  The database itself rejects that late terminal overwrite.
  assert.throws(
    () =>
      root.db
        .prepare(
          `UPDATE next_adapter_operation
           SET status = 'ready', request_json = NULL, safe_next_memory_json = '{}',
               failure_code = NULL, updated_at = ?
           WHERE operation_id = ? AND status IN ('pending', 'running')`,
        )
        .run('2026-08-12T00:00:02.000Z', 'legacy-running'),
    /state invalid/,
  );

  operations.reserve(
    prepared({
      operationId: 'legacy-claim-attempt',
      sessionId: 'legacy-session-2',
      currentUserTurnId: 'legacy-evidence-2',
      turns: [{ ...prepared().turns[0], turnId: 'legacy-evidence-2' }],
    }),
  );
  // The old markRunning SQL has no owner/token fields and cannot acquire a
  // newly reserved operation once the migration transaction commits.
  assert.throws(
    () =>
      root.db
        .prepare(
          `UPDATE next_adapter_operation SET status = 'running', updated_at = ?
           WHERE operation_id = ? AND status = 'pending'`,
        )
        .run('2026-08-12T00:00:02.000Z', 'legacy-claim-attempt'),
    /lease state invalid/,
  );
  assert.equal(operations.get('legacy-claim-attempt', 'legacy-session-2').status, 'pending');
});

test('lease renewal prevents startup theft and an expired token is fenced after takeover', (t) => {
  const root = openStores(':memory:');
  t.after(() => root.close());
  let nowMs = Date.parse('2026-08-12T00:00:00.000Z');
  const clock = () => new Date(nowMs).toISOString();
  const first = new NextAdapterOperationStore(root.db, {
    clock,
    workerId: 'worker-a',
    tokenFactory: () => 'token-a',
  });
  const second = new NextAdapterOperationStore(root.db, {
    clock,
    workerId: 'worker-b',
    tokenFactory: () => 'token-b',
  });
  first.reserve(prepared());
  const claimedByA = first.markRunning('op-001', 'session-a', { leaseMs: 10_000 });

  nowMs += 9_000;
  const renewed = first.renewLease('op-001', 'session-a', claimedByA.claimToken, {
    leaseMs: 10_000,
  });
  assert.equal(second.recoverExpiredRunning(), 0);
  assert.equal(second.listRecoverable().length, 0);
  assert.equal(renewed.claimExpiresAtMs, nowMs + 10_000);

  nowMs += 10_001;
  assert.equal(second.recoverExpiredRunning(), 1);
  const claimedByB = second.markRunning('op-001', 'session-a', { leaseMs: 10_000 });
  assert.equal(claimedByB.claimToken, 'token-b');
  assert.throws(
    () => first.renewLease('op-001', 'session-a', claimedByA.claimToken),
    /expired or changed/,
  );
  assert.throws(
    () => first.markPending('op-001', 'session-a', { claimToken: claimedByA.claimToken }),
    /another worker/,
  );
  assert.throws(
    () =>
      first.markReady(
        'op-001',
        'session-a',
        { status: 'ok', state: 'applied' },
        { claimToken: claimedByA.claimToken },
      ),
    /another worker/,
  );
  assert.throws(
    () =>
      first.markFailed('op-001', 'session-a', 'NEXT_TIMEOUT', {
        claimToken: claimedByA.claimToken,
      }),
    /another worker/,
  );
  const ready = second.markReady(
    'op-001',
    'session-a',
    { status: 'ok', state: 'applied' },
    { claimToken: claimedByB.claimToken },
  );
  assert.equal(ready.status, 'ready');
});

test('failure is terminal, retains only its stable code, and exact reservation replay is idempotent', (t) => {
  const { stores, operations } = makeStore(t);
  operations.reserve(prepared());
  const failed = operations.markFailed('op-001', 'session-a', 'NEXT_TIMEOUT');
  assert.equal(failed.status, 'failed');
  assert.equal(failed.failureCode, 'NEXT_TIMEOUT');
  assert.equal('request' in failed, false);
  const raw = stores.db
    .prepare(
      'SELECT request_json, safe_next_memory_json, failure_code FROM next_adapter_operation WHERE operation_id = ?',
    )
    .get('op-001');
  assert.equal(raw.request_json, null);
  assert.equal(raw.safe_next_memory_json, null);
  assert.equal(raw.failure_code, 'NEXT_TIMEOUT');
  assert.throws(
    () => operations.markRunning('op-001', 'session-a'),
    /terminal operation cannot be replayed/,
  );
  assert.deepEqual(operations.reserve(prepared()), failed);
});

test('an automatic ready result is immutable and exact reservation replay returns its original receipt', (t) => {
  const { operations } = makeStore(t);
  operations.reserve(prepared());
  const running = operations.markRunning('op-001', 'session-a');
  operations.markReady(
    'op-001',
    'session-a',
    {
      status: 'ok',
      state: 'applied',
      memoryChange: { revision: 1, resultHash: 'hash-1' },
    },
    { claimToken: running.claimToken },
  );
  const ready = operations.get('op-001', 'session-a');
  assert.equal(ready.status, 'ready');
  assert.equal(ready.safeNextMemory.state, 'applied');
  assert.equal('request' in ready, false);
  assert.throws(
    () => operations.markReady('op-001', 'session-a', { status: 'ok', state: 'no-change' }),
    /terminal operation cannot be changed/,
  );
  assert.deepEqual(operations.reserve(prepared()), ready);
});

test('ready receipts persist a canonical hash with their JSON in one terminal transition', (t) => {
  const { stores, operations } = makeStore(t);
  operations.reserve(prepared());
  const running = operations.markRunning('op-001', 'session-a');
  const ready = operations.markReady(
    'op-001',
    'session-a',
    {
      status: 'ok',
      state: 'applied',
      memoryProposal: {
        resultHash: 'sha256:adapter-receipt',
        currentEvidenceId: 'evidence-001',
        cognitionEvidenceChanges: [
          {
            cognitionId: 'cognition:one',
            evidenceId: 'evidence:two',
            relation: 'contradicts',
            before: { id: 'cognition:one', confidence: 600 },
            after: { id: 'cognition:one', confidence: 480 },
          },
        ],
        cognitionReplacements: [cognitionReplacement()],
        evolutionSteps: [correctionEvolutionStep()],
      },
    },
    { claimToken: running.claimToken },
  );
  assert.equal(ready.status, 'ready');
  const raw = stores.db
    .prepare(
      `SELECT safe_next_memory_json, safe_next_memory_hash
       FROM next_adapter_operation WHERE operation_id = ?`,
    )
    .get('op-001');
  assert.match(raw.safe_next_memory_hash, /^sha256:[a-f0-9]{64}$/);
  assert.ok(raw.safe_next_memory_json.includes('"cognitionEvidenceChanges"'));
  assert.ok(raw.safe_next_memory_json.includes('"cognitionReplacements"'));
});

test('tampered ready JSON is quarantined and remains isolated after reopen', (t) => {
  const { stores, operations } = makeStore(t);
  operations.reserve(prepared());
  const running = operations.markRunning('op-001', 'session-a');
  operations.markReady(
    'op-001',
    'session-a',
    { status: 'ok', state: 'applied', memoryProposal: { resultHash: 'sha256:original' } },
    { claimToken: running.claimToken },
  );
  stores.db
    .prepare(`UPDATE next_adapter_operation SET safe_next_memory_json = ? WHERE operation_id = ?`)
    .run(canonicalJson({ status: 'ok', state: 'no-change' }), 'op-001');
  assert.throws(
    () => operations.get('op-001', 'session-a'),
    /safe nextMemory does not match its durable hash/,
  );
  const isolated = operations.get('op-001', 'session-a');
  assert.equal(isolated.status, 'failed');
  assert.equal(isolated.failureCode, 'NEXT_OPERATION_STATE_INVALID');
  const reopened = new NextAdapterOperationStore(stores.db);
  assert.equal(reopened.get('op-001', 'session-a').status, 'failed');
});

test('tampered ready hash is quarantined during startup before it can replay', (t) => {
  const { stores, operations } = makeStore(t);
  operations.reserve(prepared());
  const running = operations.markRunning('op-001', 'session-a');
  operations.markReady(
    'op-001',
    'session-a',
    { status: 'ok', state: 'applied' },
    { claimToken: running.claimToken },
  );
  stores.db
    .prepare(`UPDATE next_adapter_operation SET safe_next_memory_hash = ? WHERE operation_id = ?`)
    .run(`sha256:${'a'.repeat(64)}`, 'op-001');
  const reopened = new NextAdapterOperationStore(stores.db);
  const isolated = reopened.get('op-001', 'session-a');
  assert.equal(isolated.status, 'failed');
  assert.equal(isolated.failureCode, 'NEXT_OPERATION_STATE_INVALID');
});

test('hashless legacy ready receipts are quarantined rather than retroactively signed', (t) => {
  const root = openStores(':memory:');
  t.after(() => root.close());
  root.db.exec(`
    CREATE TABLE next_adapter_operation (
      operation_id TEXT PRIMARY KEY NOT NULL,
      session_id TEXT NOT NULL,
      current_user_turn_id TEXT NOT NULL,
      status TEXT NOT NULL,
      request_hash TEXT NOT NULL,
      request_json TEXT,
      safe_next_memory_json TEXT,
      failure_code TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE (session_id, current_user_turn_id)
    );
    INSERT INTO next_adapter_operation (
      operation_id, session_id, current_user_turn_id, status, request_hash, request_json,
      safe_next_memory_json, failure_code, created_at, updated_at
    ) VALUES (
      'legacy-ready', 'legacy-session', 'legacy-evidence', 'ready', 'legacy-hash', NULL,
      '{"state":"applied","status":"ok"}', NULL,
      '2026-08-12T00:00:00.000Z', '2026-08-12T00:00:00.000Z'
    );
    INSERT INTO next_adapter_operation (
      operation_id, session_id, current_user_turn_id, status, request_hash, request_json,
      safe_next_memory_json, failure_code, created_at, updated_at
    ) VALUES (
      'legacy-forged-result-hash', 'legacy-session-2', 'legacy-evidence-2', 'ready', 'legacy-hash-2', NULL,
      '{"memoryProposal":{"resultHash":"sha256:forged-result"},"state":"applied","status":"ok"}', NULL,
      '2026-08-12T00:00:00.000Z', '2026-08-12T00:00:00.000Z'
    );
  `);
  const operations = new NextAdapterOperationStore(root.db);
  for (const [operationId, sessionId] of [
    ['legacy-ready', 'legacy-session'],
    ['legacy-forged-result-hash', 'legacy-session-2'],
  ]) {
    const isolated = operations.get(operationId, sessionId);
    assert.equal(isolated.status, 'failed');
    assert.equal(isolated.failureCode, 'NEXT_OPERATION_STATE_INVALID');
  }
  const raw = root.db
    .prepare(
      `SELECT safe_next_memory_json, safe_next_memory_hash
       FROM next_adapter_operation WHERE operation_id = ?`,
    )
    .get('legacy-forged-result-hash');
  assert.equal(raw.safe_next_memory_json, null);
  assert.equal(raw.safe_next_memory_hash, null);
});

test('terminal result validation accepts legacy omissions but rejects malformed cognition evidence changes', (t) => {
  const { operations } = makeStore(t);
  operations.reserve(prepared());
  const running = operations.markRunning('op-001', 'session-a');
  assert.throws(
    () =>
      operations.markReady(
        'op-001',
        'session-a',
        { status: 'ok', memoryProposal: {} },
        { claimToken: running.claimToken },
      ),
    /terminal state/,
  );
  assert.throws(
    () =>
      operations.markReady(
        'op-001',
        'session-a',
        { status: 'ok', state: 'applied', memoryProposal: { resultHash: 1 } },
        { claimToken: running.claimToken },
      ),
    /resultHash/,
  );
  assert.throws(
    () =>
      operations.markReady(
        'op-001',
        'session-a',
        {
          status: 'ok',
          state: 'applied',
          memoryProposal: { cognitionEvidenceChanges: Array.from({ length: 13 }, () => ({})) },
        },
        { claimToken: running.claimToken },
      ),
    /bounded terminal shape/,
  );
  assert.throws(
    () =>
      operations.markReady(
        'op-001',
        'session-a',
        {
          status: 'ok',
          state: 'applied',
          memoryProposal: {
            cognitionEvidenceChanges: [
              {
                cognitionId: 'cognition:one',
                evidenceId: 'evidence:two',
                relation: 'reaffirms',
                before: { id: 'cognition:one' },
                after: { id: 'cognition:other' },
              },
            ],
          },
        },
        { claimToken: running.claimToken },
      ),
    /do not match cognitionId/,
  );
  assert.equal(
    operations.markReady(
      'op-001',
      'session-a',
      {
        status: 'ok',
        state: 'applied',
        memoryProposal: {
          cognitionEvidenceChanges: Array.from({ length: 12 }, (_, index) => {
            const cognitionId = `cognition:${index}`;
            return {
              cognitionId,
              evidenceId: `evidence:${index}`,
              relation: index % 2 === 0 ? 'contradicts' : 'reaffirms',
              before: { id: cognitionId },
              after: { id: cognitionId },
            };
          }),
        },
      },
      { claimToken: running.claimToken },
    ).status,
    'ready',
  );
});

test('terminal result validates a complete Relationship-statement Evidence change', (t) => {
  const validStore = makeStore(t).operations;
  validStore.reserve(prepared());
  const validRunning = validStore.markRunning('op-001', 'session-a');
  const change = relationshipStatementEvidenceChange();
  assert.equal(
    validStore.markReady(
      'op-001',
      'session-a',
      {
        status: 'ok',
        state: 'applied',
        memoryProposal: {
          currentEvidenceId: 'evidence-001',
          cognitionEvidenceChanges: [change],
        },
      },
      { claimToken: validRunning.claimToken },
    ).status,
    'ready',
  );

  const invalidStore = makeStore(t).operations;
  invalidStore.reserve(prepared({ operationId: 'op-relationship-statement-fork' }));
  const invalidRunning = invalidStore.markRunning('op-relationship-statement-fork', 'session-a');
  const predicateFork = relationshipStatementEvidenceChange();
  predicateFork.after = {
    ...predicateFork.after,
    structured_claim: {
      ...predicateFork.after.structured_claim,
      predicate: '反对',
    },
  };
  assert.throws(
    () =>
      invalidStore.markReady(
        'op-relationship-statement-fork',
        'session-a',
        {
          status: 'ok',
          state: 'applied',
          memoryProposal: {
            currentEvidenceId: 'evidence-001',
            cognitionEvidenceChanges: [predicateFork],
          },
        },
        { claimToken: invalidRunning.claimToken },
      ),
    /preserve its structured proposition/,
  );
});

test('terminal result validates a complete Event-statement Evidence change', (t) => {
  const validStore = makeStore(t).operations;
  validStore.reserve(prepared());
  const validRunning = validStore.markRunning('op-001', 'session-a');
  const change = eventStatementEvidenceChange();
  assert.equal(
    validStore.markReady(
      'op-001',
      'session-a',
      {
        status: 'ok',
        state: 'applied',
        memoryProposal: {
          currentEvidenceId: 'evidence-001',
          cognitionEvidenceChanges: [change],
        },
      },
      { claimToken: validRunning.claimToken },
    ).status,
    'ready',
  );

  const invalidStore = makeStore(t).operations;
  invalidStore.reserve(prepared({ operationId: 'op-event-statement-fork' }));
  const invalidRunning = invalidStore.markRunning('op-event-statement-fork', 'session-a');
  const predicateFork = eventStatementEvidenceChange();
  predicateFork.after = {
    ...predicateFork.after,
    structured_claim: {
      ...predicateFork.after.structured_claim,
      predicate: '聚餐',
    },
  };
  assert.throws(
    () =>
      invalidStore.markReady(
        'op-event-statement-fork',
        'session-a',
        {
          status: 'ok',
          state: 'applied',
          memoryProposal: {
            currentEvidenceId: 'evidence-001',
            cognitionEvidenceChanges: [predicateFork],
          },
        },
        { claimToken: invalidRunning.claimToken },
      ),
    /preserve its structured proposition/,
  );
});

test('terminal result validation strictly binds typed cognition replacements to one supported transition', (t) => {
  const { operations } = makeStore(t);
  operations.reserve(prepared());
  const running = operations.markRunning('op-001', 'session-a');
  const attempt = (replacement, evolutionSteps = [correctionEvolutionStep(replacement)]) =>
    operations.markReady(
      'op-001',
      'session-a',
      {
        status: 'ok',
        state: 'applied',
        memoryProposal: {
          currentEvidenceId: 'evidence-001',
          cognitionReplacements: [replacement],
          evolutionSteps,
        },
      },
      { claimToken: running.claimToken },
    );

  assert.throws(
    () =>
      operations.markReady(
        'op-001',
        'session-a',
        {
          status: 'ok',
          state: 'applied',
          memoryProposal: {
            currentEvidenceId: 'evidence-001',
            cognitionReplacements: Array.from({ length: 13 }, () => cognitionReplacement()),
            evolutionSteps: [],
          },
        },
        { claimToken: running.claimToken },
      ),
    /cognitionReplacements.*bounded terminal shape/,
  );
  assert.throws(
    () =>
      attempt(
        cognitionReplacement({
          successorCognitionId: 'cognition:evaluation:prior',
          after: {
            ...cognitionReplacement().after,
            id: 'cognition:evaluation:prior',
          },
        }),
      ),
    /distinct cognition IDs/,
  );
  assert.throws(
    () => attempt(cognitionReplacement({ relation: 'supersedes' })),
    /relation is invalid/,
  );
  assert.throws(
    () => attempt(cognitionReplacement({ evidenceId: 'evidence:other' })),
    /not the current Evidence/,
  );
  assert.throws(
    () =>
      attempt(
        cognitionReplacement({
          after: { ...cognitionReplacement().after, world_id: 'world:other' },
        }),
      ),
    /same world, target, perspective, and content_type/,
  );
  assert.throws(
    () =>
      attempt(
        cognitionReplacement({
          after: {
            ...cognitionReplacement().after,
            structured_claim: {
              ...cognitionReplacement().after.structured_claim,
              statement_kind: 'attribute',
            },
          },
        }),
      ),
    /structured Attribute predicate|structured statement kind/,
  );
  assert.throws(
    () =>
      attempt(
        cognitionReplacement({
          after: {
            ...cognitionReplacement().after,
            structured_claim: { ...cognitionReplacement().before.structured_claim },
          },
        }),
      ),
    /different structured cognition values/,
  );
  assert.throws(
    () =>
      attempt(
        cognitionReplacement({
          after: {
            ...cognitionReplacement().after,
            sources: [{ evidence_id: 'evidence:unrelated', relation: 'support' }],
          },
        }),
      ),
    /current Evidence support/,
  );
  assert.throws(
    () =>
      attempt(
        cognitionReplacement({
          before: { id: 'cognition:evaluation:prior' },
        }),
      ),
    /complete cognition snapshot/,
  );

  const missingEvidenceIds = correctionEvolutionStep();
  delete missingEvidenceIds.evidence_ids;
  assert.throws(
    () => attempt(cognitionReplacement(), [missingEvidenceIds]),
    /evidence_ids must contain one ID/,
  );
  assert.throws(() => attempt(cognitionReplacement(), []), /must be one-to-one/);
  assert.throws(
    () =>
      operations.markReady(
        'op-001',
        'session-a',
        {
          status: 'ok',
          state: 'applied',
          memoryProposal: {
            currentEvidenceId: 'evidence-001',
            cognitionReplacements: [cognitionReplacement()],
          },
        },
        { claimToken: running.claimToken },
      ),
    /must be one-to-one/,
  );
  assert.throws(
    () =>
      attempt(cognitionReplacement(), [
        correctionEvolutionStep(cognitionReplacement(), {
          successor_ids: ['cognition:evaluation:wrong-successor'],
        }),
      ]),
    /must be one-to-one/,
  );
  assert.throws(
    () =>
      attempt(cognitionReplacement(), [
        correctionEvolutionStep(cognitionReplacement(), {
          subject: { kind: 'relationship', id: 'relationship:wrong-target' },
        }),
      ]),
    /must be one-to-one/,
  );
  assert.throws(
    () =>
      attempt(cognitionReplacement(), [
        correctionEvolutionStep(),
        correctionEvolutionStep(cognitionReplacement(), {
          predecessor_ids: ['cognition:evaluation:orphan-prior'],
          successor_ids: ['cognition:evaluation:orphan-successor'],
        }),
      ]),
    /must be one-to-one/,
  );

  const secondReplacement = cognitionReplacement({
    priorCognitionId: 'cognition:evaluation:second-prior',
    successorCognitionId: 'cognition:evaluation:second-successor',
    before: {
      ...cognitionReplacement().before,
      id: 'cognition:evaluation:second-prior',
    },
    after: {
      ...cognitionReplacement().after,
      id: 'cognition:evaluation:second-successor',
    },
  });
  assert.throws(
    () =>
      operations.markReady(
        'op-001',
        'session-a',
        {
          status: 'ok',
          state: 'applied',
          memoryProposal: {
            currentEvidenceId: 'evidence-001',
            cognitionReplacements: [cognitionReplacement(), cognitionReplacement()],
            evolutionSteps: [correctionEvolutionStep(), correctionEvolutionStep()],
          },
        },
        { claimToken: running.claimToken },
      ),
    /duplicate cognition transition/,
  );
  assert.throws(
    () =>
      operations.markReady(
        'op-001',
        'session-a',
        {
          status: 'ok',
          state: 'applied',
          memoryProposal: {
            currentEvidenceId: 'evidence-001',
            cognitionReplacements: [cognitionReplacement(), secondReplacement],
            evolutionSteps: [correctionEvolutionStep()],
          },
        },
        { claimToken: running.claimToken },
      ),
    /must be one-to-one/,
  );

  const tooLongContent = cognitionReplacement();
  tooLongContent.after = { ...tooLongContent.after, content: 'x'.repeat(4_001) };
  assert.throws(() => attempt(tooLongContent), /content.*up to 4000 characters/);
  const tooLongValue = cognitionReplacement();
  tooLongValue.after = {
    ...tooLongValue.after,
    structured_claim: { ...tooLongValue.after.structured_claim, value: 'x'.repeat(4_001) },
  };
  assert.throws(() => attempt(tooLongValue), /structured cognition value.*up to 4000 characters/);
  const tooLongId = cognitionReplacement({
    successorCognitionId: `cognition:${'x'.repeat(231)}`,
    after: {
      ...cognitionReplacement().after,
      id: `cognition:${'x'.repeat(231)}`,
    },
  });
  assert.throws(() => attempt(tooLongId), /up to 240 characters|complete cognition snapshot/);

  const longReplacement = cognitionReplacement({
    before: {
      ...cognitionReplacement().before,
      content: '前'.repeat(4_000),
      sources: Array.from({ length: 13 }, (_, index) => ({
        evidence_id: `evidence:prior:${index}`,
        relation: index % 2 === 0 ? 'support' : 'contradict',
      })),
      structured_claim: {
        ...cognitionReplacement().before.structured_claim,
        value: '旧'.repeat(4_000),
      },
    },
    after: {
      ...cognitionReplacement().after,
      content: '后'.repeat(4_000),
      structured_claim: {
        ...cognitionReplacement().after.structured_claim,
        value: '新'.repeat(4_000),
      },
    },
  });
  assert.equal(attempt(longReplacement).status, 'ready');
  assert.deepEqual(
    operations.get('op-001', 'session-a').safeNextMemory.memoryProposal.cognitionReplacements,
    [longReplacement],
  );
  assert.equal(longReplacement.before.sources.length, 13);
});

test('terminal result accepts exact Attribute replacement and rejects a predicate fork', (t) => {
  const validStore = makeStore(t).operations;
  validStore.reserve(prepared());
  const validRunning = validStore.markRunning('op-001', 'session-a');
  const replacement = attributeCognitionReplacement();
  const ready = validStore.markReady(
    'op-001',
    'session-a',
    {
      status: 'ok',
      state: 'applied',
      memoryProposal: {
        currentEvidenceId: 'evidence-001',
        cognitionReplacements: [replacement],
        evolutionSteps: [correctionEvolutionStep(replacement)],
      },
    },
    { claimToken: validRunning.claimToken },
  );
  assert.equal(ready.status, 'ready');

  const invalidStore = makeStore(t).operations;
  invalidStore.reserve(prepared({ operationId: 'op-attribute-fork' }));
  const invalidRunning = invalidStore.markRunning('op-attribute-fork', 'session-a');
  const predicateFork = attributeCognitionReplacement();
  predicateFork.after = {
    ...predicateFork.after,
    structured_claim: {
      ...predicateFork.after.structured_claim,
      predicate: '爱好',
    },
  };
  assert.throws(
    () =>
      invalidStore.markReady(
        'op-attribute-fork',
        'session-a',
        {
          status: 'ok',
          state: 'applied',
          memoryProposal: {
            currentEvidenceId: 'evidence-001',
            cognitionReplacements: [predicateFork],
            evolutionSteps: [correctionEvolutionStep(predicateFork)],
          },
        },
        { claimToken: invalidRunning.claimToken },
      ),
    /preserve.*Attribute predicate/,
  );
});

test('legacy terminals may omit both correction projections but orphan corrects steps are rejected', (t) => {
  const { operations } = makeStore(t);
  operations.reserve(prepared());
  const running = operations.markRunning('op-001', 'session-a');
  assert.throws(
    () =>
      operations.markReady(
        'op-001',
        'session-a',
        {
          status: 'ok',
          state: 'applied',
          memoryProposal: { evolutionSteps: [correctionEvolutionStep()] },
        },
        { claimToken: running.claimToken },
      ),
    /must be one-to-one/,
  );
  assert.equal(
    operations.markReady(
      'op-001',
      'session-a',
      { status: 'ok', state: 'applied', memoryProposal: {} },
      { claimToken: running.claimToken },
    ).status,
    'ready',
  );
});

test('get is strictly isolated by session and validates terminal input boundaries', (t) => {
  const { operations } = makeStore(t);
  operations.reserve(prepared());
  assert.equal(operations.get('op-001', 'session-b'), null);
  assert.throws(
    () => operations.markFailed('op-001', 'session-a', 'lowercase'),
    /stable uppercase/,
  );
  assert.throws(
    () => operations.markReady('op-001', 'session-a', ['not', 'an', 'object']),
    /JSON object/,
  );
  assert.throws(
    () =>
      operations.markReady('op-001', 'session-a', {
        value: 'x'.repeat(MAX_OPERATION_RESULT_BYTES),
      }),
    /bounded result size/,
  );
});

test('a locally corrupted durable request is quarantined without poisoning valid work', (t) => {
  const { stores, operations } = makeStore(t);
  operations.reserve(prepared());
  operations.reserve(
    prepared({
      operationId: 'op-002',
      sessionId: 'session-b',
      currentUserTurnId: 'evidence-002',
      turns: [{ ...prepared().turns[0], turnId: 'evidence-002' }],
    }),
  );
  stores.db
    .prepare('UPDATE next_adapter_operation SET request_json = ? WHERE operation_id = ?')
    .run(
      canonicalJson(prepared({ turns: [{ ...prepared().turns[0], content: 'tampered' }] })),
      'op-001',
    );
  const recoverable = operations.listRecoverable();
  assert.deepEqual(
    recoverable.map((item) => item.operationId),
    ['op-002'],
  );
  const quarantined = operations.get('op-001', 'session-a');
  assert.equal(quarantined.status, 'failed');
  assert.equal(quarantined.failureCode, 'NEXT_OPERATION_STATE_INVALID');
});
