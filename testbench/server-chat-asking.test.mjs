import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

test('real chat delivers one bounded ask, suppresses replay from session history, and returns the answer as user Evidence', async (t) => {
  const root = await mkdtemp(join(tmpdir(), 'memoweft-chat-asking-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const dbPath = join(root, 'testbench-evidence.db');
  const logDir = join(root, 'logs');

  const script = String.raw`
    import { Readable } from 'node:stream';

    const lowQuestion = '我看到「最近总在清晨写作」，所以在想：你可能更喜欢安静的早晨。是这样吗？';
    const conflictQuestion = '关于“你喜欢早起”，我这边的信息有点对不上，能帮我确认下现在是怎样吗？';
    const lowProposal = {
      status: 'proposed',
      proposal: {
        cognitionId: 'cognition:quiet-mornings',
        kind: 'hypothesis',
        reason: 'low_confidence',
        content: '你可能更喜欢安静的早晨',
        question: lowQuestion,
        supportEvidence: [{ id: 'evidence:morning', summary: '最近总在清晨写作' }],
        contradictEvidence: [],
        storedConfidence: 280,
        effectiveConfidence: 272,
        credStatus: 'candidate',
      },
    };
    const conflictProposal = {
      status: 'proposed',
      proposal: {
        cognitionId: 'cognition:early-mornings',
        kind: 'conflict',
        reason: 'unresolved_conflict',
        content: '你喜欢早起',
        question: conflictQuestion,
        supportEvidence: [{ id: 'evidence:early', summary: '我喜欢早起' }],
        contradictEvidence: [{ id: 'evidence:late', summary: '我通常睡到中午' }],
        storedConfidence: 80,
        effectiveConfidence: 80,
        credStatus: 'conflicted',
      },
    };

    function jsonResponse(value, ok = true, status = ok ? 200 : 503) {
      return { ok, status, headers: { get() { return null; } }, async json() { return value; } };
    }

    let chatCalls = 0;
    let askCalls = 0;
    const stageBodies = [];
    globalThis.fetch = async (url, init = {}) => {
      const target = String(url);
      if (target.endsWith('/chat/completions')) {
        chatCalls += 1;
        return jsonResponse({ choices: [{ message: { content: '普通回复-' + chatCalls } }] });
      }
      if (target.endsWith('/api/memory-asks')) {
        askCalls += 1;
        const body = JSON.parse(init.body);
        if (body.query === '触发低置信追问') {
          if (askCalls === 1) return jsonResponse(lowProposal);
          return jsonResponse({
            ...lowProposal,
            proposal: {
              ...lowProposal.proposal,
              effectiveConfidence: 260,
              credStatus: 'low',
            },
          });
        }
        if (body.query === '触发冲突追问') return jsonResponse(conflictProposal);
        return jsonResponse({ status: 'none', proposal: null });
      }
      if (target.endsWith('/api/memory-recalls')) {
        return jsonResponse({ status: 'no_memory', memories: [] });
      }
      if (target.endsWith('/api/adapter-memory-turns')) {
        const body = JSON.parse(init.body);
        stageBodies.push(body);
        const state = body.turns.at(-1)?.content.startsWith('这是我的完整回答：')
          ? 'applied'
          : 'no-change';
        return jsonResponse({
          state,
          ...(state === 'applied'
            ? { memoryChange: { state: 'applied', revision: 1, changeHash: 'sha256:answer' } }
            : {}),
          run: {
            state,
            adapter: {
              operationId: body.operationId,
              sessionId: body.sessionId,
              currentUserTurnId: body.currentUserTurnId,
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
        this.writableEnded = true;
        const text = String(raw);
        this.resolve({
          statusCode: this.statusCode,
          body: text ? JSON.parse(text) : null,
        });
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

    async function waitTerminal(server, marker) {
      let status = { body: { state: 'pending' } };
      for (let attempt = 0; attempt < 100 && status.body.state === 'pending'; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 10));
        status = await request(
          server,
          'GET',
          '/api/next/memory-operations?sessionId=' + encodeURIComponent(marker.sessionId) +
            '&operationId=' + encodeURIComponent(marker.operationId),
        );
      }
      if (status.body.state !== 'ready') throw new Error('operation did not become ready');
      return status;
    }

    const { server } = await import('./testbench/server.mjs?chat-asking-child=' + Date.now());

    const first = await request(server, 'POST', '/api/chat', {
      text: '触发低置信追问',
      originId: 'chat-asking:first',
    });
    const firstSession = first.body.sessionId;
    await waitTerminal(server, first.body.nextMemory);

    const answer = await request(server, 'POST', '/api/chat', {
      text: '这是我的完整回答：我确实更喜欢安静的早晨。',
      originId: 'chat-asking:answer',
      sessionId: firstSession,
    });
    const answerTerminal = await waitTerminal(server, answer.body.nextMemory);

    const repeated = await request(server, 'POST', '/api/chat', {
      text: '触发低置信追问',
      originId: 'chat-asking:repeat',
      sessionId: firstSession,
    });
    await waitTerminal(server, repeated.body.nextMemory);

    await request(server, 'POST', '/api/session/open', { id: firstSession });
    const afterRefresh = await request(server, 'POST', '/api/chat', {
      text: '触发低置信追问',
      originId: 'chat-asking:after-refresh',
      sessionId: firstSession,
    });
    await waitTerminal(server, afterRefresh.body.nextMemory);

    const reset = await request(server, 'POST', '/api/reset');
    const isolated = await request(server, 'POST', '/api/chat', {
      text: '触发低置信追问',
      originId: 'chat-asking:isolated',
      sessionId: reset.body.sessionId,
    });
    await waitTerminal(server, isolated.body.nextMemory);

    const resetAgain = await request(server, 'POST', '/api/reset');
    const conflict = await request(server, 'POST', '/api/chat', {
      text: '触发冲突追问',
      originId: 'chat-asking:conflict',
      sessionId: resetAgain.body.sessionId,
    });
    await waitTerminal(server, conflict.body.nextMemory);

    const ordinary = await request(server, 'POST', '/api/chat', {
      text: '没有候选',
      originId: 'chat-asking:ordinary',
      sessionId: resetAgain.body.sessionId,
    });
    await waitTerminal(server, ordinary.body.nextMemory);

    const answerStage = stageBodies.find(
      (body) => body.turns.at(-1)?.content === '这是我的完整回答：我确实更喜欢安静的早晨。',
    );
    const history = await request(
      server,
      'GET',
      '/api/chat-history?sessionId=' + encodeURIComponent(firstSession),
    );

    process.stdout.write('CHAT_ASKING_RESULT=' + JSON.stringify({
      lowQuestion,
      conflictQuestion,
      first: first.body.record,
      answer: answer.body.record,
      repeated: repeated.body.record,
      afterRefresh: afterRefresh.body.record,
      isolated: isolated.body.record,
      conflict: conflict.body.record,
      ordinary: ordinary.body.record,
      firstSession,
      isolatedSession: reset.body.sessionId,
      history: history.body.turns,
      answerTurns: answerStage?.turns,
      answerEvidenceRecords: answerStage?.evidenceRecords,
      answerTerminalState: answerTerminal.body.nextMemory?.state,
      chatCalls,
      askCalls,
    }));
    process.exit(0);
  `;

  const child = spawnSync(process.execPath, ['--input-type=module', '--eval', script], {
    cwd: process.cwd(),
    encoding: 'utf8',
    timeout: 20_000,
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
    `asking chat child failed\nstdout: ${child.stdout}\nstderr: ${child.stderr}`,
  );
  const resultLine = child.stdout
    .split(/\r?\n/)
    .find((line) => line.startsWith('CHAT_ASKING_RESULT='));
  assert.ok(resultLine, `child did not report asking result\nstdout: ${child.stdout}`);
  const result = JSON.parse(resultLine.slice('CHAT_ASKING_RESULT='.length));

  assert.equal(result.first.reply, result.lowQuestion);
  assert.equal(result.first.proactiveQuestion, result.lowQuestion);
  assert.equal(result.first.llmCalls, 0);
  assert.deepEqual(result.first.hypotheses, [
    {
      text: '你可能更喜欢安静的早晨',
      confidence: 272,
      credStatus: 'candidate',
    },
  ]);
  assert.deepEqual(result.first.conflicts, []);

  assert.equal(result.answer.reply, '普通回复-1');
  assert.equal(result.answer.proactiveQuestion, null);
  assert.equal(result.answerTerminalState, 'applied');
  assert.ok(
    result.answerTurns.some(
      (turn) => turn.role === 'assistant' && turn.content === result.lowQuestion,
    ),
    'the delivered question must return only as assistant context',
  );
  assert.equal(result.answerTurns.at(-1).role, 'user');
  assert.equal(result.answerTurns.at(-1).content, '这是我的完整回答：我确实更喜欢安静的早晨。');
  assert.ok(
    result.answerEvidenceRecords.some(
      (evidence) => evidence.rawContent === '这是我的完整回答：我确实更喜欢安静的早晨。',
    ),
  );
  assert.equal(
    result.answerEvidenceRecords.some((evidence) => evidence.rawContent === result.lowQuestion),
    false,
    'the assistant question must never enter the eligible Evidence envelope',
  );

  for (const record of [result.repeated, result.afterRefresh]) {
    assert.equal(record.proactiveQuestion, null);
    assert.equal(record.reply.startsWith('普通回复-'), true);
    assert.deepEqual(record.hypotheses, []);
    assert.deepEqual(record.conflicts, []);
  }
  assert.equal(
    result.history.filter((record) => record.reply === result.lowQuestion).length,
    1,
    'retry and refresh must not deliver the same recorded question twice in one session',
  );

  assert.notEqual(result.isolatedSession, result.firstSession);
  assert.equal(result.isolated.reply, result.lowQuestion);
  assert.equal(result.isolated.proactiveQuestion, result.lowQuestion);

  assert.equal(result.conflict.reply, result.conflictQuestion);
  assert.equal(result.conflict.proactiveQuestion, result.conflictQuestion);
  assert.deepEqual(result.conflict.hypotheses, []);
  assert.deepEqual(result.conflict.conflicts, [{ detail: '你喜欢早起' }]);
  assert.equal(result.ordinary.proactiveQuestion, null);
  assert.equal(result.ordinary.reply.startsWith('普通回复-'), true);
  assert.equal(result.chatCalls, 4);
  assert.equal(result.askCalls, 7);
});
