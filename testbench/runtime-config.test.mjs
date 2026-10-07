import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join, resolve } from 'node:path';
import { memoryAuthorityLabel, readTestbenchRuntimeConfig } from './runtime-config.mjs';

const testbenchDir = resolve('testbench');
const isolatedDir = resolve('isolated');

test('runtime config preserves the 1.x defaults when isolation overrides are absent', () => {
  const config = readTestbenchRuntimeConfig({ env: {}, dirname: testbenchDir });
  assert.equal(config.port, 7888);
  assert.match(config.dbPath, /testbench-evidence\.db$/);
  assert.match(config.logDir, /logs$/);
  assert.equal(config.profileEveryTurn, false);
  assert.equal(config.memoryAuthority, 'legacy');
  assert.equal(config.memoryAuthorityLabel, 'legacy-cognition');
});

test('runtime config enables the accepted 2.0 world only through the exact next authority switch', () => {
  const config = readTestbenchRuntimeConfig({
    env: { MEMOWEFT_TESTBENCH_MEMORY_AUTHORITY: 'next' },
    dirname: testbenchDir,
  });

  assert.equal(config.memoryAuthority, 'next');
  assert.equal(config.memoryAuthorityLabel, 'next-accepted-world');
  assert.equal(memoryAuthorityLabel('legacy'), 'legacy-cognition');
  for (const value of ['', 'NEXT', '1', 'accepted-world']) {
    assert.throws(
      () =>
        readTestbenchRuntimeConfig({
          env: { MEMOWEFT_TESTBENCH_MEMORY_AUTHORITY: value },
          dirname: testbenchDir,
        }),
      /MEMOWEFT_TESTBENCH_MEMORY_AUTHORITY.*legacy.*next/,
    );
  }
});

test('runtime config enables per-turn profile scheduling only with the explicit on switch', () => {
  const enabled = readTestbenchRuntimeConfig({
    env: { MEMOWEFT_TESTBENCH_PROFILE_EVERY_TURN: 'on' },
    dirname: testbenchDir,
  });
  const disabled = readTestbenchRuntimeConfig({
    env: { MEMOWEFT_TESTBENCH_PROFILE_EVERY_TURN: 'off' },
    dirname: testbenchDir,
  });

  assert.equal(enabled.profileEveryTurn, true);
  assert.equal(disabled.profileEveryTurn, false);
});

test('runtime config rejects an unsafe or ambiguous per-turn profile switch', () => {
  for (const value of ['', 'ON', 'true', '1', 'sometimes']) {
    assert.throws(
      () =>
        readTestbenchRuntimeConfig({
          env: { MEMOWEFT_TESTBENCH_PROFILE_EVERY_TURN: value },
          dirname: testbenchDir,
        }),
      /MEMOWEFT_TESTBENCH_PROFILE_EVERY_TURN.*on.*off/,
    );
  }
});

test('runtime config accepts an isolated loopback port and explicit absolute DB/log paths only', () => {
  const config = readTestbenchRuntimeConfig({
    env: {
      MEMOWEFT_TESTBENCH_PORT: '7889',
      MEMOWEFT_TESTBENCH_DB_PATH: join(isolatedDir, 'evidence.db'),
      MEMOWEFT_TESTBENCH_LOG_DIR: join(isolatedDir, 'logs'),
    },
    dirname: testbenchDir,
  });
  assert.equal(config.port, 7889);
  assert.match(config.dbPath, /isolated[\\/]evidence\.db$/);
  assert.match(config.logDir, /isolated[\\/]logs$/);
  assert.throws(() =>
    readTestbenchRuntimeConfig({ env: { MEMOWEFT_TESTBENCH_PORT: '0' }, dirname: testbenchDir }),
  );
  assert.throws(() =>
    readTestbenchRuntimeConfig({
      env: { MEMOWEFT_TESTBENCH_DB_PATH: join('.', 'relative.db') },
      dirname: testbenchDir,
    }),
  );
});
