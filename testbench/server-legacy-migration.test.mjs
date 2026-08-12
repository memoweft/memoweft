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

function postToServer(server, body) {
  const payload = Buffer.from(JSON.stringify(body));
  const req = Readable.from([payload]);
  req.method = 'POST';
  req.url = '/api/next/legacy-cognition-migrations';
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

test('legacy migration preflight blocks pending and concurrent imports before a model stage, then releases its guard', async (t) => {
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
  let mode = 'pending';
  let stageCalls = 0;
  let resolveBlockedWorld;
  let blockedWorldRequested;
  const blockedWorldRequestedPromise = new Promise((resolve) => {
    blockedWorldRequested = resolve;
  });

  process.env.MEMOWEFT_TESTBENCH_DB_PATH = dbPath;
  process.env.MEMOWEFT_TESTBENCH_LOG_DIR = join(root, 'logs');
  process.env.MEMOWEFT_EXPERIENCE_UI = 'off';
  globalThis.fetch = async (url) => {
    const target = String(url);
    if (target.endsWith('/api/memory-world')) {
      if (mode === 'pending') return jsonResponse({ pendingReviews: ['review:existing'] });
      if (mode === 'unavailable') throw new Error('bridge unavailable');
      if (mode === 'blocked') {
        blockedWorldRequested();
        return new Promise((resolve) => {
          resolveBlockedWorld = () => resolve(jsonResponse({ pendingReviews: [] }));
        });
      }
      return jsonResponse({ pendingReviews: [] });
    }
    if (target.endsWith('/api/adapter-legacy-memory-imports')) {
      stageCalls++;
      return jsonResponse({ staged: true });
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

  const pending = await postToServer(server, body);
  assert.equal(pending.statusCode, 409);
  assert.deepEqual(pending.body, {
    code: 'LEGACY_MIGRATION_PENDING_REVIEW_EXISTS',
    error: '请先处理当前待决定候选，再迁移下一条',
    message: '请先处理当前待决定候选，再迁移下一条',
  });
  assert.equal(stageCalls, 0, 'pending-review preflight must not invoke the migration model');

  mode = 'unavailable';
  const unavailable = await postToServer(server, body);
  assert.equal(unavailable.statusCode, 503);
  assert.deepEqual(unavailable.body, {
    next: { status: 'unavailable', code: 'NEXT_UNAVAILABLE' },
  });
  assert.equal(stageCalls, 0, 'unavailable preflight must not invoke the migration model');

  mode = 'blocked';
  const first = postToServer(server, body);
  await blockedWorldRequestedPromise;
  const concurrent = await postToServer(server, body);
  assert.equal(concurrent.statusCode, 409);
  assert.equal(concurrent.body.code, 'LEGACY_MIGRATION_IN_FLIGHT');
  assert.equal(stageCalls, 0, 'concurrent request is rejected before a second model stage');
  resolveBlockedWorld();
  const completed = await first;
  assert.equal(completed.statusCode, 200);
  assert.equal(stageCalls, 1, 'the original request alone stages after its clean preflight');

  mode = 'pending';
  const afterFinally = await postToServer(server, body);
  assert.equal(afterFinally.statusCode, 409);
  assert.equal(afterFinally.body.code, 'LEGACY_MIGRATION_PENDING_REVIEW_EXISTS');
  assert.equal(
    stageCalls,
    1,
    'finally releases the guard without permitting a pending-review model stage',
  );
});
