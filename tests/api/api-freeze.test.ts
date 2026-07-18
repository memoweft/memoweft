/**
 * Public API compatibility test.
 * 重新生成公共导出面并与 tests/api/api-surface.snapshot 逐字比对,不一致即红。
 * Intentional changes require a compatibility review, then `npm run api:update`.
 * Keep the API contract and changelog synchronized in the same commit.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { generateSnapshot, SNAPSHOT_PATH } from '../../scripts/api-snapshot.mjs';

test('api-freeze:公共 API 与冻结快照逐字一致', () => {
  const current = generateSnapshot();

  let stored: string;
  try {
    stored = readFileSync(SNAPSHOT_PATH, 'utf8');
  } catch {
    assert.fail(
      '缺少 API 快照文件 tests/api/api-surface.snapshot,请运行 `npm run api:update` 生成。',
    );
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
    '公共 API 与冻结快照不一致。\n' +
      diff +
      '\n\nIf the change is accidental, restore API compatibility.\n' +
      'If it is intentional, review the compatibility and migration impact, run `npm run api:update`, ' +
      'and update the API contract and changelog in the same commit.',
  );
});
