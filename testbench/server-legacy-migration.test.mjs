import assert from 'node:assert/strict';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { Readable } from 'node:stream';
import test from 'node:test';
import { openStores } from '../src/store/openStores.ts';

function jsonResponse(value, ok = true) {
  return {
    ok,
    async json() {
      return value;
    },
  };
}

function postToServer(server, body, pathname = '/api/next/legacy-cognition-migrations') {
  const payload = Buffer.from(JSON.stringify(body));
  const req = Readable.from([payload]);
  req.method = 'POST';
  req.url = pathname;
  req.headers = {
    host: '127.0.0.1:7888',
    'content-length': String(payload.length),
  };
  return new Promise((resolve) => {
    const res = {
      writeHead(statusCode) {
        this.statusCode = statusCode;
      },
      end(raw) {
        resolve({ statusCode: this.statusCode, body: JSON.parse(String(raw)) });
      },
    };
    server.emit('request', req, res);
  });
}

test('legacy migration ignores deprecated pending reviews, keeps its in-flight guard, and accepts an automatic terminal', async (t) => {
  const root = await mkdtemp(join(tmpdir(), 'memoweft-server-migration-'));
  const dbPath = join(root, 'testbench-evidence.db');
  const stores = openStores(dbPath);
  const evidence = stores.evidenceStore.put({
    subjectId: 'owner',
    sourceKind: 'spoken',
    hostId: 'test',
    originId: 'legacy-migration-evidence',
    occurredAt: '2026-08-10T09:00:00Z',
    rawContent: '二五有一点傲娇。',
    allowLocalRead: true,
    allowInference: true,
  });
  const cognition = stores.cognitionStore.put({
    subjectId: 'owner',
    content: '二五有一点傲娇。',
    contentType: 'trait',
    formedBy: 'stated',
    confidence: 600,
    credStatus: 'limited',
    evidence: [{ evidenceId: evidence.id, relation: 'support' }],
  });
  stores.close();

  const savedEnv = {
    dbPath: process.env.MEMOWEFT_TESTBENCH_DB_PATH,
    logDir: process.env.MEMOWEFT_TESTBENCH_LOG_DIR,
    experienceUi: process.env.MEMOWEFT_EXPERIENCE_UI,
  };
  const originalFetch = globalThis.fetch;
  let stageCalls = 0;
  let resolveBlockedStage;
  let blockedStageRequested;
  const blockedStageRequestedPromise = new Promise((resolve) => {
    blockedStageRequested = resolve;
  });

  process.env.MEMOWEFT_TESTBENCH_DB_PATH = dbPath;
  process.env.MEMOWEFT_TESTBENCH_LOG_DIR = join(root, 'logs');
  process.env.MEMOWEFT_EXPERIENCE_UI = 'off';
  globalThis.fetch = async (url) => {
    const target = String(url);
    if (target.endsWith('/api/adapter-legacy-memory-imports')) {
      stageCalls++;
      if (stageCalls === 1) {
        blockedStageRequested();
        return new Promise((resolve) => {
          resolveBlockedStage = () =>
            resolve(jsonResponse({ state: 'applied', memoryChange: { revision: 3 } }));
        });
      }
      return jsonResponse({ state: 'applied', memoryChange: { revision: 4 } });
    }
    throw new Error(`unexpected bridge route: ${target}`);
  };

  t.after(() => {
    globalThis.fetch = originalFetch;
    for (const [key, value] of Object.entries(savedEnv)) {
      const envKey =
        key === 'dbPath'
          ? 'MEMOWEFT_TESTBENCH_DB_PATH'
          : key === 'logDir'
            ? 'MEMOWEFT_TESTBENCH_LOG_DIR'
            : 'MEMOWEFT_EXPERIENCE_UI';
      if (value === undefined) delete process.env[envKey];
      else process.env[envKey] = value;
    }
  });

  const { server } = await import(
    `${pathToFileURL(join(process.cwd(), 'testbench/server.mjs')).href}?migration-guard-test=${Date.now()}`
  );
  const body = { cognitionId: cognition.id };

  const first = postToServer(server, body);
  await blockedStageRequestedPromise;
  const concurrent = await postToServer(server, body);
  assert.equal(concurrent.statusCode, 409);
  assert.equal(concurrent.body.code, 'LEGACY_MIGRATION_IN_FLIGHT');
  assert.equal(stageCalls, 1, 'concurrent request is rejected before a second model stage');
  resolveBlockedStage();
  const completed = await first;
  assert.equal(completed.statusCode, 200);
  assert.equal(completed.body.state, 'applied');
  assert.equal(stageCalls, 1, 'the original request alone stages while its guard is held');

  const afterFinally = await postToServer(server, body);
  assert.equal(afterFinally.statusCode, 200);
  assert.equal(afterFinally.body.state, 'applied');
  assert.equal(stageCalls, 2, 'finally releases the guard for a later automatic migration');
});

test('legacy migration never turns a staging-only 2xx receipt into HTTP 200', async (t) => {
  const root = await mkdtemp(join(tmpdir(), 'memoweft-server-migration-nonterminal-'));
  const dbPath = join(root, 'testbench-evidence.db');
  const stores = openStores(dbPath);
  const evidence = stores.evidenceStore.put({
    subjectId: 'owner',
    sourceKind: 'spoken',
    hostId: 'test',
    originId: 'legacy-nonterminal-evidence',
    occurredAt: '2026-08-10T09:00:00Z',
    rawContent: '二五有一点傲娇。',
    allowLocalRead: true,
    allowInference: true,
  });
  const cognition = stores.cognitionStore.put({
    subjectId: 'owner',
    content: '二五有一点傲娇。',
    contentType: 'trait',
    formedBy: 'stated',
    confidence: 600,
    credStatus: 'limited',
    evidence: [{ evidenceId: evidence.id, relation: 'support' }],
  });
  stores.close();
  const savedEnv = {
    dbPath: process.env.MEMOWEFT_TESTBENCH_DB_PATH,
    logDir: process.env.MEMOWEFT_TESTBENCH_LOG_DIR,
    experienceUi: process.env.MEMOWEFT_EXPERIENCE_UI,
  };
  const originalFetch = globalThis.fetch;
  process.env.MEMOWEFT_TESTBENCH_DB_PATH = dbPath;
  process.env.MEMOWEFT_TESTBENCH_LOG_DIR = join(root, 'logs');
  process.env.MEMOWEFT_EXPERIENCE_UI = 'off';
  globalThis.fetch = async () => jsonResponse({ staged: 2 });
  t.after(() => {
    globalThis.fetch = originalFetch;
    for (const [key, value] of Object.entries(savedEnv)) {
      const envKey =
        key === 'dbPath'
          ? 'MEMOWEFT_TESTBENCH_DB_PATH'
          : key === 'logDir'
            ? 'MEMOWEFT_TESTBENCH_LOG_DIR'
            : 'MEMOWEFT_EXPERIENCE_UI';
      if (value === undefined) delete process.env[envKey];
      else process.env[envKey] = value;
    }
  });
  const { server } = await import(
    `${pathToFileURL(join(process.cwd(), 'testbench/server.mjs')).href}?migration-nonterminal-test=${Date.now()}`
  );
  const result = await postToServer(server, { cognitionId: cognition.id });
  assert.equal(result.statusCode, 503);
  assert.equal(result.body.next.code, 'NEXT_INVALID_RESPONSE');
});

test('correction proxy never turns a pending 2xx receipt into HTTP 200', async (t) => {
  const root = await mkdtemp(join(tmpdir(), 'memoweft-server-correction-nonterminal-'));
  const savedEnv = {
    dbPath: process.env.MEMOWEFT_TESTBENCH_DB_PATH,
    logDir: process.env.MEMOWEFT_TESTBENCH_LOG_DIR,
    experienceUi: process.env.MEMOWEFT_EXPERIENCE_UI,
  };
  const originalFetch = globalThis.fetch;
  process.env.MEMOWEFT_TESTBENCH_DB_PATH = join(root, 'testbench-evidence.db');
  process.env.MEMOWEFT_TESTBENCH_LOG_DIR = join(root, 'logs');
  process.env.MEMOWEFT_EXPERIENCE_UI = 'off';
  globalThis.fetch = async (url) => {
    assert.match(String(url), /\/api\/memory-corrections$/);
    return jsonResponse({ state: 'correction-pending', operationId: 'correction-proxy-001' });
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
    for (const [key, value] of Object.entries(savedEnv)) {
      const envKey =
        key === 'dbPath'
          ? 'MEMOWEFT_TESTBENCH_DB_PATH'
          : key === 'logDir'
            ? 'MEMOWEFT_TESTBENCH_LOG_DIR'
            : 'MEMOWEFT_EXPERIENCE_UI';
      if (value === undefined) delete process.env[envKey];
      else process.env[envKey] = value;
    }
  });
  const { server } = await import(
    `${pathToFileURL(join(process.cwd(), 'testbench/server.mjs')).href}?correction-nonterminal-test=${Date.now()}`
  );

  const result = await postToServer(
    server,
    {
      operationId: 'correction-proxy-001',
      cognitionId: 'cognition-proxy-001',
      correctionText: '这是一个不能被当作已完成的待处理纠正。',
    },
    '/api/next/memory-corrections',
  );
  assert.equal(result.statusCode, 503);
  assert.equal(result.body.next.code, 'NEXT_INVALID_RESPONSE');
});
