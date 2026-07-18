/**
 * 语言中立共享资产生成器（为 Python 移植版生成共享契约资产）。
 *
 * 目的:把「跨语言必须同源」的东西从 TS 里【导出成语言中立 JSON】,让 Python 移植版直接载入同一份、
 *   而不是维护重复副本。**TS 仍是唯一来源**——本脚本 import TS 函数/常量产出：
 *   - config-constants.json:纯逻辑读的数值常量(baseByFormedBy/阈值/半衰期/transientCap/CARRIER_RANK…)。
 *   - prompts.json:8 条受治理提示词的 {id,version,text:{zh,en}}(Python 载入后算 sha256 应对齐 prompt-hashes 快照)。
 *   - parity/*.json:纯确定性函数的【输入→期望输出】夹具,供 Python 做逐值一致性验证。
 *
 * 用法(镜像 api:update / prompts:update):
 *   node scripts/gen-shared-assets.mjs           # 生成/刷新 shared/ 和 Python 包内镜像(= npm run shared:update)
 *   node scripts/gen-shared-assets.mjs --check    # 比对两处镜像,任一漂移即 exit 1(= npm run shared:check)
 * tests/shared/shared-assets.test.ts 直接 import { buildSharedAssets } 检查生成资产是否漂移。
 *
 * 纪律:本脚本【只读 src / 只写 shared 与 py 包内生成镜像】,不改任何 TS 逻辑;纯追加,不触公共 API/schema。
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync, rmSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join, relative, resolve } from 'node:path';

import { config } from '../src/config.ts';
import { computeConfidence, deriveCredStatus } from '../src/consolidation/confidence.ts';
import { deriveFormedBy } from '../src/consolidation/deriveFormedBy.ts';
import { decayFactor, halfLifeOf, effectiveConfidence } from '../src/background/decay.ts';
import { resolveEchoedId, MIN_ID_PREFIX } from '../src/llm/echoedId.ts';
import { fnv1a32, tokenize, HashEmbedder } from '../tests/retrieval/hashEmbedder.ts';
import { PROMPT_REGISTRY } from '../src/prompts/registry.ts';
import { openStores } from '../src/store/openStores.ts';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { sourceLabel, aiContextSuffix } from '../src/evidence/sourceLabel.ts';
import { hashContext } from '../src/interaction/interactionContextStore.ts';
import { expire } from '../src/background/expire.ts';
import { stripReasoning, readReplyText } from '../src/llm/client.ts';
import { extractJsonObject, parseJsonObject } from '../src/llm/jsonRepair.ts';
import { distill } from '../src/distillation/distill.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import { attribute } from '../src/attribution/attribute.ts';
import { aggregateTrends } from '../src/background/trends.ts';
import { proposeAsk } from '../src/asking/proposeAsk.ts';
import { revisitConflicts } from '../src/asking/revisitConflicts.ts';
import { updateProfile } from '../src/consolidation/updateProfile.ts';
import { importBundle } from '../src/portable/importBundle.ts';
// bench 判定纯函数：eval-consolidation.mjs 已加 main 守卫，import 不会触发真实评测。
import { checkStructural, parseYesNo } from '../bench/eval-consolidation.mjs';
import { validateBundle } from '../src/portable/validateBundle.ts';
import { BUNDLE_FORMAT, BUNDLE_SCHEMA_VERSION } from '../src/portable/model.ts';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const SHARED = join(ROOT, 'shared');
const PYTHON_SHARED_DATA = join(ROOT, 'py', 'src', 'memoweft', '_shared_data');

// ── 枚举取值（锚定 src 的类型 union；新增取值时须同步，生成资产检查会检测差异）──
const FORMED_BY = ['stated', 'observed', 'ruled', 'confirmed', 'inferred']; // cognition/model.ts:29
const CONTENT_TYPES = [
  'fact',
  'preference',
  'goal',
  'project',
  'state',
  'trait',
  'hypothesis',
  'trend',
]; // cognition/model.ts:15-23
const SOURCE_KINDS = ['spoken', 'inferred', 'observed', 'tool']; // evidence/model.ts:12
const RESPONSE_ACTS = ['affirm', 'negate', 'select', 'elaborate', 'ask', 'none', 'other']; // interaction/model.ts:16
const PROPOSITION_ORIGINS = ['user_stated', 'assistant_proposed']; // interaction/model.ts:20

/** 稳定序列化:递归按键排序 → 跨机确定,便于 --check 逐字比对(同 api-snapshot 精神)。 */
export function stableStringify(value) {
  const path = new WeakSet(); // 仅跟踪当前 DFS 路径以检测循环引用；递归返回后移除，允许共享引用。
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
    _note:
      'Language-neutral constants read by the pure-logic and storage layers. Source of truth = src/config.ts (+ CARRIER_RANK/hard cap noted). Regenerate via `npm run shared:update`.',
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
    // attribution 全字段( 纳入:原只有 hypothesisCap,windowHours/maxPhenomenaPerRun/maxCausesPerHypothesis/minPhenomenonSupport 是归因规则门,跨语言须同源)
    attribution: c.attribution,
    // asking 全字段( 纳入:原缺 maxAsks——候选 slice 依赖它,跨语言须同源)
    asking: c.asking,
    // CARRIER_RANK 未从 deriveFormedBy.ts 导出；这里保持等价副本，并由 formed-by parity 夹具验证行为一致。
    carrierRank: { confirmed: 0, observed: 1, stated: 2 }, // deriveFormedBy.ts:62
    minIdPrefix: MIN_ID_PREFIX, // echoedId.ts:17
    dayMs: 86400000, // decay.ts:13
    // ── 身份默认(perceive/ingest 缺省 subjectId/hostId 用;identity 非 env 依赖,可纳入)—— 纳入 ──
    identity: c.identity, // config.ts:100(默认 owner/local)
    // ── 证据授权默认(evidence.put 按 sourceKind 分流补默认；跨语言授权约束常量，storage 层读取)——纳入 ──
    privacyMode: c.privacyMode, // config.ts:103(cloudReadDefault = !privacyMode)
    evidenceDefaults: c.evidenceDefaults, // config.ts:104(spoken/inferred 通用默认;无 allowCloudRead → 走 cloudReadDefault)
    observedDefaults: c.observedDefaults, // config.ts:105(行为观察保守:local✓/cloud✗/infer✓)
    toolDefaults: c.toolDefaults, // 工具结果采用与 observed 证据一致的保守默认值。
  };
}

// ── 提示词共享资产 ──
function buildPrompts() {
  return {
    _note:
      'The 8 governed prompts (verbatim). Python loads this same file; per-lang sha256 must match tests/prompts/prompt-hashes.snapshot. Source of truth = src/prompts/registry.ts.',
    prompts: PROMPT_REGISTRY.map((p) => ({
      id: p.id,
      version: p.version,
      text: { zh: p.text.zh, en: p.text.en },
    })),
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
  return {
    fn: 'computeConfidence',
    note: 'formedBy×contentType×support(0..7)×contradict(0..3) 全组合',
    cases,
  };
}

// ── parity 夹具:deriveCredStatus(阈值边界 ±1) ──
function parityCredStatus() {
  const confs = [0, 50, 100, 299, 300, 301, 499, 500, 501, 749, 750, 751, 1000];
  const types = ['fact', 'state', 'preference']; // state=transient,fact/preference=非
  const cases = [];
  for (const confidence of confs)
    for (const contradictCount of [0, 1])
      for (const contentType of types)
        cases.push({
          input: { confidence, contradictCount, contentType },
          expected: deriveCredStatus(confidence, contradictCount, contentType),
        });
  return {
    fn: 'deriveCredStatus',
    note: '阈值边界 ±1 × contradict(0,1) × {fact,state(transient),preference}',
    cases,
  };
}

// ── parity 夹具:deriveFormedBy(deriveOne 单证据全分支 + 取最弱多证据) ──
function carrierInput(sourceKind, hasAi, resolution) {
  return { sourceKind, precedingAiContext: hasAi ? 'AI: do you like hiking?' : null, resolution };
}
function parityFormedBy() {
  const cases = [];
  // 单证据:非 spoken → observed(不看其余)
  for (const sk of ['observed', 'tool', 'inferred'])
    cases.push({
      input: [
        carrierInput(sk, true, { responseAct: 'affirm', propositionOrigin: 'assistant_proposed' }),
      ],
      expected: deriveFormedBy([
        carrierInput(sk, true, { responseAct: 'affirm', propositionOrigin: 'assistant_proposed' }),
      ]),
    });
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
    [
      carrierInput('spoken', false, { responseAct: 'none', propositionOrigin: 'user_stated' }),
      carrierInput('spoken', true, {
        responseAct: 'affirm',
        propositionOrigin: 'assistant_proposed',
      }),
    ], // stated + confirmed → confirmed
    [
      carrierInput('observed', false, null),
      carrierInput('spoken', false, { responseAct: 'none', propositionOrigin: 'user_stated' }),
    ], // observed + stated → observed
    [
      carrierInput('spoken', true, {
        responseAct: 'negate',
        propositionOrigin: 'assistant_proposed',
      }),
      carrierInput('spoken', false, { responseAct: 'none', propositionOrigin: 'user_stated' }),
    ], // stated + stated → stated
    [], // 空集 → null
  ];
  for (const input of multi) cases.push({ input, expected: deriveFormedBy(input) });
  return { fn: 'deriveFormedBy', note: 'deriveOne 单证据全分支 + 取最弱多证据(空集→null)', cases };
}

// ── parity 夹具:decay(decayFactor 原始 double + effectiveConfidence 整数;含半值向上边界) ──
function parityDecay() {
  const DAY = 86400000;
  const factorCases = [];
  for (const [hl, ageDays] of [
    [0, 5],
    [-1, 5],
    [1.5, 0],
    [1.5, 1.5],
    [1.5, 3],
    [1.5, 0.75],
    [7, 7],
    [14, 30],
    [60, 60],
    [2, 1],
  ]) {
    factorCases.push({
      input: { halfLifeDays: hl, ageMs: ageDays * DAY },
      expected: decayFactor(hl, ageDays * DAY),
    });
  }
  // effectiveConfidence:固定 updatedAt,now 按 ageDays 偏移;密集网格逼出 Math.round 半值边界。
  const updatedAt = '2026-01-01T00:00:00.000Z';
  const base = new Date(updatedAt).getTime();
  const effCases = [];
  for (const contentType of ['state', 'hypothesis', 'goal', 'trend', 'trait', 'fact'])
    // fact 不衰减=对照
    for (const confidence of [50, 100, 137, 300, 481, 600, 999])
      for (const ageDays of [0, 0.5, 1, 1.5, 2, 3, 5, 7, 14, 30, 60, 100]) {
        const now = new Date(base + ageDays * DAY).toISOString();
        const cog = { confidence, contentType, updatedAt };
        effCases.push({ input: { cog, now }, expected: effectiveConfidence(cog, new Date(now)) });
      }
  return {
    decayFactor: {
      fn: 'decayFactor',
      note: 'halfLife≤0→1、精确半衰期→0.5、2×→0.25 等(expected 为 IEEE 754 double；Python 按数值 exact parity 断言，不使用 tolerance)',
      cases: factorCases,
    },
    effectiveConfidence: {
      fn: 'effectiveConfidence',
      note: 'contentType×confidence×ageDays 网格;expected 为整数,⚠ Math.round 半值向 +∞,Python 须 floor(x+0.5) 非 banker round',
      cases: effCases,
    },
  };
}

// ── parity 夹具:hashEmbedder(fnv1a32 精确 uint32 + tokenize + embed 向量) ──
function parityHashEmbedder() {
  const tokens = [
    'hello',
    'rust',
    'coffee',
    '饮',
    '食',
    '饮食',
    '爬',
    '山',
    '爬山',
    '123',
    'a',
    '',
  ];
  const fnvCases = tokens.map((t) => ({ input: t, expected: fnv1a32(t) }));
  const texts = [
    'I like hiking',
    '我喜欢爬山',
    '喜欢喝咖啡 and coffee',
    'Rust 2026',
    '   ',
    '饮食偏好',
  ];
  const tokenizeCases = texts.map((t) => ({ input: t, expected: tokenize(t) }));
  // embed 向量在 buildSharedAssets 里 async 生成(HashEmbedder.embed 内部纯同步、包成 resolved Promise)。
  return { DIM: 32, embedTexts: ['I like hiking', '我喜欢爬山', ''], fnvCases, tokenizeCases };
}

// ── parity 夹具：resolveEchoedId（三级解析与无效输入分支）──
function parityEchoedId() {
  const wl = ['afb63041-b678-4ebc', 'f51446dc-f70c-4ea5', '9398bd80-ffd8-42cb'];
  const tag = [
    ['e1', 'afb63041-b678-4ebc'],
    ['e2', 'f51446dc-f70c-4ea5'],
  ];
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
  cases.push({
    input: { raw: 'abcd1234', whitelist: wl2, tagMap: [] },
    expected: resolveEchoedId('abcd1234', new Set(wl2), new Map()),
  });
  return {
    fn: 'resolveEchoedId',
    note: '标号→精确→唯一前缀三级解析；过短/捏造/歧义输入返回 null',
    cases,
  };
}

// ── parity 夹具:evidence.put 授权分流（调用 TS SqliteEvidenceStore.put；期望值仅保留最终授权位）──
//   验证「按 sourceKind 补保守默认 + 显式值优先 + cloudReadDefault 跟随 privacyMode」的跨语言一致性。
function parityEvidenceAuth() {
  const FIXED = () => new Date('2026-01-01T00:00:00.000Z'); // 只为确定,授权不吃时间
  const explicits = [
    {},
    { allowLocalRead: true },
    { allowLocalRead: false },
    { allowCloudRead: true },
    { allowCloudRead: false },
    { allowInference: true },
    { allowInference: false },
    { allowLocalRead: false, allowCloudRead: true, allowInference: false },
  ];
  const cases = [];
  for (const cfg of [config, { ...config, privacyMode: true }]) {
    const store = new SqliteEvidenceStore(':memory:', cfg, FIXED);
    let n = 0;
    for (const sourceKind of SOURCE_KINDS)
      for (const explicit of explicits) {
        const e = store.put({
          subjectId: 'owner',
          sourceKind,
          hostId: 'local',
          rawContent: `x${n++}`,
          ...explicit,
        });
        cases.push({
          input: { privacyMode: cfg.privacyMode, sourceKind, explicit },
          expected: {
            allowLocalRead: e.allowLocalRead,
            allowCloudRead: e.allowCloudRead,
            allowInference: e.allowInference,
          },
        });
      }
    store.close();
  }
  return {
    fn: 'EvidenceStore.put 授权分流',
    note: 'sourceKind × 显式授权 × privacyMode → 最终三位授权(observed/tool 保守 local✓/cloud✗/infer✓;spoken/inferred 走 evidenceDefaults + cloudReadDefault=!privacyMode;显式永远优先)',
    cases,
  };
}

// ── parity 夹具:cognition all/active 排序（调用 TS SqliteCognitionStore.insert 并记录稳定 id 顺序）──
//   验证 ORDER BY confidence DESC, created_at ASC，并确保 active 排除 invalid/archived。
function parityCognitionOrder() {
  const mk = (id, confidence, createdAt, extra = {}) => ({
    id,
    subjectId: 'owner',
    content: `内容 ${id}`,
    contentType: 'preference',
    formedBy: 'stated',
    confidence,
    credStatus: 'limited',
    scope: null,
    validAt: null,
    invalidAt: null,
    askedAt: null,
    archivedAt: null,
    mutedAt: null,
    createdAt,
    updatedAt: createdAt,
    ...extra,
  });
  const store = new SqliteCognitionStore(':memory:');
  const cogs = [
    mk('c1', 600, '2026-01-01T00:00:01.000Z'),
    mk('c2', 600, '2026-01-01T00:00:00.000Z'), // 同分、created_at 更早 → 排 c1 之前
    mk('c3', 800, '2026-01-01T00:00:05.000Z'), // 最高分 → 最前
    mk('c4', 300, '2026-01-01T00:00:00.000Z', { invalidAt: '2026-01-02T00:00:00.000Z' }), // 失效 → active 排除
    mk('c5', 500, '2026-01-01T00:00:00.000Z', { archivedAt: '2026-01-02T00:00:00.000Z' }), // 归档 → active 排除
  ];
  for (const c of cogs) store.insert(c, []);
  const all = store.all('owner').map((c) => c.id);
  const active = store.active('owner').map((c) => c.id);
  store.close();
  return {
    note: 'insert 固定认知集 → all(全含)/ active(排除 invalid/archived)的 id 序 golden;排序 ORDER BY confidence DESC, created_at ASC',
    all,
    active,
  };
}

// ── parity 夹具:sourceLabel + aiContextSuffix（验证 TS 的 trim、UTF-16 slice 与全角括号字节语义）──
function paritySourceLabel() {
  const langs = ['zh', 'en'];
  const labelCases = [];
  for (const sk of SOURCE_KINDS)
    for (const lang of langs)
      labelCases.push({ input: { sourceKind: sk, lang }, expected: sourceLabel(sk, lang) });
  const texts = [
    null,
    '',
    '   ',
    '﻿去BOM﻿', // js trim 去 U+FEFF(py str.strip 不去)
    '普通一句 AI 上文',
    '爬'.repeat(300), // > 240 → 截断(BMP,单 code unit)
    '爬'.repeat(240), // 恰好 240,不截断
    'emoji 😀😀 短句', // 含 astral(每个 length=2),但短、不截断:验 length 计算不误伤
    '  前后空白  ',
  ];
  const suffixCases = [];
  for (const text of texts)
    for (const lang of langs)
      suffixCases.push({ input: { text, lang }, expected: aiContextSuffix(text, lang) });
  return {
    sourceLabel: {
      fn: 'sourceLabel',
      note: '来源前缀(含尾随空格);未知退回 spoken',
      cases: labelCases,
    },
    aiContextSuffix: {
      fn: 'aiContextSuffix',
      note: 'js-trim(去 BOM 等)+ UTF-16 slice(前 240 code unit)+ 全角括号 ⟨⟩;空/纯空白→""',
      max: 240,
      cases: suffixCases,
    },
  };
}

// ── parity 夹具:hashContext（sha256 over JSON.stringify(context)，验证 JSON 字节与哈希跨语言一致性）──
function parityContextHash() {
  const contexts = [
    [],
    [{ role: 'user', content: 'hi' }],
    [
      { role: 'assistant', content: '你喜欢爬山吧?' },
      { role: 'user', content: '是的' },
    ],
    [{ role: 'tool', content: '{"result": 42}' }], // 内含 JSON 字符,验转义
    [{ role: 'user', content: 'emoji 😀 和中文' }],
    [{ role: 'user', content: '含"引号"和\\反斜杠\n换行' }], // 验 JSON.stringify 转义
  ];
  return {
    fn: 'hashContext',
    note: 'sha256(JSON.stringify(context));Python 用 json.dumps(ensure_ascii=False, separators=(",",":")).encode("utf-8") 对齐字节',
    cases: contexts.map((context) => ({ input: context, expected: hashContext(context) })),
  };
}

// ── parity 夹具:expire（调用 TS expire 与 SqliteCognitionStore；固定时间和认知集后记录过期结果）──
function parityExpire() {
  const store = new SqliteCognitionStore(':memory:');
  const now = new Date('2026-02-01T00:00:00.000Z');
  const daysAgo = (d) => new Date(now.getTime() - d * 86400000).toISOString();
  const mk = (id, contentType, updatedAt, extra = {}) => ({
    id,
    subjectId: 'owner',
    content: `内容 ${id}`,
    contentType,
    formedBy: 'stated',
    confidence: 300,
    credStatus: 'low',
    scope: null,
    validAt: null,
    invalidAt: null,
    askedAt: null,
    archivedAt: null,
    mutedAt: null,
    createdAt: updatedAt,
    updatedAt,
    ...extra,
  });
  const cogs = [
    mk('s-fresh', 'state', daysAgo(3)), // 3 < 7 → 不过期
    mk('s-boundary', 'state', daysAgo(7)), // 7 > 7 false(严格)→ 不过期
    mk('s-old', 'state', daysAgo(8)), // 8 > 7 → 过期
    mk('h-boundary', 'hypothesis', daysAgo(14)), // 14 > 14 false → 不过期
    mk('h-old', 'hypothesis', daysAgo(15)), // 15 > 14 → 过期
    mk('t-old', 'trend', daysAgo(31)), // 31 > 30 → 过期
    mk('f-old', 'fact', daysAgo(100)), // 不在名单 → 永不过期
    mk('p-old', 'preference', daysAgo(100)), // 不在名单 → 永不过期
    mk('s-arch', 'state', daysAgo(30), { archivedAt: daysAgo(1) }), // 归档 → active 排除,不碰
  ];
  for (const c of cogs) store.insert(c, []);
  const result = expire('owner', { cognitionStore: store }, now);
  const invalidIds = store
    .all('owner')
    .filter((c) => c.invalidAt != null)
    .map((c) => c.id)
    .sort();
  store.close();
  return {
    note: 'expire 纯规则：时效类(state7/hypothesis14/trend30)超阈标 invalid(严格 >)、fact/preference 不列永不过期、归档 active 排除不处理',
    now: now.toISOString(),
    expired: result.expired,
    invalidIds,
  };
}

// ── parity 夹具:LLM 文本处理（调用 TS stripReasoning 与 readReplyText）──
function parityLlmText() {
  const stripCases = [
    'no think here',
    '<think>思考过程</think>答案',
    '答案前<think>x</think>答案后',
    '<think>a</think><think>b</think>结果',
    '无闭合<think>思考没结束了',
    '<THINK>大写标签</THINK>正文',
    '<think>含{花括号}和"引号"</think>{"a":1}',
    '  <think>x</think>  收尾空白  ',
    '',
  ];
  const readCases = [
    { content: '答案' },
    { content: '', reasoning_content: '回落到 reasoning' },
    { content: '   ', reasoning_content: '空白回落' },
    { content: '答案', reasoning_content: '思考' },
    { content: '', reasoning_content: '' },
    { reasoning_content: 'only reasoning' },
    {},
    undefined,
  ];
  return {
    stripReasoning: {
      fn: 'stripReasoning',
      note: '剥成对 <think>…</think>(大小写不敏感/跨行)+ trim;无闭合不剥',
      cases: stripCases.map((s) => ({ input: s, expected: stripReasoning(s) })),
    },
    readReplyText: {
      fn: 'readReplyText',
      note: 'content 非空优先;否则 reasoning_content 回落;都空→content("")或 undefined(→null)',
      cases: readCases.map((m) => ({ input: m ?? null, expected: readReplyText(m) ?? null })),
    },
  };
}

// ── parity 夹具:JSON 抽取/解析（调用 TS extractJsonObject 与 parseJsonObject）──
function parityJsonExtract() {
  const extractRaws = [
    '{"a":1}',
    '```json\n{"a":1}\n```',
    '前缀说明 {"a":1} 后缀',
    '{"a":{"b":2},"c":[1,2]}',
    '{"a":"内含 } 花括号"}',
    '{"a":"转义 \\" 引号 } 内"}',
    '没有花括号',
    '{ 没闭合',
    '{"first":1} {"second":2}',
    '```\n{"x":true}\n```',
    '  {"trimmed":1}  ',
  ];
  const parseRaws = [
    '{"a":1}',
    '[1,2,3]',
    '42',
    'null',
    '"string"',
    '{"a":NaN}',
    '{"a":Infinity}',
    '{"a":-Infinity}',
    '```json {"ok":1} ```',
    'garbage no json',
    '{"nested":{"x":1},"arr":[1,2]}',
  ];
  return {
    extractJsonObject: {
      fn: 'extractJsonObject',
      note: '去围栏 + 括号配平取第一个平衡对象(跳字符串内花括号/转义);抠不到→null',
      cases: extractRaws.map((raw) => ({ input: raw, expected: extractJsonObject(raw) })),
    },
    parseJsonObject: {
      fn: 'parseJsonObject',
      note: '只认对象(数组/标量/null 不合法→null);JSON.parse 拒 NaN/Infinity(py 须 parse_constant 拒)',
      cases: parseRaws.map((raw) => ({ input: raw, expected: parseJsonObject(raw) })),
    },
  };
}

// ── parity 夹具:distill（调用 TS distill 与 stub LLM，记录消息、事件及调用计数）──
async function parityDistill() {
  const build = async (lang) => {
    const cfg = { ...config, language: lang };
    const stores = openStores(':memory:', cfg, () => new Date('2026-01-01T00:00:00.000Z'));
    const put = (sourceKind, rawContent, occurredAt, extra = {}) =>
      stores.evidenceStore.put({
        subjectId: 'owner',
        sourceKind,
        hostId: 'local',
        occurredAt,
        rawContent,
        ...extra,
      });
    // 插入序打乱,验 distill 按 occurredAt 排序;覆盖隐私门各分支。
    put('spoken', '我最近在学 Rust', '2026-01-01T10:00:00.000Z'); // digestible
    put('observed', '凌晨3点还在打游戏', '2026-01-01T03:00:00.000Z'); // observed→cloud=false → tier 挡
    put('spoken', '是的', '2026-01-01T11:00:00.000Z', { precedingAiContext: 'AI:你喜欢爬山吧?' }); // digestible + AI 上文
    put('spoken', '不想被推断', '2026-01-01T09:00:00.000Z', { allowInference: false }); // readable 但 infer=false → 不 digestible
    const seen = [];
    let n = 0;
    const stubLlm = {
      get callCount() {
        return n;
      },
      tier: 'cloud',
      async chat(messages) {
        seen.push(messages);
        n++;
        return '  用户在学 Rust 并确认了偏好。  '; // 带首尾空白验 trim
      },
    };
    const result = await distill('owner', {
      evidenceStore: stores.evidenceStore,
      eventStore: stores.eventStore,
      llm: stubLlm,
      config: cfg,
    });
    const out = {
      messages: seen[0],
      eventSummary: result.event ? result.event.summary : null,
      eventOccurredAt: result.event ? result.event.occurredAt : null,
      pendingCount: result.pendingCount,
      tierBlockedCount: result.tierBlockedCount,
      llmCalls: result.llmCalls,
      digestibleCount: result.event ? stores.eventStore.evidenceOf(result.event.id).length : 0,
    };
    stores.close();
    return out;
  };
  return {
    note: 'distill 证据→事件:messages 逐字节(system=prompt/user=材料行) + event summary(trim)/occurredAt(时间锚) + pending/tierBlocked/digestible 计数;隐私门(observed cloud挡/infer=false 不消化)',
    zh: await build('zh'),
    en: await build('en'),
  };
}

// ── parity 夹具:consolidate 四分支（调用 TS consolidate 与返回固定结果的 stub LLM）──
async function parityConsolidate() {
  const T = '2026-01-01T00:00:00.000Z';
  const build = async (lang) => {
    const cfg = { ...config, language: lang };
    const stores = openStores(':memory:', cfg, () => new Date(T));
    const mkCog = (id, content, contentType, formedBy, confidence, credStatus) => ({
      id,
      subjectId: 'owner',
      content,
      contentType,
      formedBy,
      confidence,
      credStatus,
      scope: null,
      validAt: null,
      invalidAt: null,
      askedAt: null,
      archivedAt: null,
      mutedAt: null,
      createdAt: T,
      updatedAt: T,
    });
    // existing 认知(insert 固定 id;各挂一条旧证据):reinforce/correct/conflict 各引一个。
    stores.cognitionStore.insert(
      mkCog('cog-reinf', '喜欢喝咖啡', 'preference', 'confirmed', 280, 'candidate'),
      [{ evidenceId: 'ev-old-reinf', relation: 'support' }],
    );
    stores.cognitionStore.insert(
      mkCog('cog-corr', '在北京工作', 'fact', 'stated', 600, 'limited'),
      [{ evidenceId: 'ev-old-corr', relation: 'support' }],
    );
    stores.cognitionStore.insert(
      mkCog('cog-conf', '喜欢早睡', 'preference', 'stated', 600, 'limited'),
      [{ evidenceId: 'ev-old-conf', relation: 'support' }],
    );
    // 事件覆盖的证据(spoken/cloud=true/infer=true → 全过隐私门)。
    const put = (content) =>
      stores.evidenceStore.put({
        subjectId: 'owner',
        sourceKind: 'spoken',
        hostId: 'local',
        occurredAt: T,
        rawContent: content,
      });
    const ev1 = put('我最近在学 Rust');
    const ev2 = put('对，我每天都喝咖啡');
    const ev3 = put('其实我在上海工作了');
    const ev4 = put('我昨晚熬夜了');
    // 未消化事件,覆盖 ev1-4。
    stores.eventStore.insert(
      { id: 'evt1', subjectId: 'owner', summary: '用户聊了近况', occurredAt: T, createdAt: T },
      [ev1.id, ev2.id, ev3.id, ev4.id],
      { consolidated: false },
    );
    // stub LLMOut:四分支 + 短标号 e1-e4 + cognition_id + resolutions。
    const llmOut = {
      new: [
        {
          content: '在学 Rust',
          content_type: 'project',
          formed_by: 'stated',
          support_evidence_ids: ['e1'],
        },
      ],
      reinforce: [{ cognition_id: 'cog-reinf', support_evidence_ids: ['e2'] }], // 主动说→载体维 stated→触发并存
      correct: [
        {
          cognition_id: 'cog-corr',
          content: '在上海工作',
          content_type: 'fact',
          formed_by: 'stated',
          support_evidence_ids: ['e3'],
        },
      ],
      conflict: [{ cognition_id: 'cog-conf', support_evidence_ids: ['e4'] }],
      resolutions: [
        {
          evidence_id: 'e2',
          resolved_content: '用户确认每天喝咖啡',
          response_act: 'elaborate',
          proposition_origin: 'user_stated',
        },
      ],
    };
    let n = 0;
    const seen = [];
    const stubLlm = {
      get callCount() {
        return n;
      },
      tier: 'cloud',
      async chat(messages) {
        seen.push(messages);
        n++;
        return JSON.stringify(llmOut);
      },
    };
    const result = await consolidate('owner', {
      eventStore: stores.eventStore,
      evidenceStore: stores.evidenceStore,
      cognitionStore: stores.cognitionStore,
      semanticResolutionStore: stores.semanticResolutionStore,
      llm: stubLlm,
      config: cfg,
    });
    const created = result.created.map((c) => ({
      content: c.content,
      contentType: c.contentType,
      formedBy: c.formedBy,
      confidence: c.confidence,
      credStatus: c.credStatus,
      supportCount: stores.cognitionStore.sourcesOf(c.id).filter((s) => s.relation === 'support')
        .length,
    }));
    const cogReinf = stores.cognitionStore.get('cog-reinf');
    const cogCorr = stores.cognitionStore.get('cog-corr');
    const cogConf = stores.cognitionStore.get('cog-conf');
    const res = stores.semanticResolutionStore.ofEvidence(ev2.id);
    const out = {
      messages: seen[0],
      created,
      reinforced: result.reinforced,
      corrected: result.corrected,
      conflicted: result.conflicted,
      processedEvents: result.processedEvents,
      cogReinfConfidence: cogReinf.confidence,
      cogReinfFormedBy: cogReinf.formedBy,
      cogCorrInvalidated: cogCorr.invalidAt !== null,
      cogConfCredStatus: cogConf.credStatus,
      resolution: res
        ? {
            resolvedContent: res.resolvedContent,
            responseAct: res.responseAct,
            propositionOrigin: res.propositionOrigin,
            resolverVersion: res.resolverVersion,
          }
        : null,
      allEventsConsolidated: stores.eventStore.unconsolidated('owner').length === 0,
    };
    stores.close();
    return out;
  };
  return {
    note: 'consolidate 四分支(new/reinforce 含并存 stated/correct/conflict)+ resolution 落库;messages 字节 + created 结构 + 计数 + 落库状态',
    zh: await build('zh'),
    en: await build('en'),
  };
}

// ── parity 夹具:attribute 归因（调用 TS attribute 与 stub LLM）──
function _mkStateCog(id, content, createdAt) {
  return {
    id,
    subjectId: 'owner',
    content,
    contentType: 'state',
    formedBy: 'stated',
    confidence: 300,
    credStatus: 'low',
    scope: null,
    validAt: null,
    invalidAt: null,
    askedAt: null,
    archivedAt: null,
    mutedAt: null,
    createdAt,
    updatedAt: createdAt,
  };
}

async function parityAttribute() {
  const NOW = '2026-01-02T00:00:00.000Z';
  const build = async (lang) => {
    const cfg = { ...config, language: lang };
    const stores = openStores(':memory:', cfg, () => new Date('2026-01-01T00:00:00.000Z'));
    const put = (sourceKind, rawContent, occurredAt, extra = {}) =>
      stores.evidenceStore.put({
        subjectId: 'owner',
        sourceKind,
        hostId: 'local',
        occurredAt,
        rawContent,
        ...extra,
      });
    const p1 = put('spoken', '昨晚没睡好', '2026-01-01T22:00:00.000Z');
    const p2 = put('spoken', '今天也没睡好', '2026-01-01T23:00:00.000Z'); // 最晚 → 现象锚
    put('observed', '凌晨3点还在打游戏', '2026-01-01T03:00:00.000Z', { allowCloudRead: true }); // 候选原因(显式授权上云)
    put('spoken', '晚上喝了咖啡', '2026-01-01T20:00:00.000Z'); // 候选原因
    // 现象:state 认知挂 2 条支撑(满足 minPhenomenonSupport=2)、无假设引用过(未归因)
    stores.cognitionStore.insert(
      _mkStateCog('cog-phenom', '最近总没睡好', '2026-01-01T00:00:00.000Z'),
      [
        { evidenceId: p1.id, relation: 'support' },
        { evidenceId: p2.id, relation: 'support' },
      ],
    );
    let n = 0;
    const seen = [];
    const stubLlm = {
      get callCount() {
        return n;
      },
      tier: 'cloud',
      async chat(messages) {
        seen.push(messages);
        n++;
        return JSON.stringify({
          hypotheses: [{ content: '可能是熬夜打游戏导致没睡好', based_on_evidence_ids: ['e1'] }],
        });
      },
    };
    const result = await attribute('owner', {
      evidenceStore: stores.evidenceStore,
      cognitionStore: stores.cognitionStore,
      llm: stubLlm,
      config: cfg,
      clock: () => new Date(NOW),
    });
    const out = {
      messages: seen[0],
      hypotheses: result.hypotheses.map((h) => ({
        content: h.cognition.content,
        contentType: h.cognition.contentType,
        formedBy: h.cognition.formedBy,
        confidence: h.cognition.confidence,
        credStatus: h.cognition.credStatus,
        basedOnCount: h.basedOnEvidenceIds.length,
        phenomenon: h.phenomenon,
      })),
      consideredPhenomena: result.consideredPhenomena,
      llmCalls: result.llmCalls,
    };
    stores.close();
    return out;
  };
  return {
    note: 'attribute 归因:现象筛选(minPhenomenonSupport≥2 / 未归因)+ 时间窗候选(禁 state→state)+ 短标号 e1 + hypothesisCap 封顶 + 支撑=原因+现象锚',
    zh: await build('zh'),
    en: await build('en'),
  };
}

// ── parity 夹具:trends 趋势聚合（调用 TS aggregateTrends 与 stub LLM）──
async function parityTrends() {
  const NOW = '2026-01-02T00:00:00.000Z';
  const build = async (lang) => {
    const cfg = { ...config, language: lang };
    const stores = openStores(':memory:', cfg, () => new Date('2026-01-01T00:00:00.000Z'));
    const put = (rawContent, occurredAt) =>
      stores.evidenceStore.put({
        subjectId: 'owner',
        sourceKind: 'spoken',
        hostId: 'local',
        occurredAt,
        rawContent,
      });
    const t1 = put('好累', '2026-01-01T10:00:00.000Z');
    const t2 = put('没睡好', '2026-01-01T11:00:00.000Z');
    const t3 = put('提不起劲', '2026-01-01T12:00:00.000Z');
    // 3 条 state 认知(同 confidence,createdAt 递增 → all() 序 s1,s2,s3);满足 trendMinCount=3
    stores.cognitionStore.insert(_mkStateCog('cog-s1', '很累', '2026-01-01T00:00:01.000Z'), [
      { evidenceId: t1.id, relation: 'support' },
    ]);
    stores.cognitionStore.insert(_mkStateCog('cog-s2', '没睡好', '2026-01-01T00:00:02.000Z'), [
      { evidenceId: t2.id, relation: 'support' },
    ]);
    stores.cognitionStore.insert(_mkStateCog('cog-s3', '提不起劲', '2026-01-01T00:00:03.000Z'), [
      { evidenceId: t3.id, relation: 'support' },
    ]);
    let n = 0;
    const seen = [];
    const stubLlm = {
      get callCount() {
        return n;
      },
      tier: 'cloud',
      async chat(messages) {
        seen.push(messages);
        n++;
        return JSON.stringify({
          trends: [{ content: '用户最近持续情绪低落', based_on_evidence_ids: ['e1', 'e2', 'e3'] }],
        });
      },
    };
    const result = await aggregateTrends(
      'owner',
      {
        evidenceStore: stores.evidenceStore,
        cognitionStore: stores.cognitionStore,
        llm: stubLlm,
        config: cfg,
      },
      new Date(NOW),
    );
    const out = {
      messages: seen[0],
      trends: result.trends.map((t) => ({
        content: t.content,
        contentType: t.contentType,
        formedBy: t.formedBy,
        confidence: t.confidence,
        credStatus: t.credStatus,
        supportCount: stores.cognitionStore.sourcesOf(t.id).filter((s) => s.relation === 'support')
          .length,
      })),
      consideredCount: result.consideredCount,
      llmCalls: result.llmCalls,
    };
    stores.close();
    return out;
  };
  return {
    note: 'trends 趋势:窗口内 state 支撑证据(all() 历史口径、排除 confirmed、不筛 allowInference)+ trendMinCount=3 规则门 + 短标号 + formedBy=ruled',
    zh: await build('zh'),
    en: await build('en'),
  };
}

// ── parity 夹具:asking(proposeAsk / revisitConflicts;模板路径 vs LLM 措辞路径)—— ──
async function parityAsking() {
  const T = '2026-01-01T00:00:00.000Z';
  const mkCog = (id, content, contentType, credStatus, confidence) => ({
    id,
    subjectId: 'owner',
    content,
    contentType,
    formedBy: 'inferred',
    confidence,
    credStatus,
    scope: null,
    validAt: null,
    invalidAt: null,
    askedAt: null,
    archivedAt: null,
    mutedAt: null,
    createdAt: T,
    updatedAt: T,
  });
  const dumpProposal = (p) => ({
    cognitionId: p.cognitionId,
    kind: p.kind,
    hypothesis: p.hypothesis,
    question: p.question,
    evidenceSummaries: p.evidence.map((e) => e.summary),
    contradictSummaries: p.contradictEvidence ? p.contradictEvidence.map((e) => e.summary) : null,
    confidence: p.confidence,
    credStatus: p.credStatus,
  });
  const stubOf = (reply) => {
    let n = 0;
    const seen = [];
    return {
      seen,
      llm: {
        get callCount() {
          return n;
        },
        tier: 'cloud',
        async chat(m) {
          seen.push(m);
          n++;
          return reply;
        },
      },
    };
  };
  const setupAsk = (cfg) => {
    const st = openStores(':memory:', cfg, () => new Date(T));
    const put = (sourceKind, rawContent, extra = {}) =>
      st.evidenceStore.put({
        subjectId: 'owner',
        sourceKind,
        hostId: 'local',
        occurredAt: T,
        rawContent,
        ...extra,
      });
    const e1 = put('spoken', '我最近老熬夜');
    const e2 = put('observed', '凌晨3点还在打游戏', { allowCloudRead: true }); // observed 优先亮出来
    st.cognitionStore.insert(
      mkCog('cog-hypo', '可能是熬夜导致没睡好', 'hypothesis', 'candidate', 240),
      [
        { evidenceId: e1.id, relation: 'support' },
        { evidenceId: e2.id, relation: 'support' },
      ],
    );
    return st;
  };
  const setupConflict = (cfg) => {
    const st = openStores(':memory:', cfg, () => new Date(T));
    const put = (rawContent) =>
      st.evidenceStore.put({
        subjectId: 'owner',
        sourceKind: 'spoken',
        hostId: 'local',
        occurredAt: T,
        rawContent,
      });
    const e3 = put('我喜欢早睡');
    const e4 = put('昨晚熬到3点');
    st.cognitionStore.insert(mkCog('cog-conflict', '喜欢早睡', 'preference', 'conflicted', 600), [
      { evidenceId: e3.id, relation: 'support' },
      { evidenceId: e4.id, relation: 'contradict' },
    ]);
    return st;
  };
  const build = async (lang) => {
    const cfg = { ...config, language: lang };
    const clock = () => new Date(T);
    let s = setupAsk(cfg);
    const tplAsk = await proposeAsk('owner', {
      cognitionStore: s.cognitionStore,
      evidenceStore: s.evidenceStore,
      config: cfg,
      clock,
    });
    const tplAskedAt = s.cognitionStore.get('cog-hypo').askedAt; // markAsked 默认 true
    s.close();

    s = setupAsk(cfg);
    const st1 = stubOf('  你最近是不是熬夜比较多?  '); // 带首尾空白验 trim
    const llmAsk = await proposeAsk('owner', {
      cognitionStore: s.cognitionStore,
      evidenceStore: s.evidenceStore,
      llm: st1.llm,
      config: cfg,
      clock,
    });
    const llmAskMessages = st1.seen[0];
    s.close();

    s = setupConflict(cfg);
    const tplRev = await revisitConflicts('owner', {
      cognitionStore: s.cognitionStore,
      evidenceStore: s.evidenceStore,
      config: cfg,
      clock,
    });
    s.close();

    s = setupConflict(cfg);
    const st2 = stubOf('  你现在到底是早睡还是熬夜?  ');
    const llmRev = await revisitConflicts('owner', {
      cognitionStore: s.cognitionStore,
      evidenceStore: s.evidenceStore,
      llm: st2.llm,
      config: cfg,
      clock,
    });
    const llmRevMessages = st2.seen[0];
    s.close();

    return {
      proposeAskTemplate: {
        proposals: tplAsk.proposals.map(dumpProposal),
        llmCalls: tplAsk.llmCalls,
        askedAt: tplAskedAt,
      },
      proposeAskLlm: {
        messages: llmAskMessages,
        proposals: llmAsk.proposals.map(dumpProposal),
        llmCalls: llmAsk.llmCalls,
      },
      revisitTemplate: { proposals: tplRev.proposals.map(dumpProposal), llmCalls: tplRev.llmCalls },
      revisitLlm: {
        messages: llmRevMessages,
        proposals: llmRev.proposals.map(dumpProposal),
        llmCalls: llmRev.llmCalls,
      },
    };
  };
  return {
    note: 'asking:候选筛选(hypothesis/askedAt null/askableStatuses/confidenceBand)+ observed 优先 + 模板 vs LLM 措辞(trim/空回落)+ 两面证据 + askedAt 去重写',
    zh: await build('zh'),
    en: await build('en'),
  };
}

// ── parity 夹具:updateProfile 编排(distill→consolidate→attribute→重索引)—— ──
async function parityUpdateProfile() {
  const T = '2026-01-01T00:00:00.000Z';
  const build = async (lang) => {
    const cfg = { ...config, language: lang };
    const stores = openStores(':memory:', cfg, () => new Date(T));
    const put = (rawContent) =>
      stores.evidenceStore.put({
        subjectId: 'owner',
        sourceKind: 'spoken',
        hostId: 'local',
        occurredAt: T,
        rawContent,
      });
    put('我每天都喝咖啡');
    put('尤其是早上那杯');
    // stub llm 按调用序返回:① distill 摘要 ② consolidate JSON;attribute 无 state 现象 → 不调。
    const replies = [
      '用户聊到每天喝咖啡的习惯',
      JSON.stringify({
        new: [
          {
            content: '喜欢喝咖啡',
            content_type: 'preference',
            formed_by: 'stated',
            support_evidence_ids: ['e1'],
          },
        ],
      }),
    ];
    let n = 0;
    const stubLlm = {
      get callCount() {
        return n;
      },
      tier: 'cloud',
      async chat() {
        const r = replies[n] ?? '{}';
        n++;
        return r;
      },
    };
    let indexedItems = [];
    const stubRetriever = {
      async indexAll(items) {
        indexedItems = items;
      },
      async search() {
        return [];
      },
    };
    const result = await updateProfile('owner', {
      evidenceStore: stores.evidenceStore,
      eventStore: stores.eventStore,
      cognitionStore: stores.cognitionStore,
      semanticResolutionStore: stores.semanticResolutionStore,
      retriever: stubRetriever,
      llm: stubLlm,
      transaction: stores.transaction,
      config: cfg,
      clock: () => new Date(T),
    });
    const out = {
      distilled: {
        eventSummary: result.distilled.event ? result.distilled.event.summary : null,
        pendingCount: result.distilled.pendingCount,
        tierBlockedCount: result.distilled.tierBlockedCount,
        llmCalls: result.distilled.llmCalls,
      },
      consolidated: {
        created: result.consolidated.created.map((c) => ({
          content: c.content,
          contentType: c.contentType,
          formedBy: c.formedBy,
          confidence: c.confidence,
          credStatus: c.credStatus,
        })),
        reinforced: result.consolidated.reinforced,
        corrected: result.consolidated.corrected,
        conflicted: result.consolidated.conflicted,
        processedEvents: result.consolidated.processedEvents,
        llmCalls: result.consolidated.llmCalls,
        profileSize: result.consolidated.profileSize,
        promptChars: result.consolidated.promptChars,
      },
      attributed: {
        hypothesesCount: result.attributed.hypotheses.length,
        consideredPhenomena: result.attributed.consideredPhenomena,
        llmCalls: result.attributed.llmCalls,
      },
      indexed: result.indexed,
      indexError: result.indexError,
      indexedTexts: indexedItems.map((i) => i.text),
      metrics: result.metrics,
      totalLlmCalls: n,
    };
    stores.close();
    return out;
  };
  return {
    note: 'updateProfile 编排:distill→consolidate→attribute→重索引(active 且未 muted);索引失败不回滚画像;metrics 透传',
    zh: await build('zh'),
    en: await build('en'),
  };
}

// ── parity 夹具:importBundle 完整 ImportPlan(dryRun/merge/幂等/非法/originId 撞库/悬空 corrects)—— ──
function parityImport() {
  const T = '2026-01-01T00:00:00.000Z';
  const mkDeps = (stores) => ({
    evidenceStore: stores.evidenceStore,
    eventStore: stores.eventStore,
    cognitionStore: stores.cognitionStore,
    interactionContextStore: stores.interactionContextStore,
    semanticResolutionStore: stores.semanticResolutionStore,
    transaction: stores.transaction,
  });
  const dbState = (stores) => ({
    evidence: stores.evidenceStore.all().length,
    events: stores.eventStore.all('owner').length,
    cognitions: stores.cognitionStore.all('owner').length,
  });
  const run = (bundle, mode, preSeed) => {
    const stores = openStores(':memory:', config, () => new Date(T));
    if (preSeed) preSeed(stores);
    const plan = importBundle(bundle, mkDeps(stores), { mode });
    const out = { plan, dbState: dbState(stores) };
    stores.close();
    return out;
  };

  const good = seedBundle();
  // 幂等:同一库连导两次
  const twice = (() => {
    const stores = openStores(':memory:', config, () => new Date(T));
    const deps = mkDeps(stores);
    const first = importBundle(good, deps, { mode: 'merge' });
    const second = importBundle(good, deps, { mode: 'merge' });
    const out = { first, second, dbState: dbState(stores) };
    stores.close();
    return out;
  })();

  const invalid = structuredClone(good);
  invalid.data.eventEvidence[0].evidenceId = 'ghost'; // 悬空溯源 → validateBundle 致命 error

  // originId 撞库:包里 ev-1 带 originId,库中已有【另一条 id】占用同 originId
  const withOrigin = structuredClone(good);
  withOrigin.data.evidence[0].originId = 'origin-x';
  const originCollision = run(withOrigin, 'merge', (stores) => {
    stores.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 'local',
      occurredAt: T,
      rawContent: '库里已有',
      originId: 'origin-x',
    });
  });

  // 悬空 correctsEvidenceId:指向包外/库外 → 落库前置空 + 告警
  const dangling = structuredClone(good);
  dangling.data.evidence[1].correctsEvidenceId = 'ghost-corrects';
  const danglingRun = (() => {
    const stores = openStores(':memory:', config, () => new Date(T));
    const plan = importBundle(dangling, mkDeps(stores), { mode: 'merge' });
    const got = stores.evidenceStore.get('ev-2');
    const out = {
      plan,
      dbState: dbState(stores),
      correctsAfter: got ? got.correctsEvidenceId : null,
    };
    stores.close();
    return out;
  })();

  return {
    note: 'importBundle 完整 ImportPlan:dryRun 只算不写 / merge 写入 / 幂等 duplicates / 非法包拒写 / originId 撞库丢悬空 join + 告警 / 悬空 corrects 置空 + 告警。warnings 语言随 resolveLang()(env 缺省 en)。',
    dryRun: run(good, 'dryRun'),
    merge: run(good, 'merge'),
    twice,
    invalid: run(invalid, 'merge'),
    originCollision,
    danglingCorrects: danglingRun,
  };
}

// ── parity 夹具: eval 判定纯函数(checkStructural / parseYesNo)—— ──
function parityEvalChecks() {
  const mkRun = (over = {}) => ({
    error: over.error ?? null,
    consolidated: {
      created: [],
      createdCount: 0,
      reinforced: 0,
      corrected: 0,
      conflicted: 0,
      processedEvents: 1,
      ...(over.consolidated ?? {}),
    },
    active: over.active ?? [],
    cogSources: over.cogSources ?? [],
    evidenceIds: new Set(over.evidenceIds ?? []),
    resolutions: over.resolutions ?? [],
    timings: null,
  });
  const cases = [];
  const add = (label, scenario, run) => {
    cases.push({
      label,
      input: { scenario, run: { ...run, evidenceIds: [...run.evidenceIds] } }, // Set → array 供 JSON
      expected: checkStructural(scenario, run),
    });
  };

  add(
    'conflict-hit',
    { discipline: 'conflict', lang: 'zh', expect: { conflict: true } },
    mkRun({ consolidated: { conflicted: 1 } }),
  );
  add('correct-miss', { discipline: 'correct', lang: 'zh', expect: { correct: true } }, mkRun());
  add(
    'new-cognitions-types-formedby',
    {
      discipline: 'fact-vs-belief',
      lang: 'zh',
      expect: {
        newCognitions: {
          min: 1,
          max: 2,
          types: ['fact', 'preference'],
          formedBy: ['stated', 'confirmed'],
        },
      },
    },
    mkRun({
      consolidated: {
        createdCount: 2,
        created: [
          { contentType: 'fact', formedBy: 'stated' },
          { contentType: 'goal', formedBy: 'inferred' },
        ],
      },
    }),
  );
  add('chitchat-ok', { discipline: 'chitchat-negative', lang: 'zh', expect: {} }, mkRun());
  add(
    'chitchat-bad',
    { discipline: 'chitchat-negative', lang: 'zh', expect: {} },
    mkRun({
      consolidated: { createdCount: 1, created: [{ contentType: 'fact', formedBy: 'stated' }] },
    }),
  );
  add(
    'short-reply-ok',
    { discipline: 'short-reply', lang: 'zh', expect: { resolutions: { responseAct: ['affirm'] } } },
    mkRun({
      resolutions: [
        { id: 'e1', sourceKind: 'spoken', hasAiContext: true, res: { responseAct: 'affirm' } },
      ],
    }),
  );
  add(
    'short-reply-missing-and-bad-act',
    { discipline: 'short-reply', lang: 'zh', expect: { resolutions: { responseAct: ['negate'] } } },
    mkRun({
      resolutions: [
        { id: 'e1', sourceKind: 'spoken', hasAiContext: true, res: { responseAct: 'affirm' } },
        { id: 'e2', sourceKind: 'spoken', hasAiContext: true, res: null },
      ],
    }),
  );
  add(
    'invariants-bad',
    { discipline: 'emotion-cap', lang: 'zh', expect: {} },
    mkRun({
      active: [{ id: 'c1', contentType: 'state', credStatus: 'stable', confidence: 1200 }],
      cogSources: [
        { id: 'c1', contentType: 'state', sources: [{ evidenceId: 'ghost', relation: 'support' }] },
      ],
      evidenceIds: ['real'],
    }),
  );
  add(
    'run-error',
    { discipline: 'conflict', lang: 'zh', expect: { conflict: true } },
    mkRun({ error: 'boom' }),
  );

  const yesNoInputs = [
    'YES',
    'NO',
    'yes',
    ' Yes. ',
    'NO, definitely not',
    'YES and NO',
    'NO but YES',
    'maybe',
    '',
    'YESTERDAY',
    'NOPE',
    '是的',
  ];
  return {
    checkStructural: {
      fn: 'checkStructural',
      note: '结构性断言(程序判、不调 LLM):expect 各分支 + short-reply 解析覆盖/responseAct + 三不变量 + run.error',
      cases,
    },
    parseYesNo: {
      fn: 'parseYesNo',
      note: '容错解析 judge 的 YES/NO(大小写/标点/夹句中;含糊→保守 NO;两者都有取先出现的)',
      cases: yesNoInputs.map((input) => ({ input, expected: parseYesNo(input) })),
    },
  };
}

// ── parity 夹具:SQLite schema(从 openStores 真建库 dump 权威结构;供 Python 建同构表对拍) ──
//   dump 逐表列结构(pragma_table_info,驱动无关 → node:sqlite/better-sqlite3 一致)+ user_version。
function buildSchema() {
  const stores = openStores(':memory:');
  const db = stores.db;
  try {
    const tables = db
      .prepare(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
      )
      .all()
      .map((r) => r.name);
    const uv = db.prepare('PRAGMA user_version').get();
    const out = {
      note: '从 openStores(:memory:) dump 的权威持久化 schema;Python 建同构表后逐表对拍。',
      userVersion: Number(uv.user_version),
      tables: {},
    };
    for (const t of tables) {
      // pragma_table_info 表值函数;t 来自 sqlite_master(非用户输入),内插安全。
      const cols = db
        .prepare(
          `SELECT name, type, "notnull" AS nn, dflt_value AS dflt, pk FROM pragma_table_info('${t}')`,
        )
        .all();
      out.tables[t] = cols.map((c) => ({
        name: c.name,
        type: c.type,
        notnull: Number(c.nn),
        dflt: c.dflt ?? null,
        pk: Number(c.pk),
      }));
    }
    return out;
  } finally {
    stores.close();
  }
}

// ── parity 夹具:FTS5 trigram bm25 排序(golden 只锁【id 排序】,不锁 bm25 分数——分数随 SQLite 小版本微动,
//   排序在清晰分隔的数据上稳定；跨运行时一致性由 fts5-trigram-cross-lang-parity 覆盖。
function buildFtsGolden() {
  const stores = openStores(':memory:');
  const db = stores.db;
  try {
    db.exec(
      "CREATE VIRTUAL TABLE cognition_fts USING fts5(cognition_id UNINDEXED, text, tokenize='trigram')",
    );
    const data = [
      ['c1', '我喜欢爬山和户外运动'],
      ['c2', '爬山是很好的运动'],
      ['c3', 'I like hiking and coffee'],
      ['c4', '喝咖啡 coffee 每天喝'],
      ['c5', '周末去爬山爬山爬山'],
      ['c6', '不喜欢任何运动'],
    ];
    const ins = db.prepare('INSERT INTO cognition_fts (cognition_id, text) VALUES (?, ?)');
    for (const [id, t] of data) ins.run(id, t);
    const ids = (match) =>
      db
        .prepare(
          'SELECT cognition_id FROM cognition_fts WHERE cognition_fts MATCH ? ORDER BY bm25(cognition_fts) LIMIT 10',
        )
        .all(match)
        .map((r) => r.cognition_id);
    // MATCH 串照 toMatchQuery 的短语形态(双引号包);≤2 字 CJK 命中空(trigram 需 ≥3 字)也是 parity 的一部分。
    const matches = ['"coffee"', '"hiking"', '"户外运动"', '"爬山爬山"', '"运动"', '"爬山"'];
    return {
      note: 'FTS5 trigram + bm25 排序 golden;只锁 id 排序(见函数注释)。Python 建同款表+同数据,断言排序一致。',
      ddl: "CREATE VIRTUAL TABLE cognition_fts USING fts5(cognition_id UNINDEXED, text, tokenize='trigram')",
      data,
      cases: matches.map((match) => ({ match, ids: ids(match) })),
    };
  } finally {
    stores.close();
  }
}

// ── parity 夹具:便携包(interop 用一个完整 valid bundle + validate 用好/坏例) ──
//   bundle 由手工构造【固定 id/时间戳】(store.put 会生随机 UUID,不可作确定性 golden),
//   但用 TS validateBundle 断言它确实 valid → 是「真·合法 MemoWeft 包」;Python 导入它验往返保真。
function seedBundle() {
  const ev = (id, extra = {}) => ({
    id,
    subjectId: 'owner',
    sourceKind: 'spoken',
    hostId: 'local',
    originId: null,
    occurredAt: '2026-01-01T00:00:00.000Z',
    recordedAt: '2026-01-01T00:00:01.000Z',
    rawContent: `原话 ${id}`,
    summary: `摘要 ${id}`,
    allowLocalRead: true,
    allowCloudRead: false,
    allowInference: true,
    correctsEvidenceId: null,
    ...extra,
  });
  return {
    format: BUNDLE_FORMAT,
    schemaVersion: BUNDLE_SCHEMA_VERSION,
    exportedAt: '2026-01-02T00:00:00.000Z',
    memoWeftVersion: '0.6.0-dev',
    subjectId: 'owner',
    source: { hostId: 'local', exportMode: 'full' },
    data: {
      evidence: [ev('ev-1'), ev('ev-2', { sourceKind: 'tool', allowCloudRead: false })],
      events: [
        {
          id: 'evt-1',
          subjectId: 'owner',
          summary: '一个事件',
          occurredAt: '2026-01-01T00:00:00.000Z',
          createdAt: '2026-01-01T00:00:02.000Z',
          consolidated: true,
        },
      ],
      eventEvidence: [
        { eventId: 'evt-1', evidenceId: 'ev-1' },
        { eventId: 'evt-1', evidenceId: 'ev-2' },
      ],
      cognitions: [
        {
          id: 'cog-1',
          subjectId: 'owner',
          content: '用户喜欢 X',
          contentType: 'preference',
          formedBy: 'stated',
          confidence: 600,
          credStatus: 'limited',
          scope: null,
          validAt: null,
          invalidAt: null,
          askedAt: null,
          archivedAt: null,
          mutedAt: null,
          createdAt: '2026-01-01T00:00:03.000Z',
          updatedAt: '2026-01-01T00:00:03.000Z',
        },
      ],
      cognitionEvidence: [{ cognitionId: 'cog-1', evidenceId: 'ev-1', relation: 'support' }],
      unconsolidatedEventIds: [],
      interactionContexts: [
        {
          id: 'ic-1',
          subjectId: 'owner',
          conversationId: 'conv-1',
          episodeId: 'ep-1',
          context: [{ role: 'user', content: 'hi' }],
          contextHash: 'abc123',
          createdAt: '2026-01-01T00:00:00.500Z',
        },
      ],
      semanticResolutions: [],
    },
    metadata: { counts: { evidence: 2, events: 1, cognitions: 1 }, notes: ['fixture'] },
  };
}

function buildBundleFixtures() {
  const good = seedBundle();
  const vr = validateBundle(good);
  if (!vr.valid) throw new Error('seedBundle 不合法(生成器自检失败):' + JSON.stringify(vr.errors));

  // validate parity 好/坏例:每例的 expected 由 TS validateBundle 现算(en·默认 lang)。
  const clone = (mut) => {
    const b = structuredClone(good);
    mut(b);
    return b;
  };
  const cases = [
    { label: 'valid', bundle: good },
    { label: 'not-object', bundle: 42 },
    {
      label: 'wrong-format',
      bundle: clone((b) => {
        b.format = 'nope';
      }),
    },
    {
      label: 'schemaVersion-missing',
      bundle: clone((b) => {
        delete b.schemaVersion;
      }),
    },
    {
      label: 'schemaVersion-too-high',
      bundle: clone((b) => {
        b.schemaVersion = 99;
      }),
    },
    {
      label: 'schemaVersion-lower',
      bundle: clone((b) => {
        b.schemaVersion = 1;
      }),
    },
    {
      label: 'subjectId-missing',
      bundle: clone((b) => {
        delete b.subjectId;
      }),
    },
    {
      label: 'data-missing',
      bundle: clone((b) => {
        delete b.data;
      }),
    },
    {
      label: 'evidence-not-array',
      bundle: clone((b) => {
        b.data.evidence = {};
      }),
    },
    {
      label: 'evidence-missing-id',
      bundle: clone((b) => {
        b.data.evidence[0].id = '';
      }),
    },
    {
      label: 'duplicate-evidence-id',
      bundle: clone((b) => {
        b.data.evidence[1].id = 'ev-1';
      }),
    },
    {
      label: 'dangling-eventEvidence',
      bundle: clone((b) => {
        b.data.eventEvidence[0].evidenceId = 'ghost';
      }),
    },
    {
      label: 'dangling-cognitionEvidence',
      bundle: clone((b) => {
        b.data.cognitionEvidence[0].cognitionId = 'ghost';
      }),
    },
    {
      label: 'subject-mismatch-warning',
      bundle: clone((b) => {
        b.data.evidence[0].subjectId = 'other';
      }),
    },
    {
      label: 'corrects-out-of-bundle-warning',
      bundle: clone((b) => {
        b.data.evidence[0].correctsEvidenceId = 'ghost';
      }),
    },
    {
      label: 'unconsolidated-unknown-warning',
      bundle: clone((b) => {
        b.data.unconsolidatedEventIds = ['ghost'];
      }),
    },
  ].map((c) => ({ label: c.label, bundle: c.bundle, expected: validateBundle(c.bundle) }));

  return {
    bundle: good,
    validate: {
      note: 'expected 由 TS validateBundle 现算(en);Python validate 做字段级 exact parity 验证。',
      cases,
    },
  };
}

/** 生成全部共享资产(纯计算,async 仅因 embed)。返回 { path → object }。 */
export async function buildSharedAssets() {
  const he = parityHashEmbedder();
  const emb = new HashEmbedder(he.DIM);
  const vecs = await emb.embed(he.embedTexts);
  const embedCases = he.embedTexts.map((text, i) => ({
    input: { text, dim: he.DIM },
    expected: vecs[i],
  }));
  const distillFx = await parityDistill();
  const consolidateFx = await parityConsolidate();
  const attributeFx = await parityAttribute();
  const trendsFx = await parityTrends();
  const askingFx = await parityAsking();
  const updateProfileFx = await parityUpdateProfile();

  return {
    'config-constants.json': buildConfigConstants(),
    'prompts.json': buildPrompts(),
    'parity/confidence.json': parityConfidence(),
    'parity/cred-status.json': parityCredStatus(),
    'parity/formed-by.json': parityFormedBy(),
    'parity/decay.json': parityDecay(),
    'parity/hash-embedder.json': {
      fnv1a32: {
        fn: 'fnv1a32',
        note: 'FNV-1a 32位(Math.imul 32位有符号乘 + >>>0 无符号);expected 为 uint32,Python 须掩码复刻(numpy int32 或 &0xFFFFFFFF)',
        cases: he.fnvCases,
      },
      tokenize: {
        fn: 'tokenize',
        note: 'lowercase + \\p{L}+/\\p{N}+ 连续段,汉字段拆单字+bigram',
        cases: he.tokenizeCases,
      },
      embed: {
        fn: 'HashEmbedder.embed',
        dim: he.DIM,
        note: 'L2 归一化词袋向量(空文本→全零);expected 为 dim 维 double 数组',
        cases: embedCases,
      },
    },
    'parity/echoed-id.json': parityEchoedId(),
    'parity/evidence-auth.json': parityEvidenceAuth(),
    'parity/cognition-order.json': parityCognitionOrder(),
    'parity/source-label.json': paritySourceLabel(),
    'parity/context-hash.json': parityContextHash(),
    'parity/expire.json': parityExpire(),
    'parity/llm-text.json': parityLlmText(),
    'parity/json-extract.json': parityJsonExtract(),
    'parity/distill.json': distillFx,
    'parity/consolidate.json': consolidateFx,
    'parity/attribute.json': attributeFx,
    'parity/trends.json': trendsFx,
    'parity/asking.json': askingFx,
    'parity/update-profile.json': updateProfileFx,
    'parity/import.json': parityImport(),
    'parity/eval-checks.json': parityEvalChecks(),
    'parity/schema.json': buildSchema(),
    'parity/fts.json': buildFtsGolden(),
    ...(() => {
      const bf = buildBundleFixtures();
      return { 'parity/bundle.json': bf.bundle, 'parity/bundle-validate.json': bf.validate };
    })(),
  };
}

function jsonFiles(root) {
  if (!existsSync(root)) return [];
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = join(root, entry.name);
    if (entry.isDirectory()) return jsonFiles(path);
    return entry.isFile() && entry.name.endsWith('.json') ? [path] : [];
  });
}

function removeStaleAssets(target, assets) {
  const expected = new Set(Object.keys(assets));
  for (const path of jsonFiles(target)) {
    const rel = relative(target, path).replaceAll('\\', '/');
    if (!expected.has(rel)) rmSync(path);
  }
}

function writeAssets(target, assets, { prune = false } = {}) {
  if (prune) removeStaleAssets(target, assets);
  for (const [rel, obj] of Object.entries(assets)) {
    const destination = join(target, rel);
    mkdirSync(dirname(destination), { recursive: true });
    writeFileSync(destination, stableStringify(obj));
  }
}

function checkAssets(target, label, assets) {
  let drift = 0;
  const expected = new Set(Object.keys(assets));
  for (const [rel, obj] of Object.entries(assets)) {
    const p = join(target, rel);
    const fresh = stableStringify(obj);
    const committed = existsSync(p) ? readFileSync(p, 'utf8') : '';
    if (fresh !== committed) {
      console.error(`DRIFT: ${label}/${rel} 与 TS 源不一致(运行 npm run shared:update 刷新)`);
      drift++;
    }
  }
  for (const path of jsonFiles(target)) {
    const rel = relative(target, path).replaceAll('\\', '/');
    if (!expected.has(rel)) {
      console.error(`DRIFT: ${label}/${rel} 已不再由 TS 源生成(运行 npm run shared:update 清理)`);
      drift++;
    }
  }
  return drift;
}

// 仅作为主程序运行时读写文件；被测试 import 时只暴露 buildSharedAssets。
if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  const assets = await buildSharedAssets();
  if (process.argv.includes('--check')) {
    const drift =
      checkAssets(SHARED, 'shared', assets) +
      checkAssets(PYTHON_SHARED_DATA, 'py/src/memoweft/_shared_data', assets);
    if (drift > 0) process.exit(1);
    console.log('shared/ 与 Python 包内共享资产均与 TS 源一致。');
  } else {
    writeAssets(SHARED, assets);
    writeAssets(PYTHON_SHARED_DATA, assets, { prune: true });
    console.log(
      'shared/ 与 Python 包内资产已刷新(config-constants + prompts + parity 夹具:含 schema/fts)。',
    );
  }
}
