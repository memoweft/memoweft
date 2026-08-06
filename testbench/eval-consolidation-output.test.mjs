import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { resolveOutputPaths } from '../bench/eval-consolidation.mjs';

test('explicit evaluator output creates a missing parent directory', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'memoweft-eval-output-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const prefix = join(root, 'missing', 'nested', 'run');

  const paths = resolveOutputPaths({ generatedAt: '2026-08-06T00:00:00.000Z' }, prefix);

  assert.equal(paths.md, `${prefix}.md`);
  assert.equal(paths.json, `${prefix}.json`);
  assert.equal(existsSync(join(root, 'missing', 'nested')), true);
});
