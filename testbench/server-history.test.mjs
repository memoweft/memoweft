import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const MAX_CHAT_HISTORY_RESPONSE_BYTES = 512 * 1024;

test('large concurrent chat-history responses stay bounded and an aborted client does not kill the process', async (t) => {
  const root = await mkdtemp(join(tmpdir(), 'memoweft-server-history-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const logDir = join(root, 'logs');
  const dbPath = join(root, 'testbench-evidence.db');
  const sessionId = 's-history-resilience';
  const worldMarker = 'WORLD_SHOULD_NOT_LEAK_'.repeat(40_000);
  const candidateMemory = {
    entities: [],
    relationships: [],
    events: [],
    cognitions: [{ id: 'cognition:gentle', content: '她很温柔' }],
  };
  const records = Array.from({ length: 5 }, (_, index) => ({
    ts: `2026-08-10T00:00:0${index}Z`,
    sessionId,
    turn: index + 1,
    userInput: `user-${index + 1}`,
    reply: `reply-${index + 1}`,
    evidence: [],
    recall: [],
    hypotheses: [],
    conflicts: [],
    proactiveQuestion: null,
    llmCalls: 1,
    profileChanges: [],
    error: null,
    nextMemory: {
      status: 'ok',
      memoryProposal: {
        id: `memory-run-${index + 1}`,
        state: 'candidate-ready',
        resultHash: `sha256:result-${index + 1}`,
        candidateMemory,
        evidence: [{ evidenceId: `evidence-${index + 1}`, text: '她很温柔' }],
      },
      pipeline: [{ name: 'Review', state: 'awaiting-owner', detail: '等待 Owner 决定。' }],
      world: { memory: { cognitions: [{ content: worldMarker }] } },
      run: {
        id: `memory-run-${index + 1}`,
        state: 'candidate-ready',
        resultHash: `sha256:result-${index + 1}`,
        candidateMemory,
        world: { memory: { cognitions: [{ content: worldMarker }] } },
      },
    },
  }));
  await mkdir(logDir, { recursive: true });
  await writeFile(
    join(logDir, `run-${sessionId}.jsonl`),
    records.map((record) => JSON.stringify(record)).join('\n') + '\n',
    'utf8',
  );

  const script = `
    import { EventEmitter } from 'node:events';
    import { Readable } from 'node:stream';

    class FakeResponse extends EventEmitter {
      constructor(abort = false) {
        super();
        this.abort = abort;
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
        if (this.abort) {
          setImmediate(() => {
            this.destroyed = true;
            const error = Object.assign(new Error('client aborted'), { code: 'ECONNRESET' });
            this.emit('error', error);
          });
        }
      }
    }

    function request(server, abort = false) {
      const req = Readable.from([]);
      req.method = 'GET';
      req.url = '/api/chat-history?sessionId=${sessionId}';
      req.headers = { host: '127.0.0.1:7888' };
      const res = new FakeResponse(abort);
      server.emit('request', req, res);
      return res;
    }

    const { server } = await import('./testbench/server.mjs?history-child=' + Date.now());
    const aborted = request(server, true);
    const concurrent = request(server, false);
    await Promise.all([aborted.done, concurrent.done]);
    await new Promise((resolve) => setTimeout(resolve, 25));
    const subsequent = request(server, false);
    await subsequent.done;
    const payload = JSON.parse(concurrent.body);
    const summary = payload.turns[0].nextMemory;
    process.stdout.write('HISTORY_RESULT=' + JSON.stringify({
      bytes: Buffer.byteLength(concurrent.body),
      subsequentStatus: subsequent.statusCode,
      containsWorldMarker: concurrent.body.includes('WORLD_SHOULD_NOT_LEAK'),
      hasWorld: Object.hasOwn(summary, 'world'),
      hasRun: Object.hasOwn(summary, 'run'),
      candidateContent: summary.memoryProposal.candidateMemory.cognitions[0].content,
      runId: summary.memoryProposal.id,
      resultHash: summary.memoryProposal.resultHash,
      turnCount: payload.turns.length,
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
    `aborted history client killed the child process\nstdout: ${child.stdout}\nstderr: ${child.stderr}`,
  );
  const resultLine = child.stdout.split(/\r?\n/).find((line) => line.startsWith('HISTORY_RESULT='));
  assert.ok(resultLine, `child did not report history result\nstdout: ${child.stdout}`);
  const result = JSON.parse(resultLine.slice('HISTORY_RESULT='.length));
  assert.ok(result.bytes <= MAX_CHAT_HISTORY_RESPONSE_BYTES, `history was ${result.bytes} bytes`);
  assert.equal(result.subsequentStatus, 200);
  assert.equal(result.containsWorldMarker, false);
  assert.equal(result.hasWorld, false);
  assert.equal(result.hasRun, false);
  assert.equal(result.candidateContent, '她很温柔');
  assert.equal(result.runId, 'memory-run-1');
  assert.equal(result.resultHash, 'sha256:result-1');
  assert.equal(result.turnCount, 5);
});
