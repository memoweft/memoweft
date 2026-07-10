/**
 * 公共 API 冻结测试(PROJECT_PLAN.md 第 13 章 · 铁律 2 的机器强制)。
 * 重新生成公共导出面并与 tests/api/api-surface.snapshot 逐字比对,不一致即红。
 * 合法变更流程:影响面说明 + 人类批准 + D-xxxx → `npm run api:update` 刷新快照
 *   → 同步 docs/memory-surface-contract.md 与 CHANGELOG,在同一 commit。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { generateSnapshot, SNAPSHOT_PATH } from '../../scripts/api-snapshot.mjs';

test('api-freeze:公共 API 与快照逐字一致(铁律 2)', () => {
  const current = generateSnapshot();

  let stored: string;
  try {
    stored = readFileSync(SNAPSHOT_PATH, 'utf8');
  } catch {
    assert.fail('缺少 API 快照文件 tests/api/api-surface.snapshot,请运行 `npm run api:update` 生成。');
    return;
  }

  if (current === stored) return;

  // 定位首个差异行,便于阅读
  const a = stored.split('\n');
  const b = current.split('\n');
  const n = Math.max(a.length, b.length);
  let diff = '(无法定位具体差异行)';
  for (let i = 0; i < n; i++) {
    if (a[i] !== b[i]) {
      diff = `第 ${i + 1} 行差异:\n  - 快照: ${a[i] ?? '(无此行)'}\n  + 当前: ${b[i] ?? '(无此行)'}`;
      break;
    }
  }

  assert.fail(
    '公共 API 与快照不一致(铁律 2:API 冻结)。\n' +
      diff +
      '\n\n若为【无意】破坏:回滚改动使其恢复一致。\n' +
      '若为【有意】变更:需影响面说明 + 人类批准 + D-xxxx,然后 `npm run api:update` 刷新快照,' +
      '并在同一 commit 同步 docs/memory-surface-contract.md 与 CHANGELOG。',
  );
});
