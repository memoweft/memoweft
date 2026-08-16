import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildAdapterMemoryTurns,
  createNextBridge,
  mergeRecallForRecord,
  normalizeNextBaseUrl,
  readNextBridgeTimeouts,
  sanitizePreparedAdapterMemoryTurn,
  selectCarryForwardEvidenceIds,
  validateMemoryAskResponse,
  validateRecallMemoryResponse,
} from './next-bridge.mjs';

function record({ turn, evidenceId, userInput, reply = '', ts = '2026-08-09T00:00:00.000Z' }) {
  return {
    kind: 'turn',
    turn,
    ts,
    userInput,
    reply,
    evidence: evidenceId ? [{ id: evidenceId, summary: userInput }] : [],
  };
}

function systemEvidence(item, overrides = {}) {
  const evidenceId = item.evidence?.[0]?.id;
  if (!evidenceId) throw new Error('test system Evidence requires a stored Evidence ID');
  return {
    id: evidenceId,
    subjectId: 'owner-test',
    sourceKind: 'spoken',
    hostId: 'next-bridge-test',
    originId: `origin:${evidenceId}`,
    occurredAt: item.ts,
    recordedAt: item.ts,
    rawContent: item.userInput,
    summary: item.userInput,
    allowLocalRead: true,
    allowCloudRead: false,
    allowInference: true,
    correctsEvidenceId: null,
    ...overrides,
  };
}

function jsonResponse(value, ok = true, status = ok ? 200 : 503) {
  return { ok, status, json: async () => value };
}

test('adapter payload: only current stored Evidence is eligible; prior user and assistant stay typed context', () => {
  const past = record({
    turn: 4,
    evidenceId: 'ev-past',
    userInput: '我住在南京',
    reply: '记住了。',
  });
  const current = record({
    turn: 5,
    evidenceId: 'ev-current',
    userInput: '我现在搬到上海了',
    reply: '好的。',
  });
  const payload = buildAdapterMemoryTurns({
    sessionId: 's-1',
    record: current,
    previousRecords: [past, current],
    evidenceRecords: [systemEvidence(current)],
  });

  assert.equal(payload.currentUserTurnId, 'ev-current');
  assert.equal(payload.operationId, 'testbench:s-1:ev-current');
  assert.deepEqual(payload.carryForwardEvidenceIds, []);
  assert.deepEqual(payload.evidenceRecords, [systemEvidence(current)]);
  assert.deepEqual(
    payload.turns.map((turn) => [turn.turnId, turn.role, turn.content]),
    [
      ['ev-past', 'user', '我住在南京'],
      ['ev-past:assistant', 'assistant', '记住了。'],
      ['ev-current', 'user', '我现在搬到上海了'],
    ],
  );
  assert.ok(
    !payload.turns.some((turn) => turn.content === '好的。'),
    '当前 assistant reply 绝不能进入本轮抽取',
  );
  assert.match(payload.turns[0].occurredAt, /Z$/, '每个严格 typed turn 都带可解析的时区时间');
});

test('carry selector uses prior run audit only, never message keywords or arbitrary history', () => {
  const source = record({
    turn: 1,
    evidenceId: 'ev-source',
    userInput: '那个人把蓝色文件夹留在门口。',
  });
  source.nextMemory = {
    status: 'ok',
    run: {
      id: 'run-source',
      state: 'no-candidate',
      adapter: { sessionId: 's-carry', currentUserTurnId: 'ev-source' },
      unresolvedReferences: [{ mention: '那个人', evidence_ids: ['ev-source'] }],
    },
  };
  const noUnresolved = record({ turn: 2, evidenceId: 'ev-plain', userInput: '普通历史。' });
  noUnresolved.nextMemory = {
    status: 'ok',
    run: {
      id: 'run-plain',
      state: 'no-candidate',
      adapter: { sessionId: 's-carry', currentUserTurnId: 'ev-plain' },
      unresolvedReferences: [],
    },
  };
  const failed = record({ turn: 3, evidenceId: 'ev-failed', userInput: '失败历史。' });
  failed.nextMemory = {
    status: 'ok',
    run: {
      id: 'run-failed',
      state: 'failed',
      adapter: { sessionId: 's-carry', currentUserTurnId: 'ev-failed' },
      unresolvedReferences: [{ mention: '对象', evidence_ids: ['ev-failed'] }],
    },
  };
  const otherSession = record({ turn: 4, evidenceId: 'ev-other', userInput: '另一会话。' });
  otherSession.nextMemory = {
    status: 'ok',
    run: {
      id: 'run-other',
      state: 'no-candidate',
      adapter: { sessionId: 's-other', currentUserTurnId: 'ev-other' },
      unresolvedReferences: [{ mention: '对象', evidence_ids: ['ev-other'] }],
    },
  };

  assert.deepEqual(
    selectCarryForwardEvidenceIds({
      sessionId: 's-carry',
      record: record({ turn: 5, evidenceId: 'ev-current', userInput: '补充说明。' }),
      previousRecords: [source, noUnresolved, failed, otherSession],
    }),
    ['ev-source'],
  );

  const consumer = record({ turn: 5, evidenceId: 'ev-consumer', userInput: '已完成自动形成。' });
  consumer.nextMemory = {
    status: 'ok',
    run: {
      id: 'run-consumer',
      state: 'applied',
      carryForward: { status: 'consumed', requestedEvidenceIds: ['ev-source'] },
    },
  };
  assert.deepEqual(
    selectCarryForwardEvidenceIds({
      sessionId: 's-carry',
      record: record({ turn: 6, evidenceId: 'ev-later', userInput: '再补一句。' }),
      previousRecords: [source, consumer],
    }),
    [],
    '后续消费审计会阻止旧的 immutable source-run 快照被再次选择',
  );
});

test('adapter payload carries only explicitly selected prior user Evidence and keeps it inside the window', () => {
  const history = Array.from({ length: 12 }, (_, index) =>
    record({
      turn: index + 1,
      evidenceId: `ev-history-${index + 1}`,
      userInput: `历史事实 ${index + 1}`,
      reply: `历史 AI 回复 ${index + 1}`,
    }),
  );
  const payload = buildAdapterMemoryTurns({
    sessionId: 's-window-carry',
    record: record({ turn: 13, evidenceId: 'ev-current', userInput: '这是补充描述。' }),
    previousRecords: history,
    carryForwardEvidenceIds: ['ev-history-1'],
    evidenceRecords: [
      systemEvidence(history[0]),
      systemEvidence(record({ turn: 13, evidenceId: 'ev-current', userInput: '这是补充描述。' })),
    ],
  });

  assert.deepEqual(payload.carryForwardEvidenceIds, ['ev-history-1']);
  assert.ok(payload.turns.some((turn) => turn.turnId === 'ev-history-1' && turn.role === 'user'));
  assert.equal(payload.turns.at(-1).turnId, 'ev-current');
  assert.ok(
    payload.turns.some((turn) => turn.turnId === 'ev-history-1:assistant'),
    '伴随的 assistant 仍可保留为 typed context',
  );
  assert.throws(() =>
    buildAdapterMemoryTurns({
      sessionId: 's-window-carry',
      record: record({ turn: 13, evidenceId: 'ev-current', userInput: '这是补充描述。' }),
      previousRecords: history,
      carryForwardEvidenceIds: ['ev-history-1:assistant'],
      evidenceRecords: [],
    }),
  );
});

test('adapter payload: old logs without evidence retain a deterministic context-only user id', () => {
  const payload = buildAdapterMemoryTurns({
    sessionId: 's-old',
    record: record({ turn: 7, evidenceId: 'ev-now', userInput: '现在这句' }),
    previousRecords: [record({ turn: 6, userInput: '历史旧日志', reply: '历史回复' })],
    evidenceRecords: [
      systemEvidence(record({ turn: 7, evidenceId: 'ev-now', userInput: '现在这句' })),
    ],
  });
  assert.equal(payload.turns[0].turnId, 'testbench:s-old:turn:6:user');
  assert.equal(payload.turns[1].turnId, 'testbench:s-old:turn:6:user:assistant');
});

test('adapter payload keeps the typed-turn window within the Python adapter ceiling', () => {
  const history = Array.from({ length: 20 }, (_, index) =>
    record({
      turn: index + 1,
      evidenceId: `ev-${index + 1}`,
      userInput: `历史${index + 1}`,
      reply: `回复${index + 1}`,
    }),
  );
  const payload = buildAdapterMemoryTurns({
    sessionId: 's-window',
    record: record({ turn: 21, evidenceId: 'ev-current', userInput: '当前' }),
    previousRecords: history,
    evidenceRecords: [
      systemEvidence(record({ turn: 21, evidenceId: 'ev-current', userInput: '当前' })),
    ],
  });
  assert.equal(
    payload.turns.length,
    19,
    '9 段历史 user/assistant + 当前用户 Evidence，不超过 20 条硬上限',
  );
  assert.equal(payload.turns.at(-1).turnId, 'ev-current');
  assert.throws(() =>
    buildAdapterMemoryTurns({
      sessionId: 's-window',
      record: record({ turn: 21, evidenceId: 'ev-current', userInput: '当前' }),
      previousRecords: history,
      contextLimit: 10,
      evidenceRecords: [
        systemEvidence(record({ turn: 21, evidenceId: 'ev-current', userInput: '当前' })),
      ],
    }),
  );
});

test('loopback base URL rejects non-local, credentialed, or path-bearing overrides', () => {
  assert.equal(normalizeNextBaseUrl('http://localhost:7891').toString(), 'http://localhost:7891');
  for (const bad of [
    'https://127.0.0.1:7891',
    'http://evil.invalid:7891',
    'http://127.0.0.1:7891/api',
    'http://x:y@127.0.0.1:7891',
  ]) {
    assert.throws(() => normalizeNextBaseUrl(bad));
  }
});

test('slow local model operations get a bounded model budget rather than the short status timeout', () => {
  const bridge = createNextBridge({ fetchImpl: async () => jsonResponse({}) });
  assert.equal(bridge.statusTimeoutMs, 4_500);
  assert.equal(bridge.operationTimeoutMs, 300_000);
  assert.equal(readNextBridgeTimeouts({ operationTimeoutMs: '30000' }).operationTimeoutMs, 30_000);
  assert.throws(() => readNextBridgeTimeouts({ operationTimeoutMs: '29999' }));
});

test('bridge: stage failure degrades safely and does not expose a remote response body', async () => {
  const bridge = createNextBridge({
    fetchImpl: async () => jsonResponse({ secret: 'do-not-return' }, false),
  });
  const result = await bridge.stageMemoryTurn({
    sessionId: 's',
    record: record({ turn: 1, evidenceId: 'ev-1', userInput: 'hi' }),
    previousRecords: [],
    evidenceRecords: [systemEvidence(record({ turn: 1, evidenceId: 'ev-1', userInput: 'hi' }))],
  });
  assert.deepEqual(result, { status: 'unavailable', code: 'NEXT_HTTP_UNAVAILABLE' });
});

test('bridge distinguishes a deterministic adapter rejection from transport ambiguity', async () => {
  const bridge = createNextBridge({
    fetchImpl: async () => jsonResponse({ secret: 'do-not-return' }, false, 400),
  });
  const result = await bridge.stageMemoryTurn({
    sessionId: 's-rejected',
    record: record({ turn: 1, evidenceId: 'ev-rejected', userInput: 'hi' }),
    previousRecords: [],
    evidenceRecords: [
      systemEvidence(record({ turn: 1, evidenceId: 'ev-rejected', userInput: 'hi' })),
    ],
  });
  assert.deepEqual(result, { status: 'failed', code: 'NEXT_HTTP_REQUEST_REJECTED' });
});

test('durable prepared adapter payload is revalidated and replayed unchanged on the fixed route', async () => {
  const calls = [];
  const bridge = createNextBridge({
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return jsonResponse({ run: { id: 'run-1', state: 'no-candidate' } });
    },
  });
  const payload = buildAdapterMemoryTurns({
    sessionId: 's-durable',
    record: record({ turn: 1, evidenceId: 'ev-durable', userInput: '一个项目。' }),
    previousRecords: [],
    evidenceRecords: [
      systemEvidence(record({ turn: 1, evidenceId: 'ev-durable', userInput: '一个项目。' })),
    ],
  });
  assert.deepEqual(sanitizePreparedAdapterMemoryTurn(payload), payload);
  assert.deepEqual(await bridge.stagePreparedMemoryTurn(payload), {
    status: 'ok',
    value: { run: { id: 'run-1', state: 'no-candidate' } },
  });
  assert.equal(calls[0].url, 'http://127.0.0.1:7891/api/adapter-memory-turns');
  assert.deepEqual(JSON.parse(calls[0].init.body), payload);

  for (const invalid of [
    { ...payload, operationId: 'testbench:s-other:ev-durable' },
    { ...payload, extra: true },
    { ...payload, turns: [{ ...payload.turns[0], role: 'assistant' }] },
    { ...payload, carryForwardEvidenceIds: ['missing-evidence'] },
  ]) {
    assert.deepEqual(await bridge.stagePreparedMemoryTurn(invalid), {
      status: 'failed',
      code: 'NEXT_INVALID_LOCAL_TURN',
    });
  }
  assert.equal(calls.length, 1, 'invalid durable rows never cross the fixed bridge route');
});

test('adapter Evidence envelope rejects missing, ineligible, or typed-turn-mismatched provenance', () => {
  const current = record({ turn: 1, evidenceId: 'ev-envelope', userInput: '完整系统证据。' });
  const base = {
    sessionId: 's-envelope',
    record: current,
    previousRecords: [],
  };
  assert.throws(() => buildAdapterMemoryTurns(base), /complete durable system Evidence/);
  assert.throws(() =>
    buildAdapterMemoryTurns({
      ...base,
      evidenceRecords: [systemEvidence(current, { allowInference: false })],
    }),
  );
  assert.throws(() =>
    buildAdapterMemoryTurns({
      ...base,
      evidenceRecords: [systemEvidence(current, { rawContent: '被替换的原文。' })],
    }),
  );
  assert.throws(() =>
    buildAdapterMemoryTurns({
      ...base,
      evidenceRecords: [systemEvidence(current, { sourceKind: 'inferred' })],
    }),
  );
});

test('adapter delivery requests a bounded receipt instead of an embedded World snapshot', async () => {
  const calls = [];
  const bridge = createNextBridge({
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return jsonResponse({
        memoryProposal: { state: 'applied', id: 'run-receipt' },
        run: {
          state: 'applied',
          adapter: {
            operationId: 'testbench:s-receipt:ev-receipt',
            sessionId: 's-receipt',
            currentUserTurnId: 'ev-receipt',
          },
        },
      });
    },
  });
  const result = await bridge.stageMemoryTurn({
    sessionId: 's-receipt',
    record: record({ turn: 1, evidenceId: 'ev-receipt', userInput: '这个项目已经启动。' }),
    previousRecords: [],
    evidenceRecords: [
      systemEvidence(
        record({ turn: 1, evidenceId: 'ev-receipt', userInput: '这个项目已经启动。' }),
      ),
    ],
  });
  assert.equal(result.status, 'ok');
  assert.equal(result.value.run.state, 'applied');
  assert.equal(calls[0].init.headers.Accept, 'application/vnd.memoweft.adapter-receipt+json');

  const legacyFullWorldBridge = createNextBridge({
    fetchImpl: async () =>
      jsonResponse({
        run: { state: 'applied' },
        world: { memory: { cognitions: [{ content: 'x'.repeat(129 * 1024) }] } },
      }),
  });
  assert.deepEqual(
    await legacyFullWorldBridge.stageMemoryTurn({
      sessionId: 's-receipt',
      record: record({ turn: 1, evidenceId: 'ev-receipt', userInput: '这个项目已经启动。' }),
      previousRecords: [],
      evidenceRecords: [
        systemEvidence(
          record({ turn: 1, evidenceId: 'ev-receipt', userInput: '这个项目已经启动。' }),
        ),
      ],
    }),
    { status: 'unavailable', code: 'NEXT_INVALID_RESPONSE' },
    'a pre-receipt Python response is transport-ambiguous, never a false ready receipt',
  );
});

test('bridge: oversized or cyclic successful responses fail closed before reaching callers', async () => {
  for (const value of [
    { payload: 'x'.repeat(513 * 1024) },
    (() => {
      const cyclic = {};
      cyclic.self = cyclic;
      return cyclic;
    })(),
  ]) {
    const bridge = createNextBridge({ fetchImpl: async () => jsonResponse(value) });
    const result = await bridge.stageMemoryTurn({
      sessionId: 's',
      record: record({ turn: 1, evidenceId: 'ev-1', userInput: 'hi' }),
      previousRecords: [],
      evidenceRecords: [systemEvidence(record({ turn: 1, evidenceId: 'ev-1', userInput: 'hi' }))],
    });
    assert.deepEqual(result, { status: 'unavailable', code: 'NEXT_INVALID_RESPONSE' });
  }
});

test('correction forwarding binds one immutable operationId and rejects an unbound retry locally', async () => {
  const calls = [];
  const bridge = createNextBridge({
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return jsonResponse({ state: 'applied', operationId: 'correction-op-001' });
    },
  });
  assert.deepEqual(
    await bridge.correctMemory({
      operationId: 'correction-op-001',
      cognitionId: 'cognition-001',
      correctionText: '它其实在去年已经结束了。',
      ignored: 'never forwarded',
    }),
    { status: 'ok', value: { state: 'applied', operationId: 'correction-op-001' } },
  );
  assert.equal(calls[0].url, 'http://127.0.0.1:7891/api/memory-corrections');
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    operationId: 'correction-op-001',
    cognitionId: 'cognition-001',
    correctionText: '它其实在去年已经结束了。',
  });
  assert.deepEqual(
    await bridge.correctMemory({
      cognitionId: 'cognition-001',
      correctionText: '没有 operationId。',
    }),
    { status: 'failed', code: 'NEXT_INVALID_REQUEST' },
  );
  assert.equal(calls.length, 1, 'an unbound correction retry never crosses the bridge');
});

test('correction forwarding accepts only an automatic terminal receipt bound to its request', async () => {
  const request = {
    operationId: 'correction-terminal-001',
    cognitionId: 'cognition-terminal-001',
    correctionText: '这项记忆的结论需要更正。',
  };
  const accepted = [
    { state: 'applied', operationId: request.operationId },
    { receipt: { state: 'no-change', operationId: request.operationId } },
    { run: { state: 'clarification-required', operationId: request.operationId } },
    { receipt: { state: 'out-of-scope', operationId: request.operationId } },
    { state: 'failed', operationId: request.operationId },
  ];
  for (const receipt of accepted) {
    const bridge = createNextBridge({ fetchImpl: async () => jsonResponse(receipt) });
    assert.deepEqual(await bridge.correctMemory(request), { status: 'ok', value: receipt });
  }

  const rejected = [
    { staged: 1 },
    { state: 'candidate-ready', operationId: request.operationId },
    { state: 'correction-pending', operationId: request.operationId },
    { run: { state: 'staged', operationId: request.operationId } },
    { state: 'applied', operationId: 'another-operation' },
    {
      state: 'applied',
      operationId: request.operationId,
      receipt: { state: 'applied', operationId: 'another-operation' },
    },
  ];
  for (const receipt of rejected) {
    const bridge = createNextBridge({ fetchImpl: async () => jsonResponse(receipt) });
    assert.deepEqual(await bridge.correctMemory(request), {
      status: 'unavailable',
      code: 'NEXT_INVALID_RESPONSE',
    });
  }
});

test('bridge: legacy migration posts only exact user Evidence to its fixed route and requires an automatic terminal', async () => {
  const calls = [];
  const bridge = createNextBridge({
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return jsonResponse({ state: 'applied', memoryChange: { revision: 2 } });
    },
  });
  const result = await bridge.stageLegacyMemory({
    operationId: 'legacy-import-001',
    turns: [
      {
        turnId: 'evidence-001',
        role: 'user',
        content: '二五有一点傲娇。',
        occurredAt: '2026-08-10T08:00:00+08:00',
      },
      {
        turnId: 'evidence-002',
        role: 'user',
        content: '每周六去游泳。',
        occurredAt: '2026-08-10T10:00:00Z',
      },
    ],
  });

  assert.deepEqual(result, {
    status: 'ok',
    value: { state: 'applied', memoryChange: { revision: 2 } },
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'http://127.0.0.1:7891/api/adapter-legacy-memory-imports');
  assert.equal(calls[0].init.headers.Origin, 'http://127.0.0.1:7891');
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    operationId: 'legacy-import-001',
    turns: [
      {
        turnId: 'evidence-001',
        role: 'user',
        content: '二五有一点傲娇。',
        occurredAt: '2026-08-10T08:00:00+08:00',
      },
      {
        turnId: 'evidence-002',
        role: 'user',
        content: '每周六去游泳。',
        occurredAt: '2026-08-10T10:00:00Z',
      },
    ],
  });
});

test('bridge: legacy migration rejects a staging-only or candidate-ready 2xx receipt', async () => {
  const base = {
    operationId: 'legacy-import-nonterminal',
    turns: [
      {
        turnId: 'evidence-nonterminal',
        role: 'user',
        content: '只有原始用户 Evidence。',
        occurredAt: '2026-08-10T08:00:00Z',
      },
    ],
  };
  for (const receipt of [
    { staged: 2 },
    { state: 'candidate-ready' },
    { run: { state: 'candidate-ready' } },
  ]) {
    const bridge = createNextBridge({ fetchImpl: async () => jsonResponse(receipt) });
    assert.deepEqual(await bridge.stageLegacyMemory(base), {
      status: 'unavailable',
      code: 'NEXT_INVALID_RESPONSE',
    });
  }
});

test('bridge: legacy migration strictly rejects non-Evidence shapes and bounded invalid input locally', async () => {
  const calls = [];
  const bridge = createNextBridge({
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return jsonResponse({});
    },
  });
  const base = {
    operationId: 'legacy-import-invalid',
    turns: [
      {
        turnId: 'evidence-001',
        role: 'user',
        content: '原始用户 Evidence。',
        occurredAt: '2026-08-10T08:00:00Z',
      },
    ],
  };
  const invalid = [
    { ...base, cognition: '二五傲娇（派生文本）' },
    { ...base, turns: [{ ...base.turns[0], sourcePath: '/profile/cognition' }] },
    { ...base, turns: [{ ...base.turns[0], turnId: 'evidence-001' }, { ...base.turns[0] }] },
    { ...base, turns: [{ ...base.turns[0], role: 'assistant' }] },
    { ...base, turns: [{ ...base.turns[0], occurredAt: '2026-08-10T08:00:00' }] },
    { ...base, turns: [{ ...base.turns[0], content: 'x'.repeat(4_001) }] },
    {
      ...base,
      turns: Array.from({ length: 21 }, (_, index) => ({ ...base.turns[0], turnId: `e-${index}` })),
    },
  ];
  for (const body of invalid) {
    assert.deepEqual(await bridge.stageLegacyMemory(body), {
      status: 'failed',
      code: 'NEXT_INVALID_LEGACY_IMPORT',
    });
  }
  assert.equal(calls.length, 0, '本地拒绝不得触发远端请求');
});

test('bridge: legacy migration remote failures return a stable local code without its body', async () => {
  const bridge = createNextBridge({
    fetchImpl: async () => jsonResponse({ detail: 'secret remote error' }, false),
  });
  assert.deepEqual(
    await bridge.stageLegacyMemory({
      operationId: 'legacy-import-failure',
      turns: [
        {
          turnId: 'evidence-failure',
          role: 'user',
          content: '仅原始 Evidence。',
          occurredAt: '2026-08-10T08:00:00Z',
        },
      ],
    }),
    { status: 'unavailable', code: 'NEXT_HTTP_UNAVAILABLE' },
  );
});

test('bridge: pre-reply recall posts only {query}, returns typed accepted cognition, and keeps a short timeout', async () => {
  const calls = [];
  const bridge = createNextBridge({
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return jsonResponse({
        status: 'recalled',
        memories: [{ content: '用户偏好乌龙茶', confidence: 910, credStatus: 'stable' }],
      });
    },
  });

  const result = await bridge.recallMemory('  喝什么茶好  ');

  assert.deepEqual(result, {
    status: 'ok',
    value: {
      status: 'recalled',
      memories: [{ content: '用户偏好乌龙茶', confidence: 910, credStatus: 'stable' }],
    },
  });
  assert.equal(calls[0].url, 'http://127.0.0.1:7891/api/memory-recalls');
  assert.deepEqual(JSON.parse(calls[0].init.body), { query: '喝什么茶好' });
  assert.equal(calls[0].init.signal.aborted, false, '请求有独立 AbortSignal 的短读预算');
  assert.deepEqual(await bridge.recallMemory(''), {
    status: 'failed',
    code: 'NEXT_INVALID_RECALL_QUERY',
  });
});

test('bridge: pre-reply recall response is exact and malformed data never reaches the caller', async () => {
  const valid = {
    status: 'recalled',
    memories: [{ content: '用户偏好乌龙茶', confidence: 910, credStatus: 'stable' }],
  };
  assert.deepEqual(validateRecallMemoryResponse(valid), valid);
  for (const malformed of [
    { ...valid, debug: 'leak' },
    { status: 'no_memory', memories: valid.memories },
    { status: 'recalled', memories: [{ ...valid.memories[0], debug: true }] },
    { status: 'recalled', memories: [{ ...valid.memories[0], confidence: 910.5 }] },
    { status: 'recalled', memories: Array.from({ length: 9 }, () => valid.memories[0]) },
  ]) {
    assert.equal(validateRecallMemoryResponse(malformed), null);
  }

  const bridge = createNextBridge({
    fetchImpl: async () => jsonResponse({ ...valid, rawEvidence: '不应进入提示或日志' }),
  });
  assert.deepEqual(await bridge.recallMemory('问一句'), {
    status: 'failed',
    code: 'NEXT_INVALID_RECALL_RESPONSE',
  });
});

test('bridge: pre-reply asking keeps the two reasons distinct and rejects malformed projections', async () => {
  const lowConfidence = {
    status: 'proposed',
    proposal: {
      cognitionId: 'cognition:quiet-mornings',
      kind: 'hypothesis',
      reason: 'low_confidence',
      content: '用户可能更喜欢安静的早晨',
      question: '我看到「最近总在清晨写作」，所以在想：你可能更喜欢安静的早晨。是这样吗？',
      supportEvidence: [{ id: 'evidence:morning', summary: '最近总在清晨写作' }],
      contradictEvidence: [],
      storedConfidence: 280,
      effectiveConfidence: 272,
      credStatus: 'candidate',
    },
  };
  const conflict = {
    status: 'proposed',
    proposal: {
      cognitionId: 'cognition:early-mornings',
      kind: 'conflict',
      reason: 'unresolved_conflict',
      content: '用户喜欢早起',
      question: '关于“用户喜欢早起”，我这边的信息有点对不上，能帮我确认下现在是怎样吗？',
      supportEvidence: [{ id: 'evidence:early', summary: '我喜欢早起' }],
      contradictEvidence: [{ id: 'evidence:late', summary: '我通常睡到中午' }],
      storedConfidence: 80,
      effectiveConfidence: 80,
      credStatus: 'conflicted',
    },
  };

  assert.deepEqual(validateMemoryAskResponse({ status: 'none', proposal: null }), {
    status: 'none',
    proposal: null,
  });
  assert.deepEqual(validateMemoryAskResponse(lowConfidence), lowConfidence);
  assert.deepEqual(validateMemoryAskResponse(conflict), conflict);

  for (const malformed of [
    { ...lowConfidence, debug: 'leak' },
    { status: 'none', proposal: lowConfidence.proposal },
    {
      status: 'proposed',
      proposal: { ...lowConfidence.proposal, reason: 'unresolved_conflict' },
    },
    {
      status: 'proposed',
      proposal: { ...conflict.proposal, contradictEvidence: [] },
    },
    {
      status: 'proposed',
      proposal: { ...lowConfidence.proposal, supportEvidence: [{ id: 'e', summary: '' }] },
    },
  ]) {
    assert.equal(validateMemoryAskResponse(malformed), null);
  }

  const calls = [];
  const bridge = createNextBridge({
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return jsonResponse(lowConfidence);
    },
  });
  assert.deepEqual(await bridge.proposeMemoryAsk('  早晨怎么样  '), {
    status: 'ok',
    value: lowConfidence,
  });
  assert.equal(calls[0].url, 'http://127.0.0.1:7891/api/memory-asks');
  assert.deepEqual(JSON.parse(calls[0].init.body), { query: '早晨怎么样' });
  assert.deepEqual(await bridge.proposeMemoryAsk(''), {
    status: 'failed',
    code: 'NEXT_INVALID_ASK_QUERY',
  });
});

test('record recall merges actual 2.0 injection without a fabricated score and de-duplicates 1.x content', () => {
  assert.deepEqual(
    mergeRecallForRecord(
      [
        { content: '用户偏好乌龙茶', score: 0.8 },
        { content: '用户常在晚上写作', score: 0.7 },
      ],
      [
        { content: '用户偏好乌龙茶', confidence: 910, credStatus: 'stable' },
        { content: '用户周末会骑行', confidence: 820, credStatus: 'limited' },
      ],
    ),
    [
      { summary: '用户偏好乌龙茶', score: 800 },
      { summary: '用户常在晚上写作', score: 700 },
      { summary: '2.0 · 用户周末会骑行' },
    ],
  );
});

test('bridge: remaining allowlisted proxy rejects an invalid query locally', async () => {
  const calls = [];
  const bridge = createNextBridge({
    baseUrl: 'http://127.0.0.1:7891',
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return jsonResponse({ revision: 2 });
    },
  });
  assert.equal(typeof bridge.decideMemory, 'undefined');
  assert.deepEqual(await bridge.queryMemory({ query: '' }), {
    status: 'failed',
    code: 'NEXT_INVALID_REQUEST',
  });
  assert.equal(calls.length, 0, 'invalid local query never crosses the bridge');
});
