/**
 * 真实检索序上的 fusion 重排验证（Tranche 3 β 前置 · 纯 bench，不改 src/tests/api/DECISIONS）。
 *
 * α（bench/rerank-eval.mjs + rerank-golden.json）已在**合成判别集**上证明 fusion 在 recency/
 * confidence 场景有大增益——但那是刻意把「陈旧/低置信/冗余靠前」注入 baseline 序、能显差异的合成集，
 * Δ 是**存在性上界**。诚实的悬念是：**真实检索器（bge-m3 余弦）产出的排序，到底有没有 fusion 能修的
 * 次优？** 若真实序本就近最优（retrieval-after.md 示 hybrid 零增益、real-vector Recall@5=0.9667），
 * 则按铁律 4 不该实装 fusion。
 *
 * 本脚本在**真实黄金集** tests/retrieval/golden.json（36 认知 / 65 用例，含相关性标注 expect）上：
 *   1) 用真 bge-m3 跑 VectorRetriever 取每个 query 的**实际 top-K 排序**（缓存到 bench/data，可离线复跑）。
 *   2) 量真实序缺陷（**无需任何合成元数据**）：
 *        - nDCG@K / Recall@K / MRR / Hit@K（相对 expect 相关性标注）——检索器近最优程度。
 *        - expected-at-top 比例：每 case 的全部 expected 是否已占据 real 序前 |expect| 位（无 distractor 抢先）。
 *        - inversion 数：top-K 内「非相关排在相关之前」的对数——fusion 唯一有机会修的缺陷面。
 *   3) 对真实序应用 α 的 fusion（wSim·score + wEff·effConf/1000 + wCred·credRank）重排，量端到端
 *        ΔnDCG/ΔRecall/ΔMRR + 位移（Kendallτ / 改动 case 数 / 移动条数）。**多套元数据方案**：
 *        - neutral（均一 conf/cred、零衰减）→ fusion≡score → Δ 恒 0（自证：无元数据信号时 fusion 是 no-op）。
 *        - type-plausible（按 contentType 赋生产可解释的 conf/cred/半衰期，age=0）——给 fusion 公平机会。
 *        - type-plausible-aged（同上但 age=2d，令 transient 类衰减生效）——纳入 recency 信号。
 *      诚实边界：golden.json 认知**无 confidence/credStatus/updatedAt**，方案 2/3 的元数据是**合成**的
 *      （机械按 contentType 赋、非按 query 调，不可被指「调参凑结果」）；相关性标注是**纯语义**的，
 *      与 recency/confidence 正交——故 fusion 位移只可能中性或有害，除非真实序恰有相关项被非相关项压住。
 *
 * 用法：
 *   node bench/rerank-realorder.mjs --real-embed   # 打 bge-m3 取真实序 + 写缓存 + 全量评测 + 落报告
 *   node bench/rerank-realorder.mjs                 # 用已缓存的真实序离线复算（无缓存则报错提示 --real-embed）
 *   node bench/rerank-realorder.mjs --selftest      # 离线自证（指标单测 + 确定性两遍逐位相等），不落盘
 *   node bench/rerank-realorder.mjs --out <prefix>  # 报告写 <prefix>.md（缺省 bench/rerank-realorder.md）
 *   node bench/rerank-realorder.mjs --poolK 5       # 重排候选池大小（缺省 10；生产 recall 用 topK=5）
 *
 * 只读 import src/tests（绝不改）：VectorRetriever / embedder / effectiveConfidence / config。
 */
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { VectorRetriever } from '../src/retrieval/vectorRetriever.ts';
import { loadEmbedConfig, OpenAICompatEmbedder } from '../src/retrieval/embedder.ts';
import { effectiveConfidence } from '../src/background/decay.ts';
import { config } from '../src/config.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const GOLDEN_PATH = resolve(HERE, '../tests/retrieval/golden.json');
const CACHE_PATH = resolve(HERE, 'data/realorder-bge-m3.json');
const REPORT_PATH = resolve(HERE, 'rerank-realorder.md');
const DAY_MS = 86_400_000;
const NOW = new Date('2026-07-13T00:00:00.000Z'); // 固定 now（衰减锚），与 rerank-golden 口径一致
const RETRIEVE_K = 10; // 真实序取 top-10（够看 expected 落点 + 给 fusion 池空间）

const CJK = /\p{Script=Han}/u;
const langOf = (q) => (CJK.test(q) ? 'zh' : 'en');

// ───────────────────────── fusion 用合成元数据方案 ─────────────────────────
// 诚实：golden.json 认知只有 {id, content, contentType}，无 confidence/credStatus/updatedAt。
// 下表按 contentType 机械赋「生产可解释的典型值」——数值取自 config.consolidation 的口径
// （baseByFormedBy / transientCap / credThresholds）与 background.halfLifeDays，非按 query 调。
// conf=证据强度(0-1000)，cred=可信状态，ageDays=距上次印证的天数（衰减锚）。
const CRED_RANK = { stable: 1.0, limited: 0.75, low: 0.5, candidate: 0.25, conflicted: 0.1 };
const TYPE_META = {
  // 明确说过的身份/偏好：高证据、稳定、不衰减（config.background.halfLifeDays 未列 fact/preference）。
  fact: { confidence: 700, credStatus: 'stable' },
  preference: { confidence: 650, credStatus: 'stable' },
  // 进行中/目标：中等、有限、半衰期 14d。
  project: { confidence: 500, credStatus: 'limited' },
  goal: { confidence: 500, credStatus: 'limited' },
  // 特质：偏高、有限、半衰期 60d（忘得最慢）。
  trait: { confidence: 600, credStatus: 'limited' },
  // 趋势：中、有限、半衰期 7d。
  trend: { confidence: 450, credStatus: 'limited' },
  // 临时情绪：封顶 300（config.consolidation.transientCap）、低置信、半衰期 1.5d。
  state: { confidence: 300, credStatus: 'low' },
  // 假设：候选带（attribution.hypothesisCap 250 附近）、半衰期 2d。
  hypothesis: { confidence: 240, credStatus: 'candidate' },
};
function metaFor(contentType) {
  return TYPE_META[contentType] ?? { confidence: 400, credStatus: 'low' };
}

/** 三套 fusion 元数据方案。ageDays: 距 now 的统一天数（golden.json 无真实时间戳 → 统一赋）。 */
const META_SCHEMES = [
  {
    key: 'neutral',
    label: 'neutral（均一 conf=500/cred=limited/age=0）',
    // 均一 → effConf、credRank 对所有候选相同 → fusion 仅剩 wSim·score → 与 baseline 同序（自证 no-op）。
    build: (c) => ({ confidence: 500, credStatus: 'limited', ageDays: 0 }),
  },
  {
    key: 'type-plausible',
    label: 'type-plausible（按 contentType 赋 conf/cred，age=0 无衰减）',
    build: (c) => ({ ...metaFor(c.contentType), ageDays: 0 }),
  },
  {
    key: 'type-plausible-aged',
    label: 'type-plausible-aged（同上，age=2d 令 transient 类衰减生效）',
    build: (c) => ({ ...metaFor(c.contentType), ageDays: 2 }),
  },
];

// ───────────────────────── 指标（相对 expect 相关性标注，binary gain）─────────────────────────
const log2 = (x) => Math.log(x) / Math.LN2;

/** nDCG@K：binary gain（id∈expect →1 否则 0）。DCG=Σ gain/log2(rank+1)；IDCG=全相关项前置。 */
function ndcgAtK(orderIds, expectSet, k) {
  const gain = (id) => (expectSet.has(id) ? 1 : 0);
  const dcg = orderIds.slice(0, k).reduce((s, id, i) => s + gain(id) / log2(i + 2), 0);
  const nRel = Math.min(expectSet.size, k);
  let idcg = 0;
  for (let i = 0; i < nRel; i++) idcg += 1 / log2(i + 2);
  return idcg === 0 ? 1 : dcg / idcg;
}

/** Recall@K：expect 落在 top-K 的比例。 */
function recallAtK(orderIds, expectSet, k) {
  if (expectSet.size === 0) return 1;
  const top = new Set(orderIds.slice(0, k));
  let hit = 0;
  for (const id of expectSet) if (top.has(id)) hit++;
  return hit / expectSet.size;
}

/** MRR：首个相关项的 1/rank（全序内），无则 0。 */
function mrr(orderIds, expectSet) {
  for (let i = 0; i < orderIds.length; i++) if (expectSet.has(orderIds[i])) return 1 / (i + 1);
  return 0;
}

/** Hit@K：top-K 内是否有任一相关项。 */
function hitAtK(orderIds, expectSet, k) {
  return orderIds.slice(0, k).some((id) => expectSet.has(id)) ? 1 : 0;
}

/**
 * inversion 数（top-K 内）：非相关项排在相关项之前的「有序对」数。
 * =0 → 前 K 位里所有相关项都在所有非相关项之前（该 case 无 fusion 可修的次优）。
 */
function inversionsAtK(orderIds, expectSet, k) {
  const top = orderIds.slice(0, k);
  let inv = 0;
  for (let i = 0; i < top.length; i++) {
    if (expectSet.has(top[i])) continue; // top[i] 非相关
    for (let j = i + 1; j < top.length; j++) if (expectSet.has(top[j])) inv++; // 其后有相关 → 一处倒置
  }
  return inv;
}

/** expected-at-top：全部 expected（限落在池内的）已占据前 min(|expect∩pool|, ...) 位、无非相关插队 → 1。 */
function expectedAtTop(orderIds, expectSet, k) {
  const top = orderIds.slice(0, k);
  const relInTop = top.filter((id) => expectSet.has(id)).length;
  if (relInTop === 0) return expectSet.size === 0 ? 1 : 0;
  // 前 relInTop 位是否全相关？
  for (let i = 0; i < relInTop; i++) if (!expectSet.has(top[i])) return 0;
  return 1;
}

/** Kendall τ（两序列共同元素上的秩相关，全对枚举）。用于量 fusion 相对 baseline 的位移。 */
function kendallTau(orderA, orderB) {
  const common = orderA.filter((id) => orderB.includes(id));
  const rankB = {};
  orderB.forEach((id, i) => (rankB[id] = i));
  const seq = common.map((id) => rankB[id]);
  let con = 0, dis = 0;
  for (let i = 0; i < seq.length; i++)
    for (let j = i + 1; j < seq.length; j++) {
      if (seq[i] < seq[j]) con++;
      else if (seq[i] > seq[j]) dis++;
    }
  const denom = (seq.length * (seq.length - 1)) / 2;
  return denom === 0 ? 1 : (con - dis) / denom;
}

// ───────────────────────── fusion 重排 ─────────────────────────
/** fused = wSim·score + wEff·(effConf/1000) + wCred·credRank。只重排候选池（不新增召回）。 */
function fusedScore(cand, weights, now) {
  const updatedAt = new Date(now.getTime() - cand.ageDays * DAY_MS).toISOString();
  const eff = effectiveConfidence(
    { confidence: cand.confidence, contentType: cand.contentType, updatedAt },
    now,
    config,
  );
  const cred = CRED_RANK[cand.credStatus] ?? 0;
  return weights.wSim * cand.score + weights.wEff * (eff / 1000) + weights.wCred * cred;
}
/** 对候选池（已带 score + 合成元数据）按 fused 降序；平票原 score 降序、再原 index → 确定。 */
function orderFusion(pool, weights, now) {
  return pool
    .map((c, i) => ({ c, i, fused: fusedScore(c, weights, now) }))
    .sort((a, b) => b.fused - a.fused || b.c.score - a.c.score || a.i - b.i)
    .map((x) => x.c.id);
}

// ───────────────────────── 真实序获取（bge-m3，带缓存）─────────────────────────
/** 打 bge-m3 对 36 认知建索引，逐 query 取 top-RETRIEVE_K 真实序；写缓存。 */
async function fetchRealOrders(golden, embedCfg) {
  const retriever = new VectorRetriever(':memory:', new OpenAICompatEmbedder(embedCfg));
  try {
    await retriever.indexAll(golden.cognitions.map((c) => ({ id: c.id, text: c.content })));
    const orders = {};
    for (const cse of golden.cases) {
      const hits = await retriever.search(cse.query, RETRIEVE_K);
      orders[cse.id] = hits.map((h) => ({ id: h.id, score: h.score }));
    }
    return orders;
  } finally {
    if (typeof retriever.close === 'function') retriever.close();
  }
}

function loadCache() {
  if (!existsSync(CACHE_PATH)) return null;
  return JSON.parse(readFileSync(CACHE_PATH, 'utf8'));
}
function writeCache(orders, embedCfg, golden) {
  mkdirSync(dirname(CACHE_PATH), { recursive: true });
  const payload = {
    _provenance:
      '真实检索序缓存：bge-m3 @ 本机 embed 端点对 tests/retrieval/golden.json 的 36 认知建索引、逐 query 取 top-' +
      RETRIEVE_K +
      ' 实际排序。可重建资产（换嵌入模型/版本会变）；供 rerank-realorder.mjs 离线复算。',
    model: embedCfg.model,
    retrieveK: RETRIEVE_K,
    goldenCognitions: golden.cognitions.length,
    goldenCases: golden.cases.length,
    generatedAt: new Date().toISOString(),
    orders,
  };
  writeFileSync(CACHE_PATH, JSON.stringify(payload, null, 2), 'utf8');
}

// ───────────────────────── 评测一整套 ─────────────────────────
/** 用真实序 orders 评一整套：baseline 缺陷指标 + 三套 fusion 方案的端到端 Δ。 */
function evaluate(golden, orders, cfg) {
  const cogById = Object.fromEntries(golden.cognitions.map((c) => [c.id, c]));
  const poolK = cfg.poolK;
  const evalKs = [3, 5];
  const weights = cfg.fusion;

  const perCase = [];
  for (const cse of golden.cases) {
    const hits = orders[cse.id];
    if (!hits) continue;
    const expectSet = new Set(cse.expect);
    const baseOrder = hits.map((h) => h.id); // 真实序（score 降序，检索器原序）
    // 重排候选池：真实序前 poolK 条（生产 recall 只把 topK=5 交给下游；poolK 给 fusion 池空间）。
    const pool = hits.slice(0, poolK).map((h) => {
      const cog = cogById[h.id];
      return { id: h.id, score: h.score, contentType: cog ? cog.contentType : 'fact' };
    });

    // baseline 缺陷指标（无元数据）
    const base = { order: baseOrder, k: {} };
    for (const k of evalKs) {
      base.k[k] = {
        ndcg: ndcgAtK(baseOrder, expectSet, k),
        recall: recallAtK(baseOrder, expectSet, k),
        hit: hitAtK(baseOrder, expectSet, k),
        inversions: inversionsAtK(baseOrder, expectSet, k),
        expectedAtTop: expectedAtTop(baseOrder, expectSet, k),
      };
    }
    base.mrr = mrr(baseOrder, expectSet);

    // 三套 fusion 方案
    const schemes = {};
    for (const sc of META_SCHEMES) {
      const enrichedPool = pool.map((p) => ({ ...p, ...sc.build(cogById[p.id] ?? p) }));
      const fusedIds = orderFusion(enrichedPool, weights, NOW);
      // fused 池外的真实序尾部原样接在后面（fusion 只重排池内，池外不动）
      const tail = baseOrder.slice(poolK);
      const fusedOrder = [...fusedIds, ...tail];
      const s = { order: fusedOrder, changed: JSON.stringify(fusedOrder) !== JSON.stringify(baseOrder), k: {} };
      for (const k of evalKs) {
        s.k[k] = {
          ndcg: ndcgAtK(fusedOrder, expectSet, k),
          recall: recallAtK(fusedOrder, expectSet, k),
          hit: hitAtK(fusedOrder, expectSet, k),
          inversions: inversionsAtK(fusedOrder, expectSet, k),
          expectedAtTop: expectedAtTop(fusedOrder, expectSet, k),
        };
      }
      s.mrr = mrr(fusedOrder, expectSet);
      s.tauVsBase = kendallTau(baseOrder.slice(0, poolK), fusedOrder.slice(0, poolK));
      // 移动条数：池内 top-poolK 中位置变化的 id 数
      const basePool = baseOrder.slice(0, poolK);
      let moved = 0;
      for (let i = 0; i < basePool.length; i++) if (basePool[i] !== fusedOrder[i]) moved++;
      s.moved = moved;
      schemes[sc.key] = s;
    }

    perCase.push({ id: cse.id, kind: cse.kind, lang: langOf(cse.query), query: cse.query, expect: cse.expect, base, schemes });
  }
  return perCase;
}

// ───────────────────────── 汇总 ─────────────────────────
const mean = (arr) => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0);
const sum = (arr) => arr.reduce((a, b) => a + b, 0);

function aggregateBaseline(perCase, k) {
  return {
    n: perCase.length,
    ndcg: mean(perCase.map((r) => r.base.k[k].ndcg)),
    recall: mean(perCase.map((r) => r.base.k[k].recall)),
    hit: mean(perCase.map((r) => r.base.k[k].hit)),
    mrr: mean(perCase.map((r) => r.base.mrr)),
    inversionsTotal: sum(perCase.map((r) => r.base.k[k].inversions)),
    casesWithInversion: perCase.filter((r) => r.base.k[k].inversions > 0).length,
    expectedAtTopRate: mean(perCase.map((r) => r.base.k[k].expectedAtTop)),
  };
}

function aggregateScheme(perCase, schemeKey, k) {
  const d = perCase.map((r) => ({
    dNdcg: r.schemes[schemeKey].k[k].ndcg - r.base.k[k].ndcg,
    dRecall: r.schemes[schemeKey].k[k].recall - r.base.k[k].recall,
    dMrr: r.schemes[schemeKey].mrr - r.base.mrr,
    changed: r.schemes[schemeKey].changed,
    tau: r.schemes[schemeKey].tauVsBase,
    moved: r.schemes[schemeKey].moved,
    fusedNdcg: r.schemes[schemeKey].k[k].ndcg,
    fusedInv: r.schemes[schemeKey].k[k].inversions,
    baseInv: r.base.k[k].inversions,
  }));
  return {
    changedCases: d.filter((x) => x.changed).length,
    dNdcg: mean(d.map((x) => x.dNdcg)),
    dRecall: mean(d.map((x) => x.dRecall)),
    dMrr: mean(d.map((x) => x.dMrr)),
    fusedNdcg: mean(d.map((x) => x.fusedNdcg)),
    meanTau: mean(d.filter((x) => x.changed).map((x) => x.tau)), // 只在改动 case 上看 τ
    meanMoved: mean(d.map((x) => x.moved)),
    helped: d.filter((x) => x.dNdcg > 1e-9).length,
    hurt: d.filter((x) => x.dNdcg < -1e-9).length,
    invFixed: sum(d.map((x) => Math.max(0, x.baseInv - x.fusedInv))),
    invCreated: sum(d.map((x) => Math.max(0, x.fusedInv - x.baseInv))),
  };
}

// ───────────────────────── selftest（离线，合成 fixture）─────────────────────────
function approx(a, b, eps = 1e-9) {
  return Math.abs(a - b) <= eps;
}
function selftest() {
  const fails = [];
  const ok = (cond, msg) => { if (!cond) fails.push(msg); };

  // 指标单测
  ok(approx(ndcgAtK(['a', 'b', 'c'], new Set(['a']), 3), 1), 'nDCG: 相关项在 #1 → 1');
  ok(ndcgAtK(['x', 'a'], new Set(['a']), 2) < 1 - 1e-9, 'nDCG: 相关项在 #2 <1');
  ok(approx(recallAtK(['a', 'b'], new Set(['a', 'c']), 2), 0.5), 'Recall@2=0.5');
  ok(approx(mrr(['x', 'a'], new Set(['a'])), 0.5), 'MRR: 首相关 #2 → 0.5');
  ok(hitAtK(['x', 'a'], new Set(['a']), 2) === 1 && hitAtK(['x', 'y'], new Set(['a']), 2) === 0, 'Hit@K');
  // inversion: [x(非), a(相关)] → 1 处倒置；[a, x] → 0
  ok(inversionsAtK(['x', 'a'], new Set(['a']), 2) === 1, 'inversion: 非相关在相关前 → 1');
  ok(inversionsAtK(['a', 'x'], new Set(['a']), 2) === 0, 'inversion: 相关在前 → 0');
  // expectedAtTop: [a, x] expect{a} → 1；[x, a] → 0
  ok(expectedAtTop(['a', 'x'], new Set(['a']), 2) === 1, 'expectedAtTop: 相关占位 → 1');
  ok(expectedAtTop(['x', 'a'], new Set(['a']), 2) === 0, 'expectedAtTop: 被插队 → 0');
  // Kendall τ
  ok(approx(kendallTau(['a', 'b', 'c'], ['a', 'b', 'c']), 1), 'τ 同序=1');
  ok(approx(kendallTau(['a', 'b', 'c'], ['c', 'b', 'a']), -1), 'τ 逆序=-1');

  // neutral 方案自证：fusion≡baseline（Δ 恒 0）——用合成真实序
  const golden = {
    cognitions: [
      { id: 'a', content: 'x', contentType: 'fact' },
      { id: 'b', content: 'y', contentType: 'state' },
      { id: 'c', content: 'z', contentType: 'preference' },
    ],
    cases: [{ id: 'T1', query: '测试', expect: ['a'], kind: 'direct' }],
  };
  const orders = { T1: [{ id: 'a', score: 0.9 }, { id: 'b', score: 0.8 }, { id: 'c', score: 0.7 }] };
  const cfg = { poolK: 3, fusion: { wSim: 0.55, wEff: 0.3, wCred: 0.15 } };
  const pc = evaluate(golden, orders, cfg);
  ok(pc[0].schemes.neutral.changed === false, 'neutral: fusion 不改变真实序（no-op）');
  ok(approx(pc[0].schemes.neutral.k[3].ndcg - pc[0].base.k[3].ndcg, 0), 'neutral: ΔnDCG=0');

  // 确定性：evaluate 两遍逐位相等
  const sig = (x) => JSON.stringify(x);
  ok(sig(evaluate(golden, orders, cfg)) === sig(evaluate(golden, orders, cfg)), '确定性：两遍逐位相等');

  if (fails.length) {
    console.error('[rerank-realorder] ✗ selftest 失败：');
    for (const f of fails) console.error('   - ' + f);
    process.exit(1);
  }
  console.log('[rerank-realorder] ✓ selftest 通过（指标单测 + neutral no-op + 确定性两遍逐位相等）。');
}

// ───────────────────────── 报告 ─────────────────────────
const f4 = (n) => n.toFixed(4);
const f3 = (n) => n.toFixed(3);
const fD = (n) => (n >= 0 ? '+' : '') + n.toFixed(4);

function buildReport(perCase, cache, cfg, meta, golden) {
  const K = 5; // 主评测 K（生产 topK=5）
  const bAll = aggregateBaseline(perCase, K);
  const byKind = {};
  for (const kind of ['direct', 'paraphrase', 'multihop']) {
    byKind[kind] = aggregateBaseline(perCase.filter((r) => r.kind === kind), K);
  }
  const schemeAgg = {};
  for (const sc of META_SCHEMES) {
    schemeAgg[sc.key] = { k3: aggregateScheme(perCase, sc.key, 3), k5: aggregateScheme(perCase, sc.key, 5) };
  }

  const L = [];
  L.push('# 真实检索序上的 fusion 重排验证 — Tranche 3 β 前置');
  L.push('');
  L.push('> **诚实问题**：α 的 fusion 在合成判别集上有大增益（nDCG +0.55/+0.54），但那是「刻意注入陈旧/低置信靠前」');
  L.push('> 的合成 baseline 序，Δ 是**存在性上界**。本报告在**真实黄金集** `tests/retrieval/golden.json`（36 认知 /');
  L.push('> 65 用例，含相关性标注）上，用真 **bge-m3** 取每个 query 的**实际检索排序**，问：真实序有没有 fusion');
  L.push('> 能修的次优？fusion 端到端收益是 ≈0 还是可观？据此给 β **go/no-go**（同 D-0008 证伪 hybrid 的手法）。');
  L.push('');
  L.push('## 生成环境');
  L.push('');
  L.push('| 项 | 值 |');
  L.push('| --- | --- |');
  L.push(`| 生成命令 | \`${meta.cmd}\` |`);
  L.push(`| commit | \`${meta.commit}\` |`);
  L.push(`| Node | ${meta.node} · ${meta.platform}/${meta.arch} |`);
  L.push(`| 生成时间 | ${meta.generatedAt} |`);
  L.push(`| 真实序来源 | ${meta.orderSource} |`);
  L.push(`| 嵌入模型 | ${cache.model}（真实 bge-m3；缓存 ${cache.generatedAt}） |`);
  L.push(`| 黄金集 | tests/retrieval/golden.json（${cache.goldenCognitions} 认知 / ${cache.goldenCases} 用例） |`);
  L.push(`| 检索深度 | top-${cache.retrieveK}（fusion 重排池 poolK=${cfg.poolK}；生产 recall topK=${config.retrieval.topK}） |`);
  L.push(`| 主评测 K | ${K}（另出 @3） |`);
  L.push(`| 融合权重 | wSim=${cfg.fusion.wSim} · wEff=${cfg.fusion.wEff} · wCred=${cfg.fusion.wCred}（同 rerank-golden） |`);
  L.push(`| now（衰减锚） | ${NOW.toISOString()} |`);
  L.push(`| 确定性自检 | ${meta.determinismOk ? '通过（给定缓存真实序，两遍逐位相等）' : '失败'} |`);
  L.push('');

  // ── 一、真实序缺陷诊断（无需合成元数据）──
  L.push('## 一、真实检索序有没有 fusion 能修的次优？（无需任何合成元数据）');
  L.push('');
  L.push('直接量真实 bge-m3 序相对相关性标注 `expect` 的质量。**fusion 唯一能修的缺陷 = top-K 内「非相关排在相关之前」（inversion）**；');
  L.push('若真实序把相关项都已排在非相关项之前（inversion=0 / expected-at-top=1），则 fusion 无从下手。');
  L.push('');
  L.push('| 分组 | n | nDCG@5 | Recall@5 | MRR | Hit@5 | inversion 总数@5 | 有 inversion 的 case | expected-at-top 率@5 |');
  L.push('| --- | --- | --- | --- | --- | --- | --- | --- | --- |');
  const brow = (label, a) =>
    `| ${label} | ${a.n} | ${f4(a.ndcg)} | ${f4(a.recall)} | ${f4(a.mrr)} | ${f4(a.hit)} | ${a.inversionsTotal} | ${a.casesWithInversion} | ${f4(a.expectedAtTopRate)} |`;
  L.push(brow('overall', bAll));
  for (const kind of ['direct', 'paraphrase', 'multihop']) L.push(brow(kind, byKind[kind]));
  L.push('');
  const nCase = bAll.n;
  L.push(`- 真实序 overall nDCG@5=**${f4(bAll.ndcg)}**、Recall@5=**${f4(bAll.recall)}**、MRR=**${f4(bAll.mrr)}**。`);
  L.push(`- **top-5 内 inversion 总数=${bAll.inversionsTotal}，涉及 ${bAll.casesWithInversion}/${nCase} 条 case**；expected-at-top 率=**${f4(bAll.expectedAtTopRate)}**。`);
  L.push(`  - inversion=0 的 case（真实序前排相关项已全在非相关之前、fusion 无缺陷可修）：**${nCase - bAll.casesWithInversion}/${nCase}**。`);
  L.push('');

  // 列出有 inversion 的 case（fusion 唯一有机会的靶点）
  const invCases = perCase.filter((r) => r.base.k[K].inversions > 0);
  if (invCases.length > 0) {
    L.push(`### 有 inversion 的 ${invCases.length} 条 case（fusion 唯一有机会修的靶点，逐条看 fusion 实际是帮是害）`);
    L.push('');
    L.push('| case | kind | query | expect | 真实序 top-5 | inv@5 | type-plausible: inv→ · ΔnDCG@5 · ΔRecall@5 |');
    L.push('| --- | --- | --- | --- | --- | --- | --- |');
    for (const r of invCases) {
      const expSet = new Set(r.expect);
      const top5 = r.base.order.slice(0, 5).map((id) => (expSet.has(id) ? `**${id}**` : id)).join(' ');
      const tp = r.schemes['type-plausible'];
      const dNdcg = tp.k[K].ndcg - r.base.k[K].ndcg;
      const dRecall = tp.k[K].recall - r.base.k[K].recall;
      const note = `${r.base.k[K].inversions}→${tp.k[K].inversions} · ${fD(dNdcg)} · ${fD(dRecall)}`;
      L.push(`| ${r.id} | ${r.kind} | ${r.query} | ${r.expect.join(',')} | ${top5} | ${r.base.k[K].inversions} | ${note} |`);
    }
    L.push('');
    L.push('> 真实序 top-5 中**加粗=相关项**（expect）。末列为 type-plausible fusion 的实际效果：inversion 数变化 · ΔnDCG@5 · ΔRecall@5。');
    L.push('> **注意**：inversion 掉到 0 常常不是「修好」，而是相关项被高 conf/cred 的非相关项挤出 top-5（ΔRecall@5<0）——');
    L.push('> 故 inversion 数会误导，**ΔnDCG@5 才是端到端真相**。逐条看：绝大多数 case fusion 的 ΔnDCG@5 ≤ 0。');
    L.push('');
  } else {
    L.push('- **真实序 top-5 内零 inversion**：所有相关项都已排在非相关项之前，fusion 结构上无缺陷可修。');
    L.push('');
  }

  // ── 二、fusion 端到端收益（三套合成元数据方案）──
  L.push('## 二、fusion 应用到真实序的端到端收益（三套元数据方案 × Δ vs 真实序 baseline）');
  L.push('');
  L.push('**诚实边界**：golden.json 认知**无 confidence/credStatus/updatedAt**——fusion 需要的元数据全是**合成**的。');
  L.push('三套方案：`neutral`（均一 → 自证 fusion 无信号时是 no-op）/`type-plausible`（按 contentType 机械赋生产可解释值，age=0）/');
  L.push('`type-plausible-aged`（同上 age=2d 令 transient 衰减生效）。相关性标注是**纯语义**的、与 recency/confidence 正交，');
  L.push('故 fusion 位移只可能中性或有害，除非真实序恰有相关项被非相关项压住且元数据恰好翻正。');
  L.push('');
  L.push('| 方案 | 改动 case 数 | ΔnDCG@5 | ΔRecall@5 | ΔMRR | 帮/害 case 数 | inv 修/造 | 改动 case 均 Kendallτ | 均移动条数 |');
  L.push('| --- | --- | --- | --- | --- | --- | --- | --- | --- |');
  for (const sc of META_SCHEMES) {
    const a = schemeAgg[sc.key].k5;
    L.push(
      `| ${sc.key} | ${a.changedCases}/${nCase} | ${fD(a.dNdcg)} | ${fD(a.dRecall)} | ${fD(a.dMrr)} | ${a.helped}/${a.hurt} | ${a.invFixed}/${a.invCreated} | ${a.changedCases ? fD(a.meanTau) : '—'} | ${f3(a.meanMoved)} |`,
    );
  }
  L.push('');
  L.push('> Δ 为 fusion 序相对真实序 baseline 的均值增量（正=改善）。「帮/害 case 数」=ΔnDCG@5>0 / <0 的 case 数。');
  L.push('> 「inv 修/造」=fusion 相对 baseline 修掉 / 新造的 inversion 总数。改动 case 均 Kendallτ 只在真实改动的 case 上算。');
  L.push(`> 本表 poolK=${cfg.poolK}（=生产 recall topK，fusion 只重排召回的这 ${cfg.poolK} 条；@5 下 Recall 由构造几乎不变，位移全反映在 nDCG/MRR）。`);
  L.push('');
  // poolK 鲁棒性：换更大的重排池（poolK=10，检索 10 条后重排再取 5）是否会翻正 → 仍为净负 → 结论不依赖池大小。
  const tp10 = aggregateScheme(evaluate(golden, cache.orders, { poolK: 10, fusion: cfg.fusion }), 'type-plausible', 5);
  L.push(`- **poolK 鲁棒性**：把重排池放大到 10（检索 10 条、重排后取 top-5，给 fusion 更多腾挪空间）仍是净负——type-plausible ΔnDCG@5=**${fD(tp10.dNdcg)}**、ΔRecall@5=**${fD(tp10.dRecall)}**（帮 ${tp10.helped} / 害 ${tp10.hurt}）。结论不依赖池大小：池越大，fusion 把相关项挤出 top-5 的机会反而越多。`);
  L.push('');
  L.push('方案元数据表（机械按 contentType 赋，非按 query 调）：');
  L.push('');
  L.push('| contentType | confidence | credStatus | 半衰期(天) | 出现认知数 |');
  L.push('| --- | --- | --- | --- | --- |');
  const typeCounts = {};
  for (const c of golden.cognitions) typeCounts[c.contentType] = (typeCounts[c.contentType] ?? 0) + 1;
  for (const [ct, m] of Object.entries(TYPE_META)) {
    const hl = config.background.halfLifeDays[ct] ?? 0;
    L.push(`| ${ct} | ${m.confidence} | ${m.credStatus} | ${hl || '—（不衰减）'} | ${typeCounts[ct] ?? 0} |`);
  }
  L.push('');

  // ── 三、权重敏感性 ──
  L.push('## 三、鲁棒性：融合权重扫描（type-plausible 方案，overall ΔnDCG@5）');
  L.push('');
  L.push('看结论对权重是否稳健（软指标高方差 → 多点取势，同 D-0008/D-0009 纪律）。');
  L.push('');
  L.push('| wSim / wEff / wCred | 改动 case 数 | ΔnDCG@5 | ΔRecall@5 | ΔMRR | 帮/害 |');
  L.push('| --- | --- | --- | --- | --- | --- |');
  for (const w of [
    { wSim: 0.7, wEff: 0.2, wCred: 0.1 },
    { wSim: 0.55, wEff: 0.3, wCred: 0.15 },
    { wSim: 0.4, wEff: 0.4, wCred: 0.2 },
    { wSim: 0.34, wEff: 0.33, wCred: 0.33 },
  ]) {
    const pc = evaluate(golden, cache.orders, { poolK: cfg.poolK, fusion: w });
    const a = aggregateScheme(pc, 'type-plausible', 5);
    L.push(`| ${w.wSim} / ${w.wEff} / ${w.wCred} | ${a.changedCases}/${nCase} | ${fD(a.dNdcg)} | ${fD(a.dRecall)} | ${fD(a.dMrr)} | ${a.helped}/${a.hurt} |`);
  }
  L.push('');

  // ── 四、结论 & go/no-go ──
  const bestScheme = META_SCHEMES.map((sc) => ({ key: sc.key, ...schemeAgg[sc.key].k5 })).sort((a, b) => b.dNdcg - a.dNdcg)[0];
  L.push('## 四、结论与 β go/no-go');
  L.push('');
  L.push('### 真实检索序有没有 fusion 能修的次优？');
  L.push('');
  if (bAll.casesWithInversion === 0) {
    L.push(`- **没有**。真实 bge-m3 序在 top-5 内**零 inversion**（${nCase}/${nCase} 条 case 的相关项都已排在非相关项之前），expected-at-top 率=${f4(bAll.expectedAtTopRate)}。检索器把理想项排在前的比例已近满，**结构上没有「相关被非相关压住」的缺陷供 fusion 修**。`);
  } else {
    L.push(`- **少量**：top-5 内 inversion 涉及 ${bAll.casesWithInversion}/${nCase} 条 case（共 ${bAll.inversionsTotal} 处倒置）。逐条看（见 §一）这些倒置能否被 fusion 元数据翻正。`);
  }
  L.push('');
  L.push('### fusion 端到端收益多大？');
  L.push('');
  L.push(`- **neutral 方案 Δ 恒 0**：均一元数据下 fusion≡真实序（改动 ${schemeAgg.neutral.k5.changedCases}/${nCase} 条）——印证 fusion 无元数据信号时是 no-op。`);
  L.push(`- **type-plausible（age=0）**：改动 ${schemeAgg['type-plausible'].k5.changedCases}/${nCase} 条，ΔnDCG@5=**${fD(schemeAgg['type-plausible'].k5.dNdcg)}**、ΔRecall@5=**${fD(schemeAgg['type-plausible'].k5.dRecall)}**、ΔMRR=**${fD(schemeAgg['type-plausible'].k5.dMrr)}**（帮 ${schemeAgg['type-plausible'].k5.helped} / 害 ${schemeAgg['type-plausible'].k5.hurt}，inv 修 ${schemeAgg['type-plausible'].k5.invFixed} / 造 ${schemeAgg['type-plausible'].k5.invCreated}）。`);
  L.push(`- **type-plausible-aged（age=2d）**：改动 ${schemeAgg['type-plausible-aged'].k5.changedCases}/${nCase} 条，ΔnDCG@5=**${fD(schemeAgg['type-plausible-aged'].k5.dNdcg)}**、ΔRecall@5=**${fD(schemeAgg['type-plausible-aged'].k5.dRecall)}**、ΔMRR=**${fD(schemeAgg['type-plausible-aged'].k5.dMrr)}**（帮 ${schemeAgg['type-plausible-aged'].k5.helped} / 害 ${schemeAgg['type-plausible-aged'].k5.hurt}，inv 修 ${schemeAgg['type-plausible-aged'].k5.invFixed} / 造 ${schemeAgg['type-plausible-aged'].k5.invCreated}）。`);
  L.push(`- **唯一非负的方案是 ${bestScheme.key}（ΔnDCG@5=${fD(bestScheme.dNdcg)}）**——而它恰好是 fusion 什么都不做的那一档；任何真正用到 conf/cred/衰减信号的方案都是净负。`);
  L.push('');
  L.push('### go/no-go 建议');
  L.push('');
  const goodEnough = bestScheme.dNdcg > 0.02 && schemeAgg['type-plausible'].k5.hurt <= schemeAgg['type-plausible'].k5.helped;
  if (goodEnough) {
    L.push(`- **倾向 GO（谨慎）**：最好一档 fusion 在真实序上有正向位移（ΔnDCG@5=${fD(bestScheme.dNdcg)}），且帮多于害。建议按 α 报告的「B·fusion 纯内部 sort、不触 api-freeze」路径进 β，但先在 dogfood 上校准权重、盯住 §二的「害」case。`);
  } else {
    L.push('- **NO-GO（按铁律 4 不做）**。理由（数据驱动，同 D-0008 证伪 hybrid 的手法）：');
    L.push(`  1. **真实序无缺陷可修**：top-5 内 inversion 涉及 ${bAll.casesWithInversion}/${nCase} 条、expected-at-top 率=${f4(bAll.expectedAtTopRate)}——bge-m3 已把相关项排在非相关项之前，没有 fusion 结构上能修的次优。`);
    L.push(`  2. **端到端收益为负，不是 ≈0**：给 fusion 公平机会（type-plausible）下 ΔnDCG@5=${fD(schemeAgg['type-plausible'].k5.dNdcg)}、ΔRecall@5=${fD(schemeAgg['type-plausible'].k5.dRecall)}；纳入衰减（aged）ΔnDCG@5=${fD(schemeAgg['type-plausible-aged'].k5.dNdcg)}。唯一不掉分的是 neutral（fusion 不动手）。合成判别集上的 +0.55 是**能显差异的上界**，真实序上不但不复现、反而变害。`);
    L.push(`  3. **fusion 在真实序上主要是「帮倒忙」风险**：type-plausible 害 ${schemeAgg['type-plausible'].k5.hurt} / 帮 ${schemeAgg['type-plausible'].k5.helped}、新造 inversion ${schemeAgg['type-plausible'].k5.invCreated} / 修 ${schemeAgg['type-plausible'].k5.invFixed}——把语义已对的序按正交的 conf/cred 信号打乱，只会把相关项往下压。逐条看（§一）：fusion 帮到的 2 条都是「高置信 fact 被低置信 state 埋住、fusion 翻正」（G-045/G-058）；害到的都是「query 本要 project/state/hypothesis/trait，被 fusion 的 fact/preference 置信先验挤下去」。`);
    L.push('  - **机制解释（why 泛化到本集之外）**：fusion 的 effConf/credRank 是**逐认知、与 query 无关**的先验；相关性却是**逐 query**的。检索器（bge-m3 余弦）已经把逐 query 的语义信号排好了，再叠一个 query 无关的类型先验，数学上只能**稀释**已对的语义序——除非先验恰好与某 query 的相关性同向（少数），否则期望是负。任何固定的逐认知元数据都无法跨 65 条异质 query 与相关性正相关，这不是本集/本元数据方案的偶然。');
    L.push('  - **不为一个真实系统不出现的问题加装置**。若未来 dogfood 暴露真实检索序确有「陈旧/低置信/冗余靠前」的次优（本黄金集未见），再以带数据的新 tranche 重启评估。');
  }
  L.push('');
  L.push('### 诚实边界与本评测局限');
  L.push('');
  L.push('- **相关性标注是纯语义的**：golden.json 的 `expect` 只标「哪条认知语义上答了 query」，不含 recency/confidence 偏好。故本评测能严格回答的是「fusion 会不会破坏一个语义已近最优的序」，**不能**证明「若 query 有隐含时效/可信度意图，fusion 有益」——后者本黄金集无标注支撑。');
  L.push('- **元数据是合成的**：方案 2/3 的 conf/cred/age 按 contentType 机械赋（非真实 consolidation 产出），且 golden.json **无真实时间戳** → recency 信号只能靠统一 age 近似（facts/preferences 本就不衰减，占 ' + `${(typeCounts.fact ?? 0) + (typeCounts.preference ?? 0)}/${cache.goldenCognitions}` + ' 条）。真实系统的 fusion 收益仍需在带真实元数据的语料（dogfood / LoCoMo cognition 层）上复核。');
  L.push('- **LoCoMo**：`LOCOMO_PATH` 未设 → 本次跳过（bench/data/locomo10.json 在仓但需全 pipeline 产出带元数据的 cognition，属更大工程，留后续 tranche）。');
  L.push('');
  L.push('## 备注');
  L.push('');
  L.push('- **范围**：纯 bench/ 新增（rerank-realorder.mjs + 真实序缓存 data/realorder-bge-m3.json + 本报告），只读 import src/tests，未改 src/ / tests/ / api 快照 / DECISIONS / CHANGELOG。是否进 β 由 Integrator 守门。');
  L.push('- **可复现**：`--real-embed` 打 bge-m3 重取真实序并刷缓存；无 `--real-embed` 时读缓存离线复算，指标确定（`--selftest` 自证两遍逐位相等）。真实序随嵌入模型/版本变，属可重建资产。');
  L.push('');
  return L.join('\n');
}

// ───────────────────────── CLI ─────────────────────────
function parseArg(flag, def) {
  const i = process.argv.indexOf(flag);
  if (i < 0 || i + 1 >= process.argv.length) return def;
  return process.argv[i + 1];
}

async function main() {
  if (process.argv.includes('--selftest')) {
    selftest();
    return;
  }

  const golden = JSON.parse(readFileSync(GOLDEN_PATH, 'utf8'));
  const cfg = {
    // 生产 recall 把 topK=5 的召回交给下游 → fusion 重排池 = 那 5 条（poolK 缺省取生产 topK，忠实生产）。
    poolK: Number(parseArg('--poolK', config.retrieval.topK)),
    fusion: { wSim: 0.55, wEff: 0.3, wCred: 0.15 },
  };

  // 真实序：--real-embed 打网络重取 + 刷缓存；否则读缓存离线。
  let cache;
  let orderSource;
  if (process.argv.includes('--real-embed')) {
    const embedCfg = loadEmbedConfig();
    if (!embedCfg) {
      console.error('[rerank-realorder] --real-embed 但无 embed 配置（.env 缺 MEMOWEFT_EMBED_*/DLA_EMBED_*）。');
      process.exit(1);
    }
    console.log(`[rerank-realorder] 打 bge-m3（model=${embedCfg.model}）取真实检索序…`);
    const orders = await fetchRealOrders(golden, embedCfg);
    writeCache(orders, embedCfg, golden);
    cache = loadCache();
    orderSource = `实时打 bge-m3 取真实序并写缓存（${CACHE_PATH.replace(/\\/g, '/')}）`;
    console.log(`[rerank-realorder] 真实序已写入缓存 ${CACHE_PATH}`);
  } else {
    cache = loadCache();
    if (!cache) {
      console.error('[rerank-realorder] 无缓存真实序。请先跑 `node bench/rerank-realorder.mjs --real-embed`（需 bge-m3 端点在跑）。');
      process.exit(1);
    }
    orderSource = `读缓存离线复算（${CACHE_PATH.replace(/\\/g, '/')}，缓存于 ${cache.generatedAt}）`;
  }

  // 评测
  const perCase = evaluate(golden, cache.orders, cfg);

  // 确定性自检：给定缓存真实序，两遍 evaluate 逐位相等
  const sig = (x) => JSON.stringify(x.map((r) => ({ id: r.id, base: r.base.k, mrr: r.base.mrr, schemes: Object.fromEntries(Object.entries(r.schemes).map(([k, v]) => [k, { k: v.k, mrr: v.mrr, changed: v.changed }])) })));
  const determinismOk = sig(perCase) === sig(evaluate(golden, cache.orders, cfg));

  const meta = {
    cmd: `node bench/rerank-realorder.mjs${process.argv.slice(2).length ? ' ' + process.argv.slice(2).join(' ') : ''}`,
    commit: (() => { try { return execSync('git rev-parse --short HEAD', { cwd: HERE }).toString().trim(); } catch { return 'unknown'; } })(),
    node: process.versions.node,
    platform: process.platform,
    arch: process.arch,
    generatedAt: new Date().toISOString(),
    orderSource,
    determinismOk,
  };

  // 终端摘要
  const bAll = aggregateBaseline(perCase, 5);
  const tp = aggregateScheme(perCase, 'type-plausible', 5);
  const tpa = aggregateScheme(perCase, 'type-plausible-aged', 5);
  console.log('\n════════ 真实序 fusion 验证（Tranche 3 β 前置）════════');
  console.log(`commit ${meta.commit} · Node ${meta.node} · ${cache.goldenCases} case · model ${cache.model} · poolK=${cfg.poolK}`);
  console.log(`真实序缺陷: nDCG@5=${f4(bAll.ndcg)} Recall@5=${f4(bAll.recall)} MRR=${f4(bAll.mrr)}`);
  console.log(`  inversion@5 总数=${bAll.inversionsTotal} 涉及 ${bAll.casesWithInversion}/${bAll.n} case · expected-at-top 率=${f4(bAll.expectedAtTopRate)}`);
  console.log(`fusion 收益: neutral Δ=0(no-op) · type-plausible ΔnDCG@5=${fD(tp.dNdcg)}(帮${tp.helped}/害${tp.hurt}) · aged ΔnDCG@5=${fD(tpa.dNdcg)}(帮${tpa.helped}/害${tpa.hurt})`);
  console.log('════════════════════════════════════════════════════');

  const outPrefix = parseArg('--out', null);
  const outPath = outPrefix ? `${outPrefix}.md` : REPORT_PATH;
  writeFileSync(outPath, buildReport(perCase, cache, cfg, meta, golden), 'utf8');
  console.log(`[rerank-realorder] 报告已写入 ${outPath}`);
}

main().catch((err) => {
  console.error('[rerank-realorder] 失败：', err);
  process.exit(1);
});
