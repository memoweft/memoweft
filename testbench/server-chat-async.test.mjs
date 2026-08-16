import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

test('chat reply and JSONL are durable while the exact Next operation is still pending', async (t) => {
  const root = await mkdtemp(join(tmpdir(), 'memoweft-chat-async-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const dbPath = join(root, 'testbench-evidence.db');
  const logDir = join(root, 'logs');

  const script = String.raw`
    import { readFile } from 'node:fs/promises';
    import { Readable } from 'node:stream';

    function jsonResponse(value, ok = true, status = ok ? 200 : 503) {
      return { ok, status, headers: { get() { return null; } }, async json() { return value; } };
    }

    let resolveStage;
    let reportStageStarted;
    let stageCalls = 0;
    let recallCalls = 0;
    const stageOperationIds = [];
    const stageBodies = [];
    const stageAttempts = new Map();
    const stageStarted = new Promise((resolve) => { reportStageStarted = resolve; });
    globalThis.fetch = async (url, init = {}) => {
      const target = String(url);
      if (target.endsWith('/chat/completions')) {
        return jsonResponse({ choices: [{ message: { content: '即时回复' } }] });
      }
      if (target.endsWith('/api/memory-recalls')) {
        recallCalls += 1;
        if (stageCalls === 1 && resolveStage) {
          return new Promise(() => {});
        }
        return jsonResponse({ status: 'no_memory', memories: [] });
      }
      if (target.endsWith('/api/adapter-memory-turns')) {
        stageCalls += 1;
        stageBodies.push(String(init.body));
        const adapterBody = JSON.parse(init.body);
        stageOperationIds.push(adapterBody.operationId);
        const attempt = (stageAttempts.get(adapterBody.operationId) ?? 0) + 1;
        stageAttempts.set(adapterBody.operationId, attempt);
        const currentText = adapterBody.turns.at(-1)?.content;
        reportStageStarted();
        if (currentText === '我在说一个项目。' && attempt === 1) {
          return new Promise((resolve) => {
            // A lost/non-success response is transport-ambiguous.  The worker
            // must leave the durable request pending and retry the same body.
            resolveStage = () => resolve(jsonResponse({ hidden: 'remote body' }, false, 503));
          });
        }
        if (currentText === '第二条会被确定性拒绝。') {
          return jsonResponse({ hidden: 'deterministic rejection' }, false, 400);
        }
        if (currentText === '第三条仍应继续自动形成。') {
          return jsonResponse({
            memoryProposal: {
              id: 'memory-run-' + adapterBody.currentUserTurnId,
              state: 'applied',
              reviewId: 'review-' + adapterBody.currentUserTurnId,
              resultHash: 'sha256:' + adapterBody.currentUserTurnId,
            },
            memoryChange: { state: 'applied', revision: 2, changeHash: 'sha256:third-change' },
            run: {
              id: 'memory-run-decided-recovery-proof',
              state: 'applied',
              evidence: [],
              adapter: {
                operationId: adapterBody.operationId,
                sessionId: adapterBody.sessionId,
                currentUserTurnId: adapterBody.currentUserTurnId,
              },
            },
          });
        }
        return jsonResponse({
          memoryProposal: {
            id: 'memory-run-' + adapterBody.currentUserTurnId,
            state: 'applied',
            reviewId: 'review-' + adapterBody.currentUserTurnId,
            resultHash: 'sha256:' + adapterBody.currentUserTurnId,
            candidateMemory: {
              entities: [],
              relationships: [],
              events: [{
                id: 'event:durable-proof',
                event_type: 'lived_occurrence',
                summary: '王强在项目室完成交接',
                occurred_at: '2026-08-12T10:30:00+08:00',
                participants: [
                  { entity_id: 'entity:owner', role: 'owner' },
                  { entity_id: 'entity:wang-qiang', role: 'focus' },
                ],
                related_entity_ids: ['entity:project-room'],
                evidence_ids: ['evidence-durable-event'],
              }],
              cognitions: Array.from({ length: 6 }, (_, index) => ({
                id: 'cognition:' + index,
                content: '候选 ' + index,
              })),
            },
            evidence: [],
            claims: { claims: [{ id: 'claim:kept-complete', kind: 'attribute' }] },
            currentEvidenceId: adapterBody.currentUserTurnId,
            cognitionEvidenceChanges: [
              {
                cognitionId: 'cognition:0',
                relation: 'contradicts',
                evidenceId: adapterBody.currentUserTurnId,
                before: {
                  id: 'cognition:0',
                  content: '候选 0',
                  confidence: 600,
                  cred_status: 'limited',
                  sources: [{ evidence_id: 'evidence:prior', relation: 'support' }],
                },
                after: {
                  id: 'cognition:0',
                  content: '候选 0',
                  confidence: 480,
                  cred_status: 'conflicted',
                  sources: [
                    { evidence_id: 'evidence:prior', relation: 'support' },
                    { evidence_id: adapterBody.currentUserTurnId, relation: 'contradict' },
                  ],
                },
              },
            ],
            cognitionReplacements: [
              {
                priorCognitionId: 'cognition:evaluation:prior',
                successorCognitionId: 'cognition:evaluation:replacement',
                relation: 'corrects',
                evidenceId: adapterBody.currentUserTurnId,
                before: {
                  id: 'cognition:evaluation:prior',
                  world_id: 'world:owner',
                  target: { kind: 'relationship', id: 'relationship:lihua-xinggang' },
                  content: '我觉得这段支持很可靠',
                  content_type: 'fact',
                  formed_by: 'stated',
                  confidence: 760,
                  cred_status: 'limited',
                  perspective: { kind: 'entity', holder_entity_ids: ['entity:owner'] },
                  sources: [{ evidence_id: 'evidence:prior', relation: 'support' }],
                  structured_claim: {
                    statement_kind: 'evaluation', predicate: null, value: '很可靠',
                    polarity: 'assert', epistemic_status: 'asserted',
                  },
                },
                after: {
                  id: 'cognition:evaluation:replacement',
                  world_id: 'world:owner',
                  target: { kind: 'relationship', id: 'relationship:lihua-xinggang' },
                  content: '我觉得这段支持不可靠',
                  content_type: 'fact',
                  formed_by: 'stated',
                  confidence: 760,
                  cred_status: 'limited',
                  perspective: { kind: 'entity', holder_entity_ids: ['entity:owner'] },
                  sources: [{ evidence_id: adapterBody.currentUserTurnId, relation: 'support' }],
                  structured_claim: {
                    statement_kind: 'evaluation', predicate: null, value: '不可靠',
                    polarity: 'assert', epistemic_status: 'asserted',
                  },
                },
              },
            ],
            evolutionSteps: [
              {
                id: 'evolution:typed-correction',
                kind: 'cognition_change',
                relation: 'corrects',
                subject: { kind: 'relationship', id: 'relationship:lihua-xinggang' },
                predecessor_ids: ['cognition:evaluation:prior'],
                successor_ids: ['cognition:evaluation:replacement'],
                effective_at: '2026-08-13T10:00:00+08:00',
                evidence_ids: [adapterBody.currentUserTurnId],
              },
            ],
          },
          memoryChange: {
            state: 'applied',
            revision: 1,
            changeHash: 'sha256:background-change',
          },
          run: {
            id: 'memory-run-background-proof',
            state: 'applied',
            evidence: [],
            adapter: {
              operationId: adapterBody.operationId,
              sessionId: adapterBody.sessionId,
              currentUserTurnId: adapterBody.currentUserTurnId,
            },
          },
        });
      }
      throw new Error('unexpected fetch route: ' + target);
    };

    class FakeResponse {
      constructor() {
        this.destroyed = false;
        this.headersSent = false;
        this.writableEnded = false;
        this.done = new Promise((resolve) => { this.resolve = resolve; });
      }
      writeHead(statusCode, headers) {
        this.statusCode = statusCode;
        this.headers = headers;
        this.headersSent = true;
      }
      end(raw = '') {
        this.body = String(raw);
        this.writableEnded = true;
        this.resolve({ statusCode: this.statusCode, body: JSON.parse(this.body) });
      }
    }

    function request(server, method, url, body = undefined) {
      const payload = body === undefined ? Buffer.alloc(0) : Buffer.from(JSON.stringify(body));
      const req = Readable.from(payload.length ? [payload] : []);
      req.method = method;
      req.url = url;
      req.headers = {
        host: '127.0.0.1:7888',
        ...(payload.length ? { 'content-length': String(payload.length) } : {}),
      };
      const res = new FakeResponse();
      server.emit('request', req, res);
      return res.done;
    }

    const { server } = await import('./testbench/server.mjs?chat-async-child=' + Date.now());
    const { openStores } = await import('./src/store/openStores.ts');
    const chatFlight = request(server, 'POST', '/api/chat', { text: '我在说一个项目。' });
    await stageStarted;
    const chat = await Promise.race([
      chatFlight,
      new Promise((_, reject) => setTimeout(() => reject(new Error('chat waited for automatic memory formation')), 1000)),
    ]);
    const marker = chat.body.nextMemory;
    const logBeforeStage = await readFile(chat.body.logFile, 'utf8');
    const pending = await request(
      server,
      'GET',
      '/api/next/memory-operations?sessionId=' + encodeURIComponent(marker.sessionId) +
        '&operationId=' + encodeURIComponent(marker.operationId),
    );
    const history = await request(
      server,
      'GET',
      '/api/chat-history?sessionId=' + encodeURIComponent(marker.sessionId),
    );

    const rejectedChat = await Promise.race([
      request(server, 'POST', '/api/chat', {
        text: '第二条会被确定性拒绝。',
        sessionId: marker.sessionId,
      }),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('next chat waited on background staging recall')), 1000),
      ),
    ]);
    const rejectedMarker = rejectedChat.body.nextMemory;
    const recallCallsWhileStageBlocked = recallCalls;

    resolveStage();
    let ready = pending;
    for (let attempt = 0; attempt < 140 && ready.body.state !== 'ready'; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 25));
      ready = await request(
        server,
        'GET',
        '/api/next/memory-operations?sessionId=' + encodeURIComponent(marker.sessionId) +
          '&operationId=' + encodeURIComponent(marker.operationId),
      );
    }
    const readyReplay = await request(
      server,
      'GET',
      '/api/next/memory-operations?sessionId=' + encodeURIComponent(marker.sessionId) +
        '&operationId=' + encodeURIComponent(marker.operationId),
    );
    const wrongSession = await request(
      server,
      'GET',
      '/api/next/memory-operations?sessionId=another-session&operationId=' +
        encodeURIComponent(marker.operationId),
    );
    const removedDecisionRoute = await request(server, 'POST', '/api/next/memory-decisions', {
      decision: 'accept',
    });
    let rejected = { body: { state: 'pending' } };
    for (let attempt = 0; attempt < 80 && rejected.body.state !== 'failed'; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 25));
      rejected = await request(
        server,
        'GET',
        '/api/next/memory-operations?sessionId=' + encodeURIComponent(rejectedMarker.sessionId) +
          '&operationId=' + encodeURIComponent(rejectedMarker.operationId),
      );
    }
    const afterRejectedChat = await request(server, 'POST', '/api/chat', {
      text: '第三条仍应继续自动形成。',
      sessionId: marker.sessionId,
    });
    const afterRejectedMarker = afterRejectedChat.body.nextMemory;
    let afterRejected = { body: { state: 'pending' } };
    for (let attempt = 0; attempt < 80 && afterRejected.body.state !== 'ready'; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 25));
      afterRejected = await request(
        server,
        'GET',
        '/api/next/memory-operations?sessionId=' + encodeURIComponent(afterRejectedMarker.sessionId) +
        '&operationId=' + encodeURIComponent(afterRejectedMarker.operationId),
      );
    }

    // Force the local outbox INSERT itself to fail.  The chat JSONL marker
    // must already exist, so reopening history can discover a stable
    // NOT_FOUND result instead of hiding an orphan candidate operation.
    const sabotage = openStores(process.env.MEMOWEFT_TESTBENCH_DB_PATH);
    sabotage.db.exec(
      "CREATE TRIGGER reject_operation_reserve BEFORE INSERT ON next_adapter_operation " +
        "BEGIN SELECT RAISE(ABORT, 'forced operation reserve failure'); END",
    );
    const reservationFailedChat = await request(server, 'POST', '/api/chat', {
      text: '第四条模拟本地排队失败。',
      sessionId: marker.sessionId,
    });
    sabotage.db.exec('DROP TRIGGER reject_operation_reserve');
    sabotage.close();
    const reservationFailedMarker = reservationFailedChat.body.record.nextMemory;
    const reservationFailedStatus = await request(
      server,
      'GET',
      '/api/next/memory-operations?sessionId=' +
        encodeURIComponent(reservationFailedMarker.sessionId) +
        '&operationId=' + encodeURIComponent(reservationFailedMarker.operationId),
    );
    const reservationFailedHistory = await request(
      server,
      'GET',
      '/api/chat-history?sessionId=' + encodeURIComponent(marker.sessionId),
    );
    const reservationHistoryMarker =
      reservationFailedHistory.body.turns?.at(-1)?.nextMemory?.memoryProposal;
    const staleSessionChat = await request(server, 'POST', '/api/chat', {
      text: '不能落进别的会话。',
      sessionId: 'unknown-session',
    });

    process.stdout.write('CHAT_ASYNC_RESULT=' + JSON.stringify({
      chatStatus: chat.statusCode,
      reply: chat.body.record.reply,
      recordMarker: chat.body.record.nextMemory,
      responseMarker: marker,
      logContainsReplyBeforeStage: logBeforeStage.includes('即时回复'),
      logContainsOperationBeforeStage: logBeforeStage.includes(marker.operationId),
      pendingStatus: pending.statusCode,
      pendingState: pending.body.state,
      historyMarker: history.body.turns?.[0]?.nextMemory?.memoryProposal,
      readyStatus: ready.statusCode,
      readyState: ready.body.state,
      readyMemoryState: ready.body.nextMemory?.memoryProposal?.state,
      readyCognitionCount:
        ready.body.nextMemory?.memoryProposal?.candidateMemory?.cognitions?.length,
      readyEvent: ready.body.nextMemory?.memoryProposal?.candidateMemory?.events?.[0],
      replayEvent: readyReplay.body.nextMemory?.memoryProposal?.candidateMemory?.events?.[0],
      readyCognitionEvidenceChange:
        ready.body.nextMemory?.memoryProposal?.cognitionEvidenceChanges?.[0],
      replayCognitionEvidenceChange:
        readyReplay.body.nextMemory?.memoryProposal?.cognitionEvidenceChanges?.[0],
      readyCognitionReplacement:
        ready.body.nextMemory?.memoryProposal?.cognitionReplacements?.[0],
      replayCognitionReplacement:
        readyReplay.body.nextMemory?.memoryProposal?.cognitionReplacements?.[0],
      readyClaimId: ready.body.nextMemory?.memoryProposal?.claims?.claims?.[0]?.id,
      stageCalls,
      stageOperationIds,
      stageBodies,
      recallCallsWhileStageBlocked,
      wrongSessionStatus: wrongSession.statusCode,
      automaticReceiptState: ready.body.nextMemory?.state,
      automaticChangeState: ready.body.nextMemory?.memoryChange?.state,
      removedDecisionRouteStatus: removedDecisionRoute.statusCode,
      rejectedState: rejected.body.state,
      rejectedCode: rejected.body.code,
      afterRejectedState: afterRejected.body.state,
      afterRejectedMemoryState: afterRejected.body.nextMemory?.memoryProposal?.state,
      rejectedOperationId: rejectedMarker.operationId,
      afterRejectedOperationId: afterRejectedMarker.operationId,
      reservationResponseFailure:
        reservationFailedChat.body.nextMemory?.memoryFailure?.kind,
      reservationRecordMarker: reservationFailedMarker,
      reservationStatusCode: reservationFailedStatus.statusCode,
      reservationStatusState: reservationFailedStatus.body.state,
      reservationStatusCodeName: reservationFailedStatus.body.code,
      reservationHistoryMarker,
      staleSessionStatus: staleSessionChat.statusCode,
      staleSessionCode: staleSessionChat.body.code,
    }));
    process.exit(0);
  `;

  const child = spawnSync(process.execPath, ['--input-type=module', '--eval', script], {
    cwd: process.cwd(),
    encoding: 'utf8',
    timeout: 15_000,
    env: {
      ...process.env,
      MEMOWEFT_EXPERIENCE_UI: 'off',
      MEMOWEFT_TESTBENCH_MEMORY_AUTHORITY: 'next',
      MEMOWEFT_TESTBENCH_DB_PATH: dbPath,
      MEMOWEFT_TESTBENCH_LOG_DIR: logDir,
      MEMOWEFT_LLM_BASE_URL: 'http://127.0.0.1:9999/v1',
      MEMOWEFT_LLM_API_KEY: 'test-key',
      MEMOWEFT_LLM_MODEL: 'test-chat',
    },
  });

  assert.equal(
    child.status,
    0,
    `async chat child failed\nstdout: ${child.stdout}\nstderr: ${child.stderr}`,
  );
  const resultLine = child.stdout
    .split(/\r?\n/)
    .find((line) => line.startsWith('CHAT_ASYNC_RESULT='));
  assert.ok(resultLine, `child did not report result\nstdout: ${child.stdout}`);
  const result = JSON.parse(resultLine.slice('CHAT_ASYNC_RESULT='.length));
  assert.equal(result.chatStatus, 200);
  assert.equal(result.reply, '即时回复');
  assert.equal(result.responseMarker.state, 'processing');
  assert.deepEqual(result.recordMarker, result.responseMarker);
  assert.equal(result.logContainsReplyBeforeStage, true);
  assert.equal(result.logContainsOperationBeforeStage, true);
  assert.equal(result.pendingStatus, 200);
  assert.equal(result.pendingState, 'pending');
  assert.equal(result.historyMarker.state, 'processing');
  assert.equal(result.historyMarker.operationId, result.responseMarker.operationId);
  assert.equal(result.historyMarker.sessionId, result.responseMarker.sessionId);
  assert.equal(result.readyStatus, 200);
  assert.equal(result.readyState, 'ready');
  assert.equal(result.readyMemoryState, 'applied');
  assert.equal(result.automaticReceiptState, 'applied');
  assert.equal(result.automaticChangeState, 'applied');
  assert.equal(
    result.readyCognitionCount,
    6,
    'automatic result content must not use history truncation',
  );
  assert.deepEqual(result.readyEvent, {
    id: 'event:durable-proof',
    event_type: 'lived_occurrence',
    summary: '王强在项目室完成交接',
    occurred_at: '2026-08-12T10:30:00+08:00',
    participants: [
      { entity_id: 'entity:owner', role: 'owner' },
      { entity_id: 'entity:wang-qiang', role: 'focus' },
    ],
    related_entity_ids: ['entity:project-room'],
    evidence_ids: ['evidence-durable-event'],
  });
  assert.deepEqual(
    result.replayEvent,
    result.readyEvent,
    'terminal applied Event payload must survive operation replay',
  );
  assert.deepEqual(
    result.replayCognitionEvidenceChange,
    result.readyCognitionEvidenceChange,
    'terminal same-cognition Evidence change must survive operation replay exactly',
  );
  assert.equal(result.readyCognitionEvidenceChange.relation, 'contradicts');
  assert.equal(result.readyCognitionEvidenceChange.before.confidence, 600);
  assert.equal(result.readyCognitionEvidenceChange.after.confidence, 480);
  assert.deepEqual(
    result.replayCognitionReplacement,
    result.readyCognitionReplacement,
    'terminal typed cognition replacement must survive operation replay exactly',
  );
  assert.equal(result.readyCognitionReplacement.relation, 'corrects');
  assert.equal(result.readyCognitionReplacement.before.structured_claim.value, '很可靠');
  assert.equal(result.readyCognitionReplacement.after.structured_claim.value, '不可靠');
  assert.equal(
    result.readyCognitionReplacement.after.sources[0].evidence_id,
    result.responseMarker.currentEvidenceId,
  );
  assert.equal(result.readyClaimId, 'claim:kept-complete');
  assert.equal(result.stageCalls, 4, JSON.stringify(result));
  assert.deepEqual(result.stageOperationIds, [
    result.responseMarker.operationId,
    result.rejectedOperationId,
    result.responseMarker.operationId,
    result.afterRejectedOperationId,
  ]);
  assert.equal(
    result.stageBodies[0],
    result.stageBodies[2],
    'a transport-ambiguous retry must resend the exact durable adapter request bytes',
  );
  assert.notEqual(result.stageBodies[0], result.stageBodies[1]);
  assert.notEqual(result.stageBodies[0], result.stageBodies[3]);
  const firstStageBody = JSON.parse(result.stageBodies[0]);
  assert.equal(firstStageBody.operationId, result.responseMarker.operationId);
  assert.equal(firstStageBody.sessionId, result.responseMarker.sessionId);
  assert.equal(firstStageBody.currentUserTurnId, result.responseMarker.currentEvidenceId);
  assert.equal(firstStageBody.evidenceRecords.length, 1);
  const firstSystemEvidence = firstStageBody.evidenceRecords[0];
  assert.deepEqual(Object.keys(firstSystemEvidence).sort(), [
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
  ]);
  assert.equal(firstSystemEvidence.id, result.responseMarker.currentEvidenceId);
  assert.equal(firstSystemEvidence.rawContent, '我在说一个项目。');
  assert.equal(firstSystemEvidence.sourceKind, 'spoken');
  assert.equal(firstSystemEvidence.allowLocalRead, true);
  assert.equal(firstSystemEvidence.allowInference, true);
  assert.equal(firstSystemEvidence.occurredAt, firstStageBody.turns.at(-1).occurredAt);
  assert.match(firstSystemEvidence.recordedAt, /Z$/);
  assert.equal(
    result.recallCallsWhileStageBlocked,
    1,
    'the next reply must skip the serialized Lab while candidate staging owns it',
  );
  assert.equal(result.wrongSessionStatus, 404);
  assert.equal(result.removedDecisionRouteStatus, 404);
  assert.equal(result.rejectedState, 'failed');
  assert.equal(result.rejectedCode, 'NEXT_HTTP_REQUEST_REJECTED');
  assert.equal(result.afterRejectedState, 'ready');
  assert.equal(result.afterRejectedMemoryState, 'applied');
  assert.equal(result.reservationResponseFailure, 'NEXT_OPERATION_RESERVATION_FAILED');
  assert.equal(result.reservationRecordMarker.state, 'processing');
  assert.equal(result.reservationStatusCode, 404);
  assert.equal(result.reservationStatusState, 'failed');
  assert.equal(result.reservationStatusCodeName, 'NEXT_OPERATION_NOT_FOUND');
  assert.equal(result.reservationHistoryMarker.state, 'processing');
  assert.equal(
    result.reservationHistoryMarker.operationId,
    result.reservationRecordMarker.operationId,
  );
  assert.equal(result.reservationHistoryMarker.sessionId, result.reservationRecordMarker.sessionId);
  assert.equal(
    result.reservationHistoryMarker.currentEvidenceId,
    result.reservationRecordMarker.currentEvidenceId,
  );
  assert.equal(result.staleSessionStatus, 409);
  assert.equal(result.staleSessionCode, 'CHAT_SESSION_NOT_ACTIVE');
});
