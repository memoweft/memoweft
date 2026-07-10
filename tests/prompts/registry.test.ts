/**
 * §15.3 提示词集中版本化 —— 注册表契约 + 哈希快照闸门（红测试先行）。
 *
 * 为什么存在:
 *   8 条受治理提示词从各模块散落的 export 常量收敛到 src/prompts/ 下的 registry。
 *   这份测试就是 npm test 里的那道闸门:提示词内容一旦被改、而 version 没 bump,
 *   哈希快照会立刻变红——迫使改动者走「bump version + 重跑 bench + 附分数对比」的流程。
 *
 * 断言四组(对应 PROJECT_PLAN.md §15.3 / DECISIONS D-0009):
 *   1. 注册表契约:PROMPT_REGISTRY 恰好含 8 个 id、唯一、按 id 字母序排列。
 *   2. 版本格式:每条 version 形如 vN;system.zh/en 均为非空字符串;consolidate=v2、其余 v1;
 *      promptVersions()(供 bench 记元数据)逐一对得上。
 *   3. 哈希快照:读 tests/prompts/prompt-hashes.snapshot,对 registry 现算 sha256 逐行比对。
 *      快照此刻可能还不存在 → 优雅地红(提示先跑 `npm run prompts:update`),不抛看不懂的 ENOENT。
 *   4. 认知纪律哨兵:consolidate.zh 必须仍含「只标冲突」「support_evidence_ids」;
 *      attribute.zh 必须仍含「绝不下定论」(铁律 3:纯位置迁移,一个字不许改)。
 *
 * 走 tests/**\/*.test.ts glob(离线、进 npm test 护栏)。写法与中文注释风格抄
 * tests/api/api-freeze.test.ts 与 tests/language-switch.test.ts。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { PROMPT_REGISTRY, promptVersions } from '../../src/prompts/registry.ts';
import type { VersionedPrompt } from '../../src/prompts/types.ts';

/** 哈希快照(与本测试同目录);缺失时测试优雅红,不抛 ENOENT。 */
const SNAPSHOT_URL = new URL('./prompt-hashes.snapshot', import.meta.url);

/** 与 scripts/prompt-hashes.mjs 同法:sha256(原始 system 字符串, utf8) → hex(已核对能命中 §15.3 的 16 个哈希)。 */
const sha256 = (s: string): string => createHash('sha256').update(s, 'utf8').digest('hex');

/** §15.3 定死的 8 条 id。 */
const EXPECTED_IDS = [
  'consolidate',
  'distill',
  'attribute',
  'trends',
  'proposeAsk',
  'revisitConflicts',
  'reply',
  'jsonRepairNudge',
];

/** 期望版本(D-0009:consolidate 已 bump 到 v2;其余 v1)。 */
const EXPECTED_VERSIONS: Record<string, string> = {
  consolidate: 'v2',
  distill: 'v1',
  attribute: 'v1',
  trends: 'v1',
  proposeAsk: 'v1',
  revisitConflicts: 'v1',
  reply: 'v1',
  jsonRepairNudge: 'v1',
};

/** 按 id 取一条(找不到即红,让「缺 id」的失败信息清楚,而非后续 undefined 取值报错)。 */
function byId(id: string): VersionedPrompt {
  const p = PROMPT_REGISTRY.find((x) => x.id === id);
  if (!p) assert.fail(`PROMPT_REGISTRY 缺少 id='${id}'`);
  return p;
}

test('§15.3-1 注册表契约:恰好 8 条 id、唯一、按 id 字母序', () => {
  const ids = PROMPT_REGISTRY.map((p) => p.id);
  assert.equal(ids.length, 8, '提示词注册表应恰好 8 条');
  assert.equal(new Set(ids).size, ids.length, 'id 必须唯一(不得重复注册)');
  assert.deepEqual(new Set(ids), new Set(EXPECTED_IDS), '注册表 id 集合应与 §15.3 布局一致');
  assert.deepEqual(ids, [...ids].sort(), 'PROMPT_REGISTRY 必须按 id 字母序排列(便于 diff 时肉眼比对)');
});

test('§15.3-2 版本格式:vN、双语非空、consolidate=v2 其余 v1', () => {
  for (const p of PROMPT_REGISTRY) {
    assert.match(p.version, /^v\d+$/, `${p.id}.version 应形如 vN,实得 '${p.version}'`);
    assert.equal(typeof p.text.zh, 'string', `${p.id}.text.zh 应为字符串`);
    assert.equal(typeof p.text.en, 'string', `${p.id}.text.en 应为字符串`);
    assert.ok(p.text.zh.length > 0, `${p.id}.text.zh 不能为空`);
    assert.ok(p.text.en.length > 0, `${p.id}.text.en 不能为空`);
    assert.equal(p.version, EXPECTED_VERSIONS[p.id], `${p.id} 版本应为 ${EXPECTED_VERSIONS[p.id]}`);
  }
  // promptVersions() 是 bench 评测器记进报告元数据用的 id→version 映射,须与注册表逐一对得上。
  assert.deepEqual(promptVersions(), EXPECTED_VERSIONS, 'promptVersions() 应等于期望的 id→version 映射');
});

test('§15.3-3 哈希快照:registry 现算 sha256 与快照逐行一致', () => {
  // 现算期望行(按 id 字母序,格式 = `<id>@<version>  zh=sha256:<hex>  en=sha256:<hex>`)。
  const expected = [...PROMPT_REGISTRY]
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    .map((p) => `${p.id}@${p.version}  zh=sha256:${sha256(p.text.zh)}  en=sha256:${sha256(p.text.en)}`);

  let raw: string;
  try {
    raw = readFileSync(SNAPSHOT_URL, 'utf8');
  } catch {
    // 快照文件此刻不存在 → 给出「先生成」的可执行指引,而不是抛 ENOENT。
    assert.fail(
      '缺少哈希快照 tests/prompts/prompt-hashes.snapshot。\n' +
        '首次生成:先跑 `npm run prompts:update`,再重跑本测试——它是 §15.3 的哈希闸门。',
    );
    return;
  }

  // 只取哈希行(容忍脚本可能写的头部注释/空行),按字母序比对。
  const HASH_LINE = /^\S+@v\d+\s+zh=sha256:[0-9a-f]{64}\s+en=sha256:[0-9a-f]{64}$/;
  const actual = raw
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => HASH_LINE.test(l))
    .sort();

  if (actual.join('\n') === expected.join('\n')) return;

  // 定位首个差异行,便于阅读。
  const n = Math.max(actual.length, expected.length);
  let diff = '(无法定位具体差异行)';
  for (let i = 0; i < n; i++) {
    if (actual[i] !== expected[i]) {
      diff = `第 ${i + 1} 行差异:\n  - 快照: ${actual[i] ?? '(无此行)'}\n  + 现算: ${expected[i] ?? '(无此行)'}`;
      break;
    }
  }

  assert.fail(
    '提示词内容变了(registry 现算哈希 ≠ 快照)。\n' +
      diff +
      '\n\n若是【有意】改动:bump version → 跑 `npm run prompts:update` → 重跑 `bench/eval-consolidation.mjs` 全量 → ' +
      '在 commit 正文附前后分数对比(§15.3 / D-0009)。\n' +
      '若是【无意】破坏:回滚改动,使提示词字符串逐字节恢复原样。',
  );
});

test('§15.3-4 认知纪律哨兵:纯位置迁移、一字不改(铁律 3)', () => {
  const consolidate = byId('consolidate');
  assert.ok(consolidate.text.zh.includes('只标冲突'), 'consolidate.zh 应仍含「只标冲突」(冲突只暴露不裁决的纪律)');
  assert.ok(
    consolidate.text.zh.includes('support_evidence_ids'),
    'consolidate.zh 应仍含「support_evidence_ids」(证据级溯源纪律)',
  );

  const attribute = byId('attribute');
  assert.ok(attribute.text.zh.includes('绝不下定论'), 'attribute.zh 应仍含「绝不下定论」(假设只低声说的纪律)');
});
