/**
 * 语言中立共享资产 · 漂移守门(1.3 Python 移植 · Phase 0 · D-0042)。
 *
 * shared/ 下的 config-constants.json / prompts.json / parity/*.json 是从 TS 源【生成】的、供 Python 移植版
 *   载入的同源资产。本测试重新生成一遍、与 committed 逐字比对——**TS 源一改、committed 不刷新就变红**
 *   (同 api-freeze / prompt-hashes 的机器强制)。合法变更:改 TS 后跑 `npm run shared:update` 刷新、同 commit。
 *
 * 另断言几条不变量,防生成器自身悄悄退化:①提示词 sha256 与 tests/prompts/prompt-hashes.snapshot 对齐
 *   (证共享 prompts.json 与受治理提示词是同一字节)②parity 夹具规模不为空 ③三个 parity 杀手样例在位。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { buildSharedAssets, stableStringify, SHARED } from '../../scripts/gen-shared-assets.mjs';

test('shared-assets:committed 与 TS 源逐字一致(漂移守门)', async () => {
  const assets = await buildSharedAssets();
  for (const [rel, obj] of Object.entries(assets) as [string, unknown][]) {
    const p = join(SHARED, rel);
    assert.ok(existsSync(p), `缺少 shared/${rel},请运行 \`npm run shared:update\``);
    const committed = readFileSync(p, 'utf8');
    const fresh = stableStringify(obj);
    assert.equal(committed, fresh, `shared/${rel} 与 TS 源不一致,请运行 \`npm run shared:update\` 刷新`);
  }
});

test('shared-assets:prompts.json 的 zh/en sha256 与 prompt-hashes 快照对齐(同一字节提示词)', async () => {
  const assets = (await buildSharedAssets()) as Record<string, { prompts: { id: string; version: string; text: { zh: string; en: string } }[] }>;
  const prompts = assets['prompts.json']!.prompts;
  const snapPath = join(SHARED, '..', 'tests', 'prompts', 'prompt-hashes.snapshot');
  const snap = readFileSync(snapPath, 'utf8');
  const sha = (s: string) => createHash('sha256').update(s, 'utf8').digest('hex');
  let checked = 0;
  for (const p of prompts) {
    for (const lang of ['zh', 'en'] as const) {
      const h = sha(p.text[lang]);
      // 快照里应出现该 hash(格式不强绑,只要该 64-hex 存在即证共享文本与受治理提示词字节一致)。
      assert.ok(snap.includes(h), `prompt ${p.id}.${lang} 的 sha256 未出现在 prompt-hashes 快照 → 共享 prompts.json 与受治理提示词漂移了`);
      checked++;
    }
  }
  assert.equal(checked, prompts.length * 2, '应校验每条提示词的 zh+en');
  assert.ok(prompts.length >= 8, `受治理提示词应 ≥8 条,实得 ${prompts.length}`);
});

test('shared-assets:parity 夹具非空 + 三个 parity 杀手样例在位', async () => {
  const a = (await buildSharedAssets()) as Record<string, unknown>;
  const conf = a['parity/confidence.json'] as { cases: unknown[] };
  const decay = a['parity/decay.json'] as { effectiveConfidence: { cases: { input: { cog: { contentType: string } } }[] } };
  const he = a['parity/hash-embedder.json'] as { fnv1a32: { cases: { input: string; expected: number }[] } };
  assert.ok(conf.cases.length >= 1000, `confidence 夹具应覆盖全组合(≥1000),实得 ${conf.cases.length}`);
  // 杀手①(round 半值向上):decay 夹具含 transient 类型(state)的衰减样例(整数输出,Python banker round 会分叉)。
  assert.ok(decay.effectiveConfidence.cases.some((c) => c.input.cog.contentType === 'state'), 'decay 夹具应含 state 衰减样例(round 半值边界)');
  // 杀手②(Math.imul 32位):fnv1a32 夹具含 uint32 输出,且 CJK token 在位。
  const fnvHan = he.fnv1a32.cases.find((c) => c.input === '饮');
  assert.ok(fnvHan && Number.isInteger(fnvHan.expected) && fnvHan.expected >= 0 && fnvHan.expected <= 0xffffffff, 'fnv1a32 应含 CJK token 的 uint32 输出(Math.imul parity 锚)');
  // 杀手③(config 单一源):config-constants.json 存在且带 baseByFormedBy。
  const cfg = a['config-constants.json'] as { consolidation: { baseByFormedBy: Record<string, number> } };
  assert.equal(cfg.consolidation.baseByFormedBy.stated, 600, 'config-constants 应携带 baseByFormedBy 数值(Python 单一源)');
});
