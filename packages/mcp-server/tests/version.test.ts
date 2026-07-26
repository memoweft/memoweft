/**
 * 版本一致性守护：MCP_SERVER_VERSION（server.ts 硬编码，serverInfo 用）
 * 必须与 package.json 的 version 一致。历史上二者漂移过（package.json 升到 0.2.1
 * 而代码仍写 0.2.0），本测试把「文档/常量写了、没同步」变成一次红测，防再漂移。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { MCP_SERVER_VERSION } from '../src/index.ts';

test('MCP_SERVER_VERSION 与 package.json version 一致（防漂移）', () => {
  const pkgPath = fileURLToPath(new URL('../package.json', import.meta.url));
  const pkg = JSON.parse(readFileSync(pkgPath, 'utf8')) as { version: string };
  assert.equal(
    MCP_SERVER_VERSION,
    pkg.version,
    'server.ts 的 MCP_SERVER_VERSION 必须与 package.json 的 version 同步',
  );
});
