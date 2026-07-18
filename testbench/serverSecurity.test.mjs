import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  ClientInputError,
  clientInputRejection,
  encodeDotenvEntries,
  isTrustedLoopbackAuthority,
  requestRejection,
  setOwnPath,
} from './serverSecurity.mjs';

const PORT = 7888;

test('accepts only loopback Host authorities used by the testbench', () => {
  assert.equal(isTrustedLoopbackAuthority('localhost:7888', PORT), true);
  assert.equal(isTrustedLoopbackAuthority('127.0.0.1:7888', PORT), true);
  assert.equal(isTrustedLoopbackAuthority('localhost:9999', PORT), false);
  assert.equal(isTrustedLoopbackAuthority('example.test:7888', PORT), false);
});

test('rejects non-loopback Host for every request before route handling', () => {
  assert.equal(requestRejection({ host: 'localhost:7888' }, 'GET', PORT), null);
  assert.deepEqual(requestRejection({ host: 'example.test:7888' }, 'GET', PORT), {
    statusCode: 403,
    message: '拒绝非本机 Host 的请求',
  });
  assert.deepEqual(requestRejection({ host: 'example.test:7888' }, 'POST', PORT), {
    statusCode: 403,
    message: '拒绝非本机 Host 的请求',
  });
});

test('requires same-origin POST while allowing same-origin GET and local scripts', () => {
  assert.equal(requestRejection({ host: 'localhost:7888' }, 'POST', PORT), null);
  assert.equal(
    requestRejection({ host: 'localhost:7888', origin: 'http://localhost:7888' }, 'POST', PORT),
    null,
  );
  assert.equal(
    requestRejection({ host: 'localhost:7888', origin: 'https://example.test' }, 'GET', PORT),
    null,
  );
  assert.deepEqual(
    requestRejection({ host: 'localhost:7888', origin: 'https://localhost:7888' }, 'POST', PORT),
    { statusCode: 403, message: 'Origin 不可信' },
  );
  assert.deepEqual(
    requestRejection({ host: 'localhost:7888', origin: 'http://127.0.0.1:7888' }, 'POST', PORT),
    { statusCode: 403, message: 'Origin 不可信' },
  );
});

test('setOwnPath updates own configuration fields without traversing prototypes', () => {
  const target = { privacy: { enabled: false } };
  assert.equal(setOwnPath(target, 'privacy.enabled', true), null);
  assert.equal(target.privacy.enabled, true);

  assert.equal(setOwnPath(target, '__proto__.polluted', true), '非法路径：__proto__.polluted');
  assert.equal(
    setOwnPath(target, 'privacy.constructor.prototype.polluted', true),
    '非法路径：privacy.constructor.prototype.polluted',
  );
  assert.equal({}.polluted, undefined);

  const inherited = Object.create({ inherited: { value: false } });
  assert.equal(setOwnPath(inherited, 'inherited.value', true), '路径不存在：inherited');
});

test('maps deliberate client-input failures to a fixed safe 400 response', () => {
  assert.deepEqual(clientInputRejection(new ClientInputError('请求 JSON 无效。')), {
    statusCode: 400,
    message: '请求 JSON 无效。',
  });
  assert.equal(clientInputRejection(new Error('internal path and stack detail')), null);
});

test('dotenv encoding survives Node loadEnvFile without exposing or retaining configuration values', async () => {
  const prefix = `MEMOWEFT_TEST_${Date.now()}_${Math.random().toString(16).slice(2)}_`;
  const entries = {
    [`${prefix}EMPTY`]: '',
    [`${prefix}WRAPPED_SINGLE`]: "'literal'",
    [`${prefix}WRAPPED_DOUBLE`]: '"literal"',
    [`${prefix}MID_SINGLE`]: "mid'dle",
    [`${prefix}MID_DOUBLE`]: 'mid"dle',
    [`${prefix}BACKSLASH`]: 'path\\segment # note',
    [`${prefix}HASH`]: 'value # note',
    [`${prefix}SPACE`]: 'two words',
    [`${prefix}NEWLINE`]: 'first line\nsecond line',
    [`${prefix}DOUBLE_QUOTE`]: 'say "hello" # note',
    [`${prefix}SINGLE_QUOTE`]: "it's # fine",
  };
  const { encoded, unrepresentable } = encodeDotenvEntries(entries);
  assert.deepEqual(unrepresentable, []);

  const incompatible = encodeDotenvEntries({
    [`${prefix}BOTH_QUOTES`]: 'contains \' " marker',
    [`${prefix}BOTH_QUOTES_AND_BACKSLASH`]: 'contains \' " \\ marker',
  });
  assert.deepEqual(incompatible.unrepresentable, [
    `${prefix}BOTH_QUOTES`,
    `${prefix}BOTH_QUOTES_AND_BACKSLASH`,
  ]);

  const directory = await mkdtemp(join(tmpdir(), 'memoweft-dotenv-'));
  const envFile = join(directory, '.env');
  try {
    await writeFile(
      envFile,
      Object.entries(encoded)
        .map(([name, value]) => `${name}=${value}`)
        .join('\n'),
    );
    process.loadEnvFile(envFile);
    for (const [name, value] of Object.entries(entries)) assert.equal(process.env[name], value);
  } finally {
    for (const name of Object.keys(entries)) delete process.env[name];
    await rm(directory, { force: true, recursive: true });
  }
});

test('API-backed memory renderers do not interpolate ids or enum fallbacks into inline handlers', async () => {
  const html = await readFile(new URL('./index.html', import.meta.url), 'utf8');
  assert.doesNotMatch(html, /onclick=.*(?:c\.id|e\.id|evidence\.id|cognition\.id)/);
  assert.doesNotMatch(html, /onchange=.*(?:c\.id|e\.id|evidence\.id|cognition\.id)/);
  assert.doesNotMatch(
    html,
    /\((?:TYPE_CN|FORMED_CN|STATUS_CN|SOURCE_CN)\[[^\]]+\]\s*\|\|\s*(?:c|e|p)\./,
  );
  assert.match(html, /item\.addEventListener\('click', \(\) => mmSelect/);
  assert.match(html, /button\('改原话', \(\) => mmEditEvRaw\(e\.id\)\)/);
});
