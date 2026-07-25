/**
 * 版本三处一致守护。
 *
 * PUBLISHING.md 要求发布前 package.json / package-lock.json 根条目 / src/version.ts 的
 * MEMOWEFT_VERSION 三处版本号完全一致。历史上「文档写了必须一致、靠人记」会漂
 * （mcp-server 的 MCP_SERVER_VERSION 就漂过一个补丁号），本测试把这条纪律变成一次红测，
 * 让漂移在本地 `npm test` 就撞红，而不是等到打 tag / 发布才发现。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { MEMOWEFT_VERSION } from '../src/version.ts';

function readJson<T>(relPath: string): T {
  const raw = readFileSync(fileURLToPath(new URL(relPath, import.meta.url)), 'utf8');
  return JSON.parse(raw) as T;
}

test('MEMOWEFT_VERSION 与 package.json version 一致（防漂移）', () => {
  const pkg = readJson<{ version: string }>('../package.json');
  assert.equal(
    MEMOWEFT_VERSION,
    pkg.version,
    'src/version.ts 的 MEMOWEFT_VERSION 必须与 package.json 的 version 同步',
  );
});

test('package-lock 根条目 version 与 package.json 一致（防漂移）', () => {
  const pkg = readJson<{ version: string }>('../package.json');
  const lock = readJson<{ packages?: Record<string, { version?: string }> }>(
    '../package-lock.json',
  );
  const rootVersion = lock.packages?.['']?.version;
  assert.equal(
    rootVersion,
    pkg.version,
    'package-lock.json 根条目 packages[""].version 必须与 package.json 的 version 同步',
  );
});
