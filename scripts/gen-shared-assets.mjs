/**
 * 语言中立共享资产生成器（1.3 Python 移植 · Phase 0 · D-0042）。
 *
 * 目的:把「跨语言必须同源」的东西从 TS 里【导出成语言中立 JSON】,让 Python 移植版直接载入同一份、
 *   而不是手抄(手抄必漂,见 D-0042)。**TS 仍是唯一真相源**——本脚本 import 真 TS 函数/常量产出:
 *   - config-constants.json:纯逻辑读的数值常量(baseByFormedBy/阈值/半衰期/transientCap/CARRIER_RANK…)。
 *   - prompts.json:8 条受治理提示词的 {id,version,text:{zh,en}}(Python 载入后算 sha256 应对齐 prompt-hashes 快照)。
 *   - parity/*.json:纯确定性函数的【输入→期望输出】夹具,供 Python 逐位对拍(移植即验证)。
 *
 * 用法(镜像 api:update / prompts:update):
 *   node scripts/gen-shared-assets.mjs           # 生成/刷新 shared/ 下全部文件(= npm run shared:update)
 *   node scripts/gen-shared-assets.mjs --check    # 只比对不写,committed 与现生成不一致 exit 1(= npm run shared:check)
 * 守门测试 tests/shared/shared-assets.test.ts 直接 import { buildSharedAssets } 逐字比对(漂移即红)。
 *
 * 纪律:本脚本【只读 src / 只写 shared】,不改任何 TS 逻辑;纯追加,不触公共 API/schema。
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join, resolve } from 'node:path';

import { config } from '../src/config.ts';
import { computeConfidence, deriveCredStatus } from '../src/consolidation/confidence.ts';
import { deriveFormedBy } from '../src/consolidation/deriveFormedBy.ts';
import { decayFactor, halfLifeOf, effectiveConfidence } from '../src/background/decay.ts';
import { resolveEchoedId, MIN_ID_PREFIX } from '../src/llm/echoedId.ts';
import { fnv1a32, tokenize, HashEmbedder } from '../tests/retrieval/hashEmbedder.ts';
import { PROMPT_REGISTRY } from '../src/prompts/registry.ts';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const SHARED = join(ROOT, 'shared');

// ── 枚举取值(锚定 src 的类型 union;新增取值时须同步这里,守门测试仍会拿 committed 对现生成,故不会静默错) ──
const FORMED_BY = ['stated', 'observed', 'ruled', 'confirmed', 'inferred']; // cognition/model.ts:29
const CONTENT_TYPES = ['fact', 'preference', 'goal', 'project', 'state', 'trait', 'hypothesis', 'trend']; // cognition/model.ts:15-23
const SOURCE_KINDS = ['spoken', 'inferred', 'observed', 'tool']; // evidence/model.ts:12
const RESPONSE_ACTS = ['affirm', 'negate', 'select', 'elaborate', 'ask', 'none', 'other']; // interaction/model.ts:16
const PROPOSITION_ORIGINS = ['user_stated', 'assistant_proposed']; // interaction/model.ts:20

/** 稳定序列化:递归按键排序 → 跨机确定,便于 --check 逐字比对(同 api-snapshot 精神)。 */
export function stableStringify(value) {
  const path = new WeakSet(); // 当前 DFS 路径(检真环),递归后移除 → 允许共享引用(如复用的 whitelist 数组)。
  const norm = (v) => {
    if (v === null || typeof v !== 'object') return v;
    if (path.has(v)) throw new Error('circular');
    path.add(v);
    let out;
    if (Array.isArray(v)) out = v.map(norm);
    else {
      out = {};
      for (const k of Object.keys(v).sort()) out[k] = norm(v[k]);
    }
    path.delete(v);
    return out;
  };
  return JSON.stringify(norm(value), null, 2) + '\n';
}

// ── config 常量(纯逻辑读的数值;不含 env 相关的 language/identity) ──
function buildConfigConstants() {
  const c = config;
  return {
    _note: 'Language-neutral numeric constants read by the pure-logic layer. Source of truth = src/config.ts (+ CARRIER_RANK/hard cap noted). Regenerate via `npm run shared:update`.',
    consolidation: {
      baseByFormedBy: c.consolidation.baseByFormedBy,
      supportStep: c.consolidation.supportStep,
      supportCap: c.consolidation.supportCap,
      contradictPenalty: c.consolidation.contradictPenalty,
      minConfidence: c.consolidation.minConfidence,
      confidenceHardMax: 1000, // computeConfidence 里硬编码的上限(不在 config)——confidence.ts:30
      credThresholds: c.consolidation.credThresholds,
      transientTypes: c.consolidation.transientTypes,
      transientCap: c.consolidation.transientCap,
    },
    background: {
      halfLifeDays: c.background.halfLifeDays,
      expireAfterDays: c.background.expireAfterDays,
      trendWindowDays: c.background.trendWindowDays,
      trendMinCount: c.background.trendMinCount,
    },
    retrieval: {
      topK: c.retrieval.topK,
      minEffectiveConfidence: c.retrieval.minEffectiveConfidence,
      minSimilarity: c.retrieval.minSimilarity,
    },
    attribution: { hypothesisCap: c.attribution.hypothesisCap },
    asking: { confidenceBand: c.asking.confidenceBand, askableStatuses: c.asking.askableStatuses },
    // CARRIER_RANK 未从 deriveFormedBy.ts 导出(内部件),此处照抄并由 formed-by parity 夹具间接钉死其效果。
    carrierRank: { confirmed: 0, observed: 1, stated: 2 }, // deriveFormedBy.ts:62
    minIdPrefix: MIN_ID_PREFIX, // echoedId.ts:17
    dayMs: 86400000, // decay.ts:13
  };
}

// ── 提示词共享资产 ──
function buildPrompts() {
  return {
    _note: 'The 8 governed prompts (verbatim). Python loads this same file; per-lang sha256 must match tests/prompts/prompt-hashes.snapshot. Source of truth = src/prompts/registry.ts.',
    prompts: PROMPT_REGISTRY.map((p) => ({ id: p.id, version: p.version, text: { zh: p.text.zh, en: p.text.en } })),
  };
}

// ── parity 夹具:computeConfidence(全组合) ──
function parityConfidence() {
  const cases = [];
  for (const formedBy of FORMED_BY)
    for (const contentType of CONTENT_TYPES)
      for (let supportCount = 0; supportCount <= 7; supportCount++)
        for (let contradictCount = 0; contradictCount <= 3; contradictCount++) {
          const input = { formedBy, contentType, supportCount, contradictCount };
          cases.push({ input, expected: computeConfidence(input) });
        }
  return { fn: 'computeConfidence', note: 'formedBy×contentType×support(0..7)×contradict(0..3) 全组合', cases };
}

// ── parity 夹具:deriveCredStatus(阈值边界 ±1) ──
function parityCredStatus() {
  const confs = [0, 50, 100, 299, 300, 301, 499, 500, 501, 749, 750, 751, 1000];
  const types = ['fact', 'state', 'preference']; // state=transient,fact/preference=非
  const cases = [];
  for (const confidence of confs)
    for (const contradictCount of [0, 1])
      for (const contentType of types)
        cases.push({ input: { confidence, contradictCount, contentType }, expected: deriveCredStatus(confidence, contradictCount, contentType) });
  return { fn: 'deriveCredStatus', note: '阈值边界 ±1 × contradict(0,1) × {fact,state(transient),preference}', cases };
}

// ── parity 夹具:deriveFormedBy(deriveOne 单证据全分支 + 取最弱多证据) ──
function carrierInput(sourceKind, hasAi, resolution) {
  return { sourceKind, precedingAiContext: hasAi ? 'AI: do you like hiking?' : null, resolution };
}
function parityFormedBy() {
  const cases = [];
  // 单证据:非 spoken → observed(不看其余)
  for (const sk of ['observed', 'tool', 'inferred'])
    cases.push({ input: [carrierInput(sk, true, { responseAct: 'affirm', propositionOrigin: 'assistant_proposed' })], expected: deriveFormedBy([carrierInput(sk, true, { responseAct: 'affirm', propositionOrigin: 'assistant_proposed' })]) });
  // spoken × hasAi × resolution 组合
  for (const hasAi of [false, true]) {
    // resolution=null(兜底:hasAi?confirmed:stated)
    let inp = [carrierInput('spoken', hasAi, null)];
    cases.push({ input: inp, expected: deriveFormedBy(inp) });
    for (const po of PROPOSITION_ORIGINS)
      for (const ra of [...RESPONSE_ACTS, null]) {
        inp = [carrierInput('spoken', hasAi, { responseAct: ra, propositionOrigin: po })];
        cases.push({ input: inp, expected: deriveFormedBy(inp) });
      }
    // propositionOrigin=null 收敛(非法枚举兜底)
    inp = [carrierInput('spoken', hasAi, { responseAct: 'affirm', propositionOrigin: null })];
    cases.push({ input: inp, expected: deriveFormedBy(inp) });
  }
  // 多证据取最弱
  const multi = [
    [carrierInput('spoken', false, { responseAct: 'none', propositionOrigin: 'user_stated' }), carrierInput('spoken', true, { responseAct: 'affirm', propositionOrigin: 'assistant_proposed' })], // stated + confirmed → confirmed
    [carrierInput('observed', false, null), carrierInput('spoken', false, { responseAct: 'none', propositionOrigin: 'user_stated' })], // observed + stated → observed
    [carrierInput('spoken', true, { responseAct: 'negate', propositionOrigin: 'assistant_proposed' }), carrierInput('spoken', false, { responseAct: 'none', propositionOrigin: 'user_stated' })], // stated + stated → stated
    [], // 空集 → null
  ];
  for (const input of multi) cases.push({ input, expected: deriveFormedBy(input) });
  return { fn: 'deriveFormedBy', note: 'deriveOne 单证据全分支 + 取最弱多证据(空集→null)', cases };
}

// ── parity 夹具:decay(decayFactor 原始 double + effectiveConfidence 整数;含半值向上边界) ──
function parityDecay() {
  const DAY = 86400000;
  const factorCases = [];
  for (const [hl, ageDays] of [[0, 5], [-1, 5], [1.5, 0], [1.5, 1.5], [1.5, 3], [1.5, 0.75], [7, 7], [14, 30], [60, 60], [2, 1]]) {
    factorCases.push({ input: { halfLifeDays: hl, ageMs: ageDays * DAY }, expected: decayFactor(hl, ageDays * DAY) });
  }
  // effectiveConfidence:固定 updatedAt,now 按 ageDays 偏移;密集网格逼出 Math.round 半值边界。
  const updatedAt = '2026-01-01T00:00:00.000Z';
  const base = new Date(updatedAt).getTime();
  const effCases = [];
  for (const contentType of ['state', 'hypothesis', 'goal', 'trend', 'trait', 'fact']) // fact 不衰减=对照
    for (const confidence of [50, 100, 137, 300, 481, 600, 999])
      for (const ageDays of [0, 0.5, 1, 1.5, 2, 3, 5, 7, 14, 30, 60, 100]) {
        const now = new Date(base + ageDays * DAY).toISOString();
        const cog = { confidence, contentType, updatedAt };
        effCases.push({ input: { cog, now }, expected: effectiveConfidence(cog, new Date(now)) });
      }
  return {
    decayFactor: { fn: 'decayFactor', note: 'halfLife≤0→1、精确半衰期→0.5、2×→0.25 等(expected 为原始 double,Python 应 IEEE754 逐位一致)', cases: factorCases },
    effectiveConfidence: { fn: 'effectiveConfidence', note: 'contentType×confidence×ageDays 网格;expected 为整数,⚠ Math.round 半值向 +∞,Python 须 floor(x+0.5) 非 banker round', cases: effCases },
  };
}

// ── parity 夹具:hashEmbedder(fnv1a32 精确 uint32 + tokenize + embed 向量) ──
function parityHashEmbedder() {
  const tokens = ['hello', 'rust', 'coffee', '饮', '食', '饮食', '爬', '山', '爬山', '123', 'a', ''];
  const fnvCases = tokens.map((t) => ({ input: t, expected: fnv1a32(t) }));
  const texts = ['I like hiking', '我喜欢爬山', '喜欢喝咖啡 and coffee', 'Rust 2026', '   ', '饮食偏好'];
  const tokenizeCases = texts.map((t) => ({ input: t, expected: tokenize(t) }));
  // embed 向量在 buildSharedAssets 里 async 生成(HashEmbedder.embed 内部纯同步、包成 resolved Promise)。
  return { DIM: 32, embedTexts: ['I like hiking', '我喜欢爬山', ''], fnvCases, tokenizeCases };
}

// ── parity 夹具:resolveEchoedId(三级解析全分支 + 护栏) ──
function parityEchoedId() {
  const wl = ['afb63041-b678-4ebc', 'f51446dc-f70c-4ea5', '9398bd80-ffd8-42cb'];
  const tag = [['e1', 'afb63041-b678-4ebc'], ['e2', 'f51446dc-f70c-4ea5']];
  const raws = [
    'e1', // ① 标号命中
    'afb63041-b678-4ebc', // ② 精确
    'afb63041', // ③ 唯一前缀(≥8)
    'ev-afb63041', // ③ 剥 ev- 前缀后唯一前缀
    'cog-9398bd80', // ③ 剥 cog- 前缀
    '1', // 过短(<8)→ null
    'ev-1', // 剥前缀后过短 → null
    'zzzzzzzz', // 捏造(无前缀命中)→ null
    'f', // 歧义?单字符过短 → null
    undefined, // → null
    '', // → null
  ];
  const cases = raws.map((raw) => ({
    input: { raw: raw ?? null, whitelist: wl, tagMap: tag },
    expected: resolveEchoedId(raw, new Set(wl), new Map(tag)),
  }));
  // 歧义前缀:两个同前缀 id + 一个歧义查询 → null
  const wl2 = ['abcd1234ef', 'abcd1234gh'];
  cases.push({ input: { raw: 'abcd1234', whitelist: wl2, tagMap: [] }, expected: resolveEchoedId('abcd1234', new Set(wl2), new Map()) });
  return { fn: 'resolveEchoedId', note: '标号→精确→唯一前缀三级 + 护栏(过短/捏造/歧义→null)', cases };
}

/** 生成全部共享资产(纯计算,async 仅因 embed)。返回 { path → object }。 */
export async function buildSharedAssets() {
  const he = parityHashEmbedder();
  const emb = new HashEmbedder(he.DIM);
  const vecs = await emb.embed(he.embedTexts);
  const embedCases = he.embedTexts.map((text, i) => ({ input: { text, dim: he.DIM }, expected: vecs[i] }));

  return {
    'config-constants.json': buildConfigConstants(),
    'prompts.json': buildPrompts(),
    'parity/confidence.json': parityConfidence(),
    'parity/cred-status.json': parityCredStatus(),
    'parity/formed-by.json': parityFormedBy(),
    'parity/decay.json': parityDecay(),
    'parity/hash-embedder.json': {
      fnv1a32: { fn: 'fnv1a32', note: 'FNV-1a 32位(Math.imul 32位有符号乘 + >>>0 无符号);expected 为 uint32,Python 须掩码复刻(numpy int32 或 &0xFFFFFFFF)', cases: he.fnvCases },
      tokenize: { fn: 'tokenize', note: 'lowercase + \\p{L}+/\\p{N}+ 连续段,汉字段拆单字+bigram', cases: he.tokenizeCases },
      embed: { fn: 'HashEmbedder.embed', dim: he.DIM, note: 'L2 归一化词袋向量(空文本→全零);expected 为 dim 维 double 数组', cases: embedCases },
    },
    'parity/echoed-id.json': parityEchoedId(),
  };
}

function writeAll(assets) {
  mkdirSync(join(SHARED, 'parity'), { recursive: true });
  for (const [rel, obj] of Object.entries(assets)) {
    writeFileSync(join(SHARED, rel), stableStringify(obj));
  }
}

function checkAll(assets) {
  let drift = 0;
  for (const [rel, obj] of Object.entries(assets)) {
    const p = join(SHARED, rel);
    const fresh = stableStringify(obj);
    const committed = existsSync(p) ? readFileSync(p, 'utf8') : '';
    if (fresh !== committed) {
      console.error(`DRIFT: shared/${rel} 与 TS 源不一致(运行 npm run shared:update 刷新)`);
      drift++;
    }
  }
  return drift;
}

// 仅作为主程序运行时读写文件;被 import(守门测试)时只暴露 buildSharedAssets。
if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  const assets = await buildSharedAssets();
  if (process.argv.includes('--check')) {
    const drift = checkAll(assets);
    if (drift > 0) process.exit(1);
    console.log('shared/ 资产与 TS 源一致。');
  } else {
    writeAll(assets);
    console.log('shared/ 资产已刷新(config-constants + prompts + 6 份 parity 夹具)。');
  }
}
