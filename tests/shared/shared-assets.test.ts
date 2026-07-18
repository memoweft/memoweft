/**
 * 语言中立共享资产 · 漂移检查（Python 移植）。
 *
 * shared/ 下的 config-constants.json / prompts.json / parity/*.json 是从 TS 源生成的、供 Python 移植版
 *   载入的同源资产。本测试重新生成一遍、与 committed 逐字比对；变更 TS 后请运行
 *   `npm run shared:update` 刷新资产。
 *
 * 另断言几条不变量，防生成器退化：①提示词 sha256 与快照对齐
 *   ②parity 夹具规模不为空 ③边界样例在位。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { buildSharedAssets, stableStringify, SHARED } from '../../scripts/gen-shared-assets.mjs';

/**
 * 唯一破例逐字比对的资产:`parity/decay.json`。
 *
 * 为什么：decayFactor 走 `Math.pow(0.5, x)`，而 IEEE754 不要求 pow 正确舍入，
 *   不同运行时可能产生 1 ULP 的差异。逐字比对 JSON 文本会把这种数值差异判成漂移，
 *   因此浮点值使用与 Python parity 检查一致的相对容差。
 *
 * 键集、结构、字符串和整数仍严格相等；只有非整数浮点值允许 1e-15 相对容差。
 */
const FLOAT_TOLERANT = new Set(['parity/decay.json']);

function assertEqualTolerant(a: unknown, b: unknown, path: string): void {
  if (typeof a === 'number' && typeof b === 'number') {
    if (Object.is(a, b)) return;
    // 整数(含 effectiveConfidence 的输出)必须逐位相等,不吃容差。
    assert.ok(
      !Number.isInteger(a) && !Number.isInteger(b),
      `${path}: 整数不一致 ${a} ≠ ${b}(整数不吃浮点容差)`,
    );
    const tol = Math.max(1e-15 * Math.max(Math.abs(a), Math.abs(b)), 1e-18);
    assert.ok(Math.abs(a - b) <= tol, `${path}: 浮点超出 1e-15 相对容差 ${a} ≠ ${b}`);
    return;
  }
  if (Array.isArray(a) || Array.isArray(b)) {
    assert.ok(Array.isArray(a) && Array.isArray(b), `${path}: 一侧不是数组`);
    assert.equal(a.length, b.length, `${path}: 数组长度不一致`);
    a.forEach((_, i) => assertEqualTolerant(a[i], b[i], `${path}[${i}]`));
    return;
  }
  if (a && b && typeof a === 'object' && typeof b === 'object') {
    const ka = Object.keys(a as object).sort();
    const kb = Object.keys(b as object).sort();
    assert.deepEqual(ka, kb, `${path}: 键集不一致`);
    for (const k of ka) {
      assertEqualTolerant(
        (a as Record<string, unknown>)[k],
        (b as Record<string, unknown>)[k],
        `${path}.${k}`,
      );
    }
    return;
  }
  assert.equal(a, b, `${path}: 值不一致`);
}

test('shared-assets:committed 与 TS 源逐字一致', async () => {
  const assets = await buildSharedAssets();
  for (const [rel, obj] of Object.entries(assets) as [string, unknown][]) {
    const p = join(SHARED, rel);
    assert.ok(existsSync(p), `缺少 shared/${rel},请运行 \`npm run shared:update\``);
    const committed = readFileSync(p, 'utf8');
    const fresh = stableStringify(obj);
    if (committed === fresh) continue;
    // 唯有 decay.json 允许 1 ULP 级的浮点差(见上方说明);其余一律逐字。
    assert.ok(
      FLOAT_TOLERANT.has(rel),
      `shared/${rel} 与 TS 源不一致,请运行 \`npm run shared:update\` 刷新`,
    );
    assertEqualTolerant(JSON.parse(committed), JSON.parse(fresh), `shared/${rel}`);
  }
});

test('shared-assets:prompts.json 的 zh/en sha256 与 prompt-hashes 快照对齐(同一字节提示词)', async () => {
  const assets = (await buildSharedAssets()) as Record<
    string,
    { prompts: { id: string; version: string; text: { zh: string; en: string } }[] }
  >;
  const prompts = assets['prompts.json']!.prompts;
  const snapPath = join(SHARED, '..', 'tests', 'prompts', 'prompt-hashes.snapshot');
  const snap = readFileSync(snapPath, 'utf8');
  const sha = (s: string) => createHash('sha256').update(s, 'utf8').digest('hex');
  let checked = 0;
  for (const p of prompts) {
    for (const lang of ['zh', 'en'] as const) {
      const h = sha(p.text[lang]);
      // 快照里应出现该 hash(格式不强绑,只要该 64-hex 存在即证共享文本与受治理提示词字节一致)。
      assert.ok(
        snap.includes(h),
        `prompt ${p.id}.${lang} 的 sha256 未出现在 prompt-hashes 快照 → 共享 prompts.json 与受治理提示词漂移了`,
      );
      checked++;
    }
  }
  assert.equal(checked, prompts.length * 2, '应校验每条提示词的 zh+en');
  assert.ok(prompts.length >= 8, `受治理提示词应 ≥8 条,实得 ${prompts.length}`);
});

test('shared-assets:parity 夹具非空 + 边界样例在位', async () => {
  const a = (await buildSharedAssets()) as Record<string, unknown>;
  const conf = a['parity/confidence.json'] as { cases: unknown[] };
  const decay = a['parity/decay.json'] as {
    effectiveConfidence: { cases: { input: { cog: { contentType: string } } }[] };
  };
  const he = a['parity/hash-embedder.json'] as {
    fnv1a32: { cases: { input: string; expected: number }[] };
  };
  assert.ok(
    conf.cases.length >= 1000,
    `confidence 夹具应覆盖全组合(≥1000),实得 ${conf.cases.length}`,
  );
  // 边界样例：decay 夹具含 transient 类型(state)的衰减样例。
  assert.ok(
    decay.effectiveConfidence.cases.some((c) => c.input.cog.contentType === 'state'),
    'decay 夹具应含 state 衰减样例(round 半值边界)',
  );
  // 边界样例：fnv1a32 夹具含 uint32 输出，且 CJK token 在位。
  const fnvHan = he.fnv1a32.cases.find((c) => c.input === '饮');
  assert.ok(
    fnvHan &&
      Number.isInteger(fnvHan.expected) &&
      fnvHan.expected >= 0 &&
      fnvHan.expected <= 0xffffffff,
    'fnv1a32 应含 CJK token 的 uint32 输出(Math.imul parity 锚)',
  );
  // 配置资产应存在且带 baseByFormedBy。
  const cfg = a['config-constants.json'] as {
    consolidation: { baseByFormedBy: Record<string, number> };
  };
  assert.equal(
    cfg.consolidation.baseByFormedBy.stated,
    600,
    'config-constants 应携带 baseByFormedBy 数值(Python 单一源)',
  );
});
