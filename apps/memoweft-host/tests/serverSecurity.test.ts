/**
 * Reference Host 的 HTTP 边界回归测试。
 *
 * server.ts 顶层会启动真实服务，故以子进程跑在临时库/临时 .env 上测试；不触及开发者的 Host 数据。
 */
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { mkdtempSync, readFileSync, rmSync, statSync } from 'node:fs';
import { createServer as createHttpServer, request as httpRequest, type Server } from 'node:http';
import { createServer } from 'node:net';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { spawn, type ChildProcess } from 'node:child_process';

const HOST_DIR = join(import.meta.dirname, '..');
const MAX_BODY_BYTES = 5 * 1024 * 1024;
const UPSTREAM_SECRET = 'SENSITIVE_UPSTREAM_ERROR_74A1';
let port = 0;
let baseUrl = '';
let csrfToken = '';
let child: ChildProcess | undefined;
let llmServer: Server | undefined;
let hostStderr = '';
let testDir = '';

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const address = probe.address();
      if (!address || typeof address === 'string') {
        reject(new Error('未能取得测试端口'));
        return;
      }
      probe.close((error) => (error ? reject(error) : resolve(address.port)));
    });
  });
}

function rawPost(
  path: string,
  headers: Record<string, string>,
  body = '',
): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const req = httpRequest(
      {
        host: '127.0.0.1',
        port,
        path,
        method: 'POST',
        headers,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (chunk: Buffer) => chunks.push(chunk));
        res.on('end', () => {
          resolve({ status: res.statusCode ?? 0, body: Buffer.concat(chunks).toString('utf8') });
        });
      },
    );
    req.once('error', reject);
    req.end(body);
  });
}

function rawGet(
  path: string,
  headers: Record<string, string>,
): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const req = httpRequest({ host: '127.0.0.1', port, path, method: 'GET', headers }, (res) => {
      const chunks: Buffer[] = [];
      res.on('data', (chunk: Buffer) => chunks.push(chunk));
      res.on('end', () => {
        resolve({ status: res.statusCode ?? 0, body: Buffer.concat(chunks).toString('utf8') });
      });
    });
    req.once('error', reject);
    req.end();
  });
}

function trustedHeaders(overrides: Record<string, string> = {}): Record<string, string> {
  return {
    Host: `127.0.0.1:${port}`,
    Origin: baseUrl,
    'Content-Type': 'application/json; charset=utf-8',
    'X-MemoWeft-CSRF-Token': csrfToken,
    ...overrides,
  };
}

before(async () => {
  testDir = mkdtempSync(join(tmpdir(), 'memoweft-host-security-'));
  port = await freePort();
  baseUrl = `http://127.0.0.1:${port}`;
  llmServer = createHttpServer((_req, res) => {
    // 让 Host 稳定进入 outcome.error 分支，并模拟上游响应中带有不应回显的敏感内容。
    res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ error: { message: UPSTREAM_SECRET } }));
  });
  const llmPort = await new Promise<number>((resolve, reject) => {
    llmServer!.once('error', reject);
    llmServer!.listen(0, '127.0.0.1', () => {
      const address = llmServer!.address();
      if (!address || typeof address === 'string') {
        reject(new Error('未能取得模拟 LLM 端口'));
        return;
      }
      resolve(address.port);
    });
  });
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    PORT: String(port),
    MEMOWEFT_HOST_DB: join(testDir, 'host.db'),
    MEMOWEFT_HOST_ENV_PATH: join(testDir, '.env'),
    MEMOWEFT_EXPERIENCE_UI: 'on',
  };
  for (const name of [
    'MEMOWEFT_LLM_BASE_URL',
    'MEMOWEFT_LLM_API_KEY',
    'MEMOWEFT_LLM_MODEL',
    'MEMOWEFT_LLM_TIMEOUT_MS',
    'MEMOWEFT_WRITE_LLM_BASE_URL',
    'MEMOWEFT_WRITE_LLM_API_KEY',
    'MEMOWEFT_WRITE_LLM_MODEL',
    'MEMOWEFT_WRITE_LLM_TIMEOUT_MS',
    'DLA_LLM_BASE_URL',
    'DLA_LLM_API_KEY',
    'DLA_LLM_MODEL',
    'DLA_LLM_TIMEOUT_MS',
    'DLA_WRITE_LLM_BASE_URL',
    'DLA_WRITE_LLM_API_KEY',
    'DLA_WRITE_LLM_MODEL',
    'DLA_WRITE_LLM_TIMEOUT_MS',
  ]) {
    delete env[name];
  }
  env.MEMOWEFT_LLM_BASE_URL = `http://127.0.0.1:${llmPort}/v1`;
  env.MEMOWEFT_LLM_API_KEY = UPSTREAM_SECRET;
  env.MEMOWEFT_LLM_MODEL = 'security-test-model';
  env.MEMOWEFT_WRITE_LLM_BASE_URL = env.MEMOWEFT_LLM_BASE_URL;
  env.MEMOWEFT_WRITE_LLM_API_KEY = UPSTREAM_SECRET;
  env.MEMOWEFT_WRITE_LLM_MODEL = 'security-test-model';
  child = spawn(process.execPath, ['src/server.ts'], {
    cwd: HOST_DIR,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  await new Promise<void>((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error(`Host 启动超时：${output}`)), 15_000);
    child!.stdout!.on('data', (chunk: Buffer) => {
      output += chunk.toString('utf8');
      if (output.includes('MemoWeft Reference Host →')) {
        clearTimeout(timer);
        resolve();
      }
    });
    child!.stderr!.on('data', (chunk: Buffer) => {
      const text = chunk.toString('utf8');
      output += text;
      hostStderr += text;
    });
    child!.once('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`Host 提前退出（${code}）：${output}`));
    });
  });

  const html = await fetch(`${baseUrl}/`).then((response) => response.text());
  const match = html.match(/const CSRF_TOKEN = '([^']+)'/);
  assert.ok(match?.[1], '同源 HTML 注入了本次启动的随机 CSRF token');
  csrfToken = match[1];
  assert.ok(!html.includes('__MEMOWEFT_CSRF_TOKEN__'), '不会把未替换的占位符交给前端');
  assert.match(html, /不用于内建云写模型/, '授权标签准确限定为内建云写模型');
  assert.match(
    html,
    /不会限制回忆、读取\s+API、MCP、自定义宿主或派生理解/,
    '页面明确说明该授权位不是通用访问控制',
  );
});

after(async () => {
  if (child && !child.killed) {
    child.kill('SIGTERM');
    await new Promise<void>((resolve) => child!.once('exit', () => resolve()));
  }
  if (llmServer) {
    await new Promise<void>((resolve, reject) =>
      llmServer!.close((error) => (error ? reject(error) : resolve())),
    );
  }
  if (testDir) rmSync(testDir, { recursive: true, force: true });
});

test('拒绝异常 Host 与跨源 Origin', async () => {
  const badHost = await rawPost('/api/reset', trustedHeaders({ Host: 'attacker.example' }), '{}');
  assert.equal(badHost.status, 403, 'DNS rebinding 的 Host 不能改变本机状态');

  const badOrigin = await rawPost(
    '/api/reset',
    trustedHeaders({ Origin: 'http://attacker.example' }),
    '{}',
  );
  assert.equal(badOrigin.status, 403, '跨源浏览器请求不能改变本机状态');
});

test('所有 GET 响应也拒绝异常 Host，合法 loopback Host 仍可读取', async () => {
  const hostileHome = await rawGet('/', { Host: 'attacker.example' });
  assert.equal(hostileHome.status, 403, '恶意 Host 不能读取内嵌 CSRF token 的首页');

  const hostileExport = await rawGet('/api/export-bundle', { Host: 'attacker.example' });
  assert.equal(hostileExport.status, 403, '恶意 Host 不能读取本机记忆导出');

  const loopbackHealth = await rawGet('/api/health', { Host: `127.0.0.1:${port}` });
  assert.equal(loopbackHealth.status, 200);
  const localhostHealth = await rawGet('/api/health', { Host: `localhost:${port}` });
  assert.equal(localhostHealth.status, 200);
});

test('状态更改拒绝 text/plain 与缺失 token', async () => {
  const plain = await rawPost('/api/reset', trustedHeaders({ 'Content-Type': 'text/plain' }), '{}');
  assert.equal(plain.status, 415, 'simple text/plain POST 被拒绝');

  const noToken = await rawPost(
    '/api/reset',
    trustedHeaders({ 'X-MemoWeft-CSRF-Token': '' }),
    '{}',
  );
  assert.equal(noToken.status, 403, '缺少会话 token 的状态更改被拒绝');
});

test('本机辅助进程可取得会话 token 并通过受保护的 observe 端点', async () => {
  const tokenResponse = await rawGet('/api/csrf-token', { Host: `127.0.0.1:${port}` });
  assert.equal(tokenResponse.status, 200);
  const payload = JSON.parse(tokenResponse.body) as { token?: string };
  assert.equal(payload.token, csrfToken, '辅助进程取得与同源页面相同的本次启动 token');

  const rejectedHost = await rawGet('/api/csrf-token', { Host: 'attacker.example' });
  assert.equal(rejectedHost.status, 403);
  const rejectedOrigin = await rawGet('/api/csrf-token', {
    Host: `127.0.0.1:${port}`,
    Origin: 'http://attacker.example',
  });
  assert.equal(rejectedOrigin.status, 403);

  const observed = await rawPost(
    '/api/observe',
    trustedHeaders({ Origin: '', 'X-MemoWeft-CSRF-Token': payload.token! }),
    JSON.stringify({
      observations: [
        {
          kind: 'active_window',
          content: 'Synthetic collector integration check',
          occurredAt: '2026-07-18T00:00:00.000Z',
          originId: 'security-test-observation',
        },
      ],
    }),
  );
  assert.equal(observed.status, 200);
  assert.equal((JSON.parse(observed.body) as { stored?: number }).stored, 1);
});

test('在完整缓冲前拒绝超过上限的请求体', async () => {
  const response = await rawPost(
    '/api/save-env',
    trustedHeaders({ 'Content-Length': String(MAX_BODY_BYTES + 1) }),
  );
  assert.equal(response.status, 413);
  assert.match(response.body, /请求体过大/);
});

test('无效 JSON 仍以安全的 400 合约响应，不回显运行时解析细节', async () => {
  const response = await fetch(`${baseUrl}/api/save-env`, {
    method: 'POST',
    headers: trustedHeaders(),
    body: '{',
  });
  const responseBody = await response.text();
  assert.equal(response.status, 400);
  assert.deepEqual(JSON.parse(responseBody), { error: '请求体必须是有效 JSON' });
  assert.ok(!responseBody.includes('SyntaxError'));
  assert.ok(!responseBody.includes('server.ts'));
});

test('聊天失败维持 200 + error 合约，且不把用户秘密或内部错误回显给客户端', async () => {
  const secret = 'SENSITIVE_CHAT_INPUT_4C3D';
  const response = await fetch(`${baseUrl}/api/chat`, {
    method: 'POST',
    headers: trustedHeaders(),
    body: JSON.stringify({ message: secret }),
  });
  const responseBody = await response.text();
  const payload = JSON.parse(responseBody) as { error?: string; recall?: unknown[] };
  assert.equal(response.status, 200, '聊天失败仍维持既有 HTTP 成功响应形状');
  assert.equal(payload.error, '回话没成功，请稍后重试。');
  assert.deepEqual(payload.recall, []);
  assert.ok(!responseBody.includes(secret), '响应不回显可能被上游回显的用户秘密');
  assert.ok(!responseBody.includes(UPSTREAM_SECRET), '响应不回显上游错误里的秘密');
  assert.ok(!responseBody.includes('Error:'), '响应不回显内部异常');
  assert.ok(!hostStderr.includes(UPSTREAM_SECRET), '默认 Host 日志也不记录上游秘密');
});

test('save-env 成功只确认保存，不泄露完整 env 或绝对路径', async () => {
  const apiKey = 'super-secret-key-that-must-not-be-echoed';
  const response = await fetch(`${baseUrl}/api/save-env`, {
    method: 'POST',
    headers: trustedHeaders(),
    body: JSON.stringify({
      llmBaseUrl: 'https://api.example.com/v1',
      llmApiKey: apiKey,
      llmModel: 'example-chat',
    }),
  });
  const responseBody = await response.text();

  assert.equal(response.status, 200);
  assert.deepEqual(JSON.parse(responseBody), { saved: true });
  assert.ok(!responseBody.includes(apiKey), '响应不回显 API key');
  assert.ok(!responseBody.includes(testDir), '响应不回显绝对路径');

  const saved = readFileSync(join(testDir, '.env'), 'utf8');
  assert.ok(saved.includes(apiKey), '配置实际写入目标 .env');
  if (process.platform !== 'win32') {
    assert.equal(statSync(join(testDir, '.env')).mode & 0o077, 0, '.env 仅当前用户可读写');
  }

  // 已有目标文件时也应替换成功（同目录临时文件 + rename 的常规保存路径）。
  const replacementKey = 'replacement-key';
  const replacement = await fetch(`${baseUrl}/api/save-env`, {
    method: 'POST',
    headers: trustedHeaders(),
    body: JSON.stringify({
      llmBaseUrl: 'https://api.example.com/v1',
      llmApiKey: replacementKey,
      llmModel: 'replacement-chat',
    }),
  });
  assert.equal(replacement.status, 200, '已有 .env 时可安全替换');
  const replaced = readFileSync(join(testDir, '.env'), 'utf8');
  assert.ok(replaced.includes(replacementKey));
  assert.ok(!replaced.includes(apiKey), '替换后不保留旧的完整内容');
});
