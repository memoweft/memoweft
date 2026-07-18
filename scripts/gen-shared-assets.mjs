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
import { openStores } from '../src/store/openStores.ts';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { sourceLabel, aiContextSuffix } from '../src/evidence/sourceLabel.ts';
import { hashContext } from '../src/interaction/interactionContextStore.ts';
import { expire } from '../src/background/expire.ts';
import { validateBundle } from '../src/portable/validateBundle.ts';
import { BUNDLE_FORMAT, BUNDLE_SCHEMA_VERSION } from '../src/portable/model.ts';

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
    _note: 'Language-neutral constants read by the pure-logic and storage layers. Source of truth = src/config.ts (+ CARRIER_RANK/hard cap noted). Regenerate via `npm run shared:update`.',
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
    // ── 身份默认(perceive/ingest 缺省 subjectId/hostId 用;identity 非 env 依赖,可纳入)——P2-1b 纳入 ──
    identity: c.identity, // config.ts:100(默认 owner/local)
    // ── 证据授权默认(evidence.put 按 sourceKind 分流补默认;跨语言【授权红线】常量,storage 层读)——P2-1a 纳入 ──
    privacyMode: c.privacyMode, // config.ts:103(cloudReadDefault = !privacyMode)
    evidenceDefaults: c.evidenceDefaults, // config.ts:104(spoken/inferred 通用默认;无 allowCloudRead → 走 cloudReadDefault)
    observedDefaults: c.observedDefaults, // config.ts:105(行为观察保守:local✓/cloud✗/infer✓)
    toolDefaults: c.toolDefaults, // config.ts:106(工具返回同 observed 保守 AD-3/D-0013)
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

// ── parity 夹具:evidence.put 授权分流(用真 TS SqliteEvidenceStore.put;golden 只取最终三位授权,不取随机 id/时间) ──
//   钉死「按 sourceKind 补保守默认 + 显式优先 + cloudReadDefault 跟随 privacyMode」的跨语言一致(P2-1a)。
function parityEvidenceAuth() {
  const FIXED = () => new Date('2026-01-01T00:00:00.000Z'); // 只为确定,授权不吃时间
  const explicits = [
    {},
    { allowLocalRead: true }, { allowLocalRead: false },
    { allowCloudRead: true }, { allowCloudRead: false },
    { allowInference: true }, { allowInference: false },
    { allowLocalRead: false, allowCloudRead: true, allowInference: false },
  ];
  const cases = [];
  for (const cfg of [config, { ...config, privacyMode: true }]) {
    const store = new SqliteEvidenceStore(':memory:', cfg, FIXED);
    let n = 0;
    for (const sourceKind of SOURCE_KINDS)
      for (const explicit of explicits) {
        const e = store.put({ subjectId: 'owner', sourceKind, hostId: 'local', rawContent: `x${n++}`, ...explicit });
        cases.push({
          input: { privacyMode: cfg.privacyMode, sourceKind, explicit },
          expected: { allowLocalRead: e.allowLocalRead, allowCloudRead: e.allowCloudRead, allowInference: e.allowInference },
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

// ── parity 夹具:cognition all/active 排序(用真 TS SqliteCognitionStore.insert 固定认知集 → id 序 golden) ──
//   钉 ORDER BY confidence DESC, created_at ASC + active 排除 invalid/archived(侦察点名 Phase 1b 未覆盖此序)。
function parityCognitionOrder() {
  const mk = (id, confidence, createdAt, extra = {}) => ({
    id, subjectId: 'owner', content: `内容 ${id}`, contentType: 'preference', formedBy: 'stated',
    confidence, credStatus: 'limited', scope: null, validAt: null, invalidAt: null,
    askedAt: null, archivedAt: null, mutedAt: null, createdAt, updatedAt: createdAt, ...extra,
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

// ── parity 夹具:sourceLabel + aiContextSuffix(用真 TS;钉 js-trim/UTF-16 slice/全角括号字节)——P2-1b ──
function paritySourceLabel() {
  const langs = ['zh', 'en'];
  const labelCases = [];
  for (const sk of SOURCE_KINDS)
    for (const lang of langs) labelCases.push({ input: { sourceKind: sk, lang }, expected: sourceLabel(sk, lang) });
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
    for (const lang of langs) suffixCases.push({ input: { text, lang }, expected: aiContextSuffix(text, lang) });
  return {
    sourceLabel: { fn: 'sourceLabel', note: '来源前缀(含尾随空格);未知退回 spoken', cases: labelCases },
    aiContextSuffix: {
      fn: 'aiContextSuffix',
      note: 'js-trim(去 BOM 等)+ UTF-16 slice(前 240 code unit)+ 全角括号 ⟨⟩;空/纯空白→""',
      max: 240,
      cases: suffixCases,
    },
  };
}

// ── parity 夹具:hashContext(sha256 over JSON.stringify(context))——钉 JSON 字节 + sha256 跨语言一致(P2-1b)──
function parityContextHash() {
  const contexts = [
    [],
    [{ role: 'user', content: 'hi' }],
    [{ role: 'assistant', content: '你喜欢爬山吧?' }, { role: 'user', content: '是的' }],
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

// ── parity 夹具:expire(纯规则;用真 TS expire + SqliteCognitionStore;固定 now/认知集 → 过期结果)——P2-2 ──
function parityExpire() {
  const store = new SqliteCognitionStore(':memory:');
  const now = new Date('2026-02-01T00:00:00.000Z');
  const daysAgo = (d) => new Date(now.getTime() - d * 86400000).toISOString();
  const mk = (id, contentType, updatedAt, extra = {}) => ({
    id, subjectId: 'owner', content: `内容 ${id}`, contentType, formedBy: 'stated',
    confidence: 300, credStatus: 'low', scope: null, validAt: null, invalidAt: null,
    askedAt: null, archivedAt: null, mutedAt: null, createdAt: updatedAt, updatedAt, ...extra,
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
  const invalidIds = store.all('owner').filter((c) => c.invalidAt != null).map((c) => c.id).sort();
  store.close();
  return {
    note: 'expire 纯规则:临时类(state7/hypothesis14/trend30)超阈标 invalid(严格 >)、fact/preference 不列永不过期、归档 active 排除不碰',
    now: now.toISOString(),
    expired: result.expired,
    invalidIds,
  };
}

// ── parity 夹具:SQLite schema(从 openStores 真建库 dump 权威结构;供 Python 建同构表对拍) ──
//   dump 逐表列结构(pragma_table_info,驱动无关 → node:sqlite/better-sqlite3 一致)+ user_version。
function buildSchema() {
  const stores = openStores(':memory:');
  const db = stores.db;
  try {
    const tables = db
      .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
      .all()
      .map((r) => r.name);
    const uv = db.prepare('PRAGMA user_version').get();
    const out = { note: '从 openStores(:memory:) dump 的权威持久化 schema;Python 建同构表后逐表对拍。', userVersion: Number(uv.user_version), tables: {} };
    for (const t of tables) {
      // pragma_table_info 表值函数;t 来自 sqlite_master(非用户输入),内插安全。
      const cols = db.prepare(`SELECT name, type, "notnull" AS nn, dflt_value AS dflt, pk FROM pragma_table_info('${t}')`).all();
      out.tables[t] = cols.map((c) => ({ name: c.name, type: c.type, notnull: Number(c.nn), dflt: c.dflt ?? null, pk: Number(c.pk) }));
    }
    return out;
  } finally {
    stores.close();
  }
}

// ── parity 夹具:FTS5 trigram bm25 排序(golden 只锁【id 排序】,不锁 bm25 分数——分数随 SQLite 小版本微动,
//   排序在清晰分隔的数据上稳定;已实测 node:sqlite 3.51 与 CPython 3.50 逐位一致,见 fts5-trigram-cross-lang-parity)。
function buildFtsGolden() {
  const stores = openStores(':memory:');
  const db = stores.db;
  try {
    db.exec("CREATE VIRTUAL TABLE cognition_fts USING fts5(cognition_id UNINDEXED, text, tokenize='trigram')");
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
        .prepare('SELECT cognition_id FROM cognition_fts WHERE cognition_fts MATCH ? ORDER BY bm25(cognition_fts) LIMIT 10')
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
    id, subjectId: 'owner', sourceKind: 'spoken', hostId: 'local', originId: null,
    occurredAt: '2026-01-01T00:00:00.000Z', recordedAt: '2026-01-01T00:00:01.000Z',
    rawContent: `原话 ${id}`, summary: `摘要 ${id}`,
    allowLocalRead: true, allowCloudRead: false, allowInference: true, correctsEvidenceId: null, ...extra,
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
      events: [{ id: 'evt-1', subjectId: 'owner', summary: '一个事件', occurredAt: '2026-01-01T00:00:00.000Z', createdAt: '2026-01-01T00:00:02.000Z', consolidated: true }],
      eventEvidence: [{ eventId: 'evt-1', evidenceId: 'ev-1' }, { eventId: 'evt-1', evidenceId: 'ev-2' }],
      cognitions: [{
        id: 'cog-1', subjectId: 'owner', content: '用户喜欢 X', contentType: 'preference', formedBy: 'stated',
        confidence: 600, credStatus: 'limited', scope: null, validAt: null, invalidAt: null,
        askedAt: null, archivedAt: null, mutedAt: null, createdAt: '2026-01-01T00:00:03.000Z', updatedAt: '2026-01-01T00:00:03.000Z',
      }],
      cognitionEvidence: [{ cognitionId: 'cog-1', evidenceId: 'ev-1', relation: 'support' }],
      unconsolidatedEventIds: [],
      interactionContexts: [{ id: 'ic-1', subjectId: 'owner', conversationId: 'conv-1', episodeId: 'ep-1', context: [{ role: 'user', content: 'hi' }], contextHash: 'abc123', createdAt: '2026-01-01T00:00:00.500Z' }],
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
  const clone = (mut) => { const b = structuredClone(good); mut(b); return b; };
  const cases = [
    { label: 'valid', bundle: good },
    { label: 'not-object', bundle: 42 },
    { label: 'wrong-format', bundle: clone((b) => { b.format = 'nope'; }) },
    { label: 'schemaVersion-missing', bundle: clone((b) => { delete b.schemaVersion; }) },
    { label: 'schemaVersion-too-high', bundle: clone((b) => { b.schemaVersion = 99; }) },
    { label: 'schemaVersion-lower', bundle: clone((b) => { b.schemaVersion = 1; }) },
    { label: 'subjectId-missing', bundle: clone((b) => { delete b.subjectId; }) },
    { label: 'data-missing', bundle: clone((b) => { delete b.data; }) },
    { label: 'evidence-not-array', bundle: clone((b) => { b.data.evidence = {}; }) },
    { label: 'evidence-missing-id', bundle: clone((b) => { b.data.evidence[0].id = ''; }) },
    { label: 'duplicate-evidence-id', bundle: clone((b) => { b.data.evidence[1].id = 'ev-1'; }) },
    { label: 'dangling-eventEvidence', bundle: clone((b) => { b.data.eventEvidence[0].evidenceId = 'ghost'; }) },
    { label: 'dangling-cognitionEvidence', bundle: clone((b) => { b.data.cognitionEvidence[0].cognitionId = 'ghost'; }) },
    { label: 'subject-mismatch-warning', bundle: clone((b) => { b.data.evidence[0].subjectId = 'other'; }) },
    { label: 'corrects-out-of-bundle-warning', bundle: clone((b) => { b.data.evidence[0].correctsEvidenceId = 'ghost'; }) },
    { label: 'unconsolidated-unknown-warning', bundle: clone((b) => { b.data.unconsolidatedEventIds = ['ghost']; }) },
  ].map((c) => ({ label: c.label, bundle: c.bundle, expected: validateBundle(c.bundle) }));

  return { bundle: good, validate: { note: 'expected 由 TS validateBundle 现算(en);Python validate 逐字对拍。', cases } };
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
    'parity/evidence-auth.json': parityEvidenceAuth(),
    'parity/cognition-order.json': parityCognitionOrder(),
    'parity/source-label.json': paritySourceLabel(),
    'parity/context-hash.json': parityContextHash(),
    'parity/expire.json': parityExpire(),
    'parity/schema.json': buildSchema(),
    'parity/fts.json': buildFtsGolden(),
    ...(() => { const bf = buildBundleFixtures(); return { 'parity/bundle.json': bf.bundle, 'parity/bundle-validate.json': bf.validate }; })(),
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
    console.log('shared/ 资产已刷新(config-constants + prompts + 8 份 parity 夹具:含 schema/fts)。');
  }
}
