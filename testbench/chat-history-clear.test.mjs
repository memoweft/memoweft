import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

test('workbench exposes a chat-only clear control with an explicit memory boundary', async () => {
  const html = await readFile(new URL('./index.html', import.meta.url), 'utf8');
  assert.match(html, /id="clearChatHistoryButton"/);
  assert.match(html, />\s*清空聊天记录\s*</);
  assert.match(html, /正式记忆不会删除/);
  assert.match(html, /fetch\('\/api\/sessions\/clear', \{ method: 'POST' \}\)/);
});

test('chat clear archives every visible session and leaves non-chat state untouched', async (t) => {
  const root = await mkdtemp(join(tmpdir(), 'memoweft-chat-clear-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const logDir = join(root, 'logs');
  const dbPath = join(root, 'testbench-evidence.db');
  await mkdir(logDir, { recursive: true });
  await writeFile(
    join(logDir, 'run-s-first.jsonl'),
    `${JSON.stringify({ sessionId: 's-first', turn: 1, userInput: 'first', reply: 'reply' })}\n`,
    'utf8',
  );
  await writeFile(
    join(logDir, 'run-s-second.jsonl'),
    `${JSON.stringify({ sessionId: 's-second', turn: 1, userInput: 'second', reply: 'reply' })}\n`,
    'utf8',
  );
  await writeFile(join(logDir, 'accepted-world-sentinel.txt'), 'KEEP_ACCEPTED_WORLD', 'utf8');

  const script = `
    import { EventEmitter } from 'node:events';
    import { readdir, readFile } from 'node:fs/promises';
    import { join } from 'node:path';
    import { Readable } from 'node:stream';

    class FakeResponse extends EventEmitter {
      constructor() {
        super();
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
        this.body = String(raw);
        this.resolve();
      }
    }

    function request(server, method, url) {
      const req = Readable.from([]);
      req.method = method;
      req.url = url;
      req.headers = { host: '127.0.0.1:7888', 'content-length': '0' };
      const res = new FakeResponse();
      server.emit('request', req, res);
      return res;
    }

    const { server } = await import('./testbench/server.mjs?chat-clear-child=' + Date.now());
    const cleared = request(server, 'POST', '/api/sessions/clear');
    await cleared.done;
    const listed = request(server, 'GET', '/api/sessions');
    await listed.done;
    const names = await readdir(${JSON.stringify(logDir)});
    process.stdout.write('CLEAR_RESULT=' + JSON.stringify({
      clearStatus: cleared.statusCode,
      clear: JSON.parse(cleared.body),
      sessions: JSON.parse(listed.body),
      activeChatLogs: names.filter((name) => /^run-s-.*\\.jsonl$/.test(name)),
      archivedChatLogs: names.filter((name) => /\\.jsonl\\.cleared-/.test(name)).sort(),
      sentinel: await readFile(join(${JSON.stringify(logDir)}, 'accepted-world-sentinel.txt'), 'utf8'),
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
      MEMOWEFT_TESTBENCH_DB_PATH: dbPath,
      MEMOWEFT_TESTBENCH_LOG_DIR: logDir,
    },
  });

  assert.equal(
    child.status,
    0,
    `chat-clear child failed\nstdout: ${child.stdout}\nstderr: ${child.stderr}`,
  );
  const resultLine = child.stdout.split(/\r?\n/).find((line) => line.startsWith('CLEAR_RESULT='));
  assert.ok(resultLine, `child did not report clear result\nstdout: ${child.stdout}`);
  const result = JSON.parse(resultLine.slice('CLEAR_RESULT='.length));
  assert.equal(result.clearStatus, 200);
  assert.equal(result.clear.ok, true);
  assert.equal(result.clear.scope, 'chat-only');
  assert.equal(result.clear.archivedCount, 2);
  assert.equal(result.clear.legacyMemoryPreserved, true);
  assert.equal(result.clear.acceptedWorldPreserved, true);
  assert.match(result.clear.currentId, /^s-/);
  assert.deepEqual(result.sessions.sessions, []);
  assert.equal(result.sessions.currentId, result.clear.currentId);
  assert.deepEqual(result.activeChatLogs, []);
  assert.equal(result.archivedChatLogs.length, 2);
  assert.equal(result.sentinel, 'KEEP_ACCEPTED_WORLD');
});
