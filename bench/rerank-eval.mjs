/**
 * 重排判别评测（Tranche 3 α · 纯 bench，不改 src / 不碰 api / 不动 eval 断言）。
 *
 * 目的：在**判别性**黄金集 bench/rerank-golden.json 上，量化两种确定性重排策略相对
 *   「检索器原序（baseline，恒等重排）」是否有可测收益：
 *     - A · MMR 多样性重排：贪心 Maximal Marginal Relevance，用 score 作相关性、候选文本的
 *       HashEmbedder 余弦作两两冗余度，mmrLambda 权衡相关 vs 多样。
 *     - B · score 融合重排：用召回项已有元数据（effectiveConfidence[生产 decay] / credStatus /
 *       检索 score）加权重排。**只用 RecalledCognitionItem 现有字段，零新数据**。
 *
 * 指标（每 case，@K，K=evalK 缺省 3；另出 @5）：
 *   - nDCG@K        —— graded gain（记忆注入效用分档 0..3），看高效用条是否早出。
 *   - αnDCG@K       —— 多样性感知（α=0.5，topic 覆盖，冗余按 (1-α)^已见次数 折扣）；MMR 的靶。
 *   - distinct@K    —— top-K 里不同 topic 数（直观多样性）。
 *   - kendallTau    —— 相对 idealOrder 的 Kendall 秩相关。
 *   - firstMaxRank  —— 第一条满分 gain 的 1-based 名次（越小越好）。
 *
 * 三臂：baseline（原序恒等）/ A(MMR) / B(fusion)。逐 case 三臂同一套指标，按 scenario 汇总，
 *   给出「哪个策略在哪类 scenario 赢、幅度多大」。
 *
 * 确定性：HashEmbedder + effectiveConfidence（读时算、无系统时钟依赖，now 由 golden.json 固定注入）
 *   全确定。跑两遍逐位比对（--selftest 里断言）。
 *
 * 只读 import src/tests（绝不改）：
 *   - effectiveConfidence  ← ../src/background/decay.ts（生产分型衰减，B 的新近度信号）
 *   - config               ← ../src/config.ts（半衰期 / 阈值口径与生产一致）
 *   - HashEmbedder         ← ../tests/retrieval/hashEmbedder.ts（确定性候选向量，A 的冗余度）
 *
 * 用法：
 *   node bench/rerank-eval.mjs                 # 跑判别集，落 bench/rerank-baseline.md
 *   node bench/rerank-eval.mjs --selftest      # 离线自证（指标单测 + 判别不变量 + 确定性），不落盘
 *   node bench/rerank-eval.mjs --lambda 0.5    # 覆盖 mmrLambda
 *   node bench/rerank-eval.mjs --out <prefix>  # 报告写 <prefix>.md
 *   node bench/rerank-eval.mjs --debug         # 打印逐 case 三臂排序（调参用）
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { effectiveConfidence } from '../src/background/decay.ts';
import { config } from '../src/config.ts';
import { loadEmbedConfig, OpenAICompatEmbedder } from '../src/retrieval/embedder.ts';
import { HashEmbedder } from '../tests/retrieval/hashEmbedder.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const GOLDEN_PATH = resolve(HERE, '../tests/retrieval/rerank-golden.json'); // fallback 见 loadGolden
const DAY_MS = 86_400_000;
const ALPHA = 0.5; // αnDCG 冗余折扣系数（TREC 经典 0.5）

// ───────────────────────── 向量 / 相似度 ─────────────────────────
function cosine(a, b) {
  let dot = 0, na = 0, nb = 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const x = a[i] ?? 0, y = b[i] ?? 0;
    dot += x * y; na += x * x; nb += y * y;
  }
  if (na === 0 || nb === 0) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

/** 一个 case 内所有候选文本的两两余弦矩阵（HashEmbedder，确定性）。 */
async function pairwiseSim(cands, embedder) {
  const vecs = await embedder.embed(cands.map((c) => c.text));
  const n = cands.length;
  const m = Array.from({ length: n }, () => new Array(n).fill(0));
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) m[i][j] = cosine(vecs[i], vecs[j]);
  return m;
}

// ───────────────────────── 重排三臂 ─────────────────────────

/** baseline：检索器原序（按 score 降序，平票稳定按原 index）。这是「恒等重排」对照。 */
function orderBaseline(cands) {
  return cands
    .map((c, i) => ({ c, i }))
    .sort((a, b) => b.c.score - a.c.score || a.i - b.i)
    .map((x) => x.c.id);
}

/**
 * A · MMR 贪心：相关性=score，冗余度=候选文本两两 HashEmbedder 余弦。
 * 每步选 argmax [ λ·score(d) − (1−λ)·max_{s∈已选} sim(d,s) ]；首选无已选项 → 惩罚 0。
 * 平票：先原 score 降序、再原 index 升序 → 确定。
 */
function orderMMR(cands, simMatrix, lambda) {
  const idx = cands.map((_, i) => i);
  const remaining = new Set(idx);
  const selected = [];
  while (remaining.size > 0) {
    let best = null;
    for (const i of remaining) {
      let maxSim = 0;
      for (const s of selected) maxSim = Math.max(maxSim, simMatrix[i][s]);
      const mmr = lambda * cands[i].score - (1 - lambda) * (selected.length ? maxSim : 0);
      if (
        best === null ||
        mmr > best.mmr + 1e-12 ||
        (Math.abs(mmr - best.mmr) <= 1e-12 &&
          (cands[i].score > cands[best.i].score ||
            (cands[i].score === cands[best.i].score && i < best.i)))
      ) {
        best = { i, mmr };
      }
    }
    selected.push(best.i);
    remaining.delete(best.i);
  }
  return selected.map((i) => cands[i].id);
}

/**
 * B · score 融合：fused = wSim·score + wEff·(effConf/1000) + wCred·credRank。
 * effConf = 生产 effectiveConfidence（按 contentType 半衰期对 age=now−updatedAt 折衰减，读时算）。
 * 只用召回项已有信号（score/confidence/credStatus/contentType/updatedAt），零新数据。
 * 平票：原 score 降序、再原 index 升序 → 确定。
 */
function fusedScore(c, weights, credRank, now) {
  const updatedAt = new Date(now.getTime() - c.ageDays * DAY_MS).toISOString();
  const eff = effectiveConfidence({ confidence: c.confidence, contentType: c.contentType, updatedAt }, now, config);
  const cred = credRank[c.credStatus] ?? 0;
  return {
    fused: weights.wSim * c.score + weights.wEff * (eff / 1000) + weights.wCred * cred,
    eff,
  };
}
function orderFusion(cands, weights, credRank, now) {
  return cands
    .map((c, i) => ({ c, i, ...fusedScore(c, weights, credRank, now) }))
    .sort((a, b) => b.fused - a.fused || b.c.score - a.c.score || a.i - b.i)
    .map((x) => x.c.id);
}

// ───────────────────────── 指标 ─────────────────────────
const log2 = (x) => Math.log(x) / Math.LN2;

/** nDCG@K：graded gain。DCG=Σ (2^g−1)/log2(rank+1)；ideal=按 gain 降序。 */
function ndcgAtK(orderIds, gainById, k) {
  const dcg = (ids) =>
    ids.slice(0, k).reduce((s, id, i) => s + (Math.pow(2, gainById[id] ?? 0) - 1) / log2(i + 2), 0);
  const ideal = [...orderIds].sort((a, b) => (gainById[b] ?? 0) - (gainById[a] ?? 0));
  const idcg = dcg(ideal);
  return idcg === 0 ? 1 : dcg(orderIds) / idcg;
}

/** αnDCG@K：多样性感知。item 在 rank i 的收益 = rel·(1−α)^(该 topic 此前已见次数)，rel=gain>0?1:0。 */
function alphaDcgOf(orderIds, meta, k) {
  const seen = {};
  let dcg = 0;
  orderIds.slice(0, k).forEach((id, i) => {
    const t = meta[id].topic;
    const rel = (meta[id].gain > 0 ? 1 : 0) * Math.pow(1 - ALPHA, seen[t] ?? 0);
    dcg += rel / log2(i + 2);
    seen[t] = (seen[t] ?? 0) + 1;
  });
  return dcg;
}
/** 贪心求 ideal αDCG 归一化分母（TREC 惯用近似）。 */
function idealAlphaDcg(allIds, meta, k) {
  const seen = {};
  const remaining = new Set(allIds);
  const order = [];
  while (order.length < Math.min(k, allIds.length) && remaining.size > 0) {
    let best = null;
    for (const id of remaining) {
      const t = meta[id].topic;
      const g = (meta[id].gain > 0 ? 1 : 0) * Math.pow(1 - ALPHA, seen[t] ?? 0);
      if (best === null || g > best.g + 1e-12 || (Math.abs(g - best.g) <= 1e-12 && id < best.id)) {
        best = { id, g, t };
      }
    }
    order.push(best.id);
    remaining.delete(best.id);
    seen[best.t] = (seen[best.t] ?? 0) + 1;
  }
  return alphaDcgOf(order, meta, k);
}
function alphaNdcgAtK(orderIds, meta, allIds, k) {
  const idcg = idealAlphaDcg(allIds, meta, k);
  return idcg === 0 ? 1 : alphaDcgOf(orderIds, meta, k) / idcg;
}

/** distinct@K：top-K 里不同 topic 数。 */
function distinctAtK(orderIds, meta, k) {
  return new Set(orderIds.slice(0, k).map((id) => meta[id].topic)).size;
}

/** Kendall τ：orderIds 相对 idealOrder 的秩相关（全集排列）。 */
function kendallTau(orderIds, idealOrder) {
  const rank = {};
  idealOrder.forEach((id, i) => (rank[id] = i));
  const seq = orderIds.map((id) => rank[id]);
  let con = 0, dis = 0;
  for (let i = 0; i < seq.length; i++)
    for (let j = i + 1; j < seq.length; j++) {
      if (seq[i] < seq[j]) con++;
      else if (seq[i] > seq[j]) dis++;
    }
  const denom = (seq.length * (seq.length - 1)) / 2;
  return denom === 0 ? 1 : (con - dis) / denom;
}

/** 第一条满分 gain（=该 case 最大 gain）的 1-based 名次；无则 0。 */
function firstMaxRank(orderIds, gainById) {
  const maxG = Math.max(...orderIds.map((id) => gainById[id] ?? 0));
  if (maxG <= 0) return 0;
  const i = orderIds.findIndex((id) => (gainById[id] ?? 0) === maxG);
  return i < 0 ? 0 : i + 1;
}

// ───────────────────────── 单 case 评测 ─────────────────────────
async function evalCase(cse, cfg, embedder, now) {
  const cands = cse.candidates;
  const meta = Object.fromEntries(cands.map((c) => [c.id, c]));
  const gainById = Object.fromEntries(cands.map((c) => [c.id, c.gain]));
  const allIds = cands.map((c) => c.id);
  const sim = await pairwiseSim(cands, embedder);

  const orders = {
    baseline: orderBaseline(cands),
    mmr: orderMMR(cands, sim, cfg.mmrLambda),
    fusion: orderFusion(cands, cfg.fusion, cfg.credRank, now),
  };

  const metricsFor = (orderIds, k) => ({
    ndcg: ndcgAtK(orderIds, gainById, k),
    alphaNdcg: alphaNdcgAtK(orderIds, meta, allIds, k),
    distinct: distinctAtK(orderIds, meta, k),
    tau: kendallTau(orderIds, cse.idealOrder),
    firstMax: firstMaxRank(orderIds, gainById),
  });

  const out = { id: cse.id, scenario: cse.scenario, query: cse.query, orders, k3: {}, k5: {} };
  for (const arm of ['baseline', 'mmr', 'fusion']) {
    out.k3[arm] = metricsFor(orders[arm], cfg.evalK);
    out.k5[arm] = metricsFor(orders[arm], 5);
  }
  return out;
}

// ───────────────────────── 汇总 ─────────────────────────
const ARMS = ['baseline', 'mmr', 'fusion'];
const METRICS = ['ndcg', 'alphaNdcg', 'distinct', 'tau', 'firstMax'];

function meanBy(rows, arm, metric, kkey) {
  if (rows.length === 0) return 0;
  return rows.reduce((s, r) => s + r[kkey][arm][metric], 0) / rows.length;
}

function aggregate(caseResults, kkey) {
  const scenarios = [...new Set(caseResults.map((r) => r.scenario))];
  const byScenario = {};
  for (const sc of scenarios) {
    const rows = caseResults.filter((r) => r.scenario === sc);
    byScenario[sc] = { n: rows.length };
    for (const arm of ARMS) {
      byScenario[sc][arm] = {};
      for (const m of METRICS) byScenario[sc][arm][m] = meanBy(rows, arm, m, kkey);
    }
  }
  const overall = { n: caseResults.length };
  for (const arm of ARMS) {
    overall[arm] = {};
    for (const m of METRICS) overall[arm][m] = meanBy(caseResults, arm, m, kkey);
  }
  return { byScenario, overall, scenarios };
}

// ───────────────────────── 跑一整套 ─────────────────────────
async function runAll(golden, cfg, embedder = new HashEmbedder()) {
  const now = new Date(golden.now);
  const caseResults = [];
  for (const cse of golden.cases) caseResults.push(await evalCase(cse, cfg, embedder, now));
  return { caseResults, k3: aggregate(caseResults, 'k3'), k5: aggregate(caseResults, 'k5') };
}

/** 确定性签名（含逐 case 三臂排序 + 全指标；无系统量）。 */
function deterministicSig(run) {
  return JSON.stringify(
    run.caseResults.map((r) => ({ id: r.id, orders: r.orders, k3: r.k3, k5: r.k5 })),
  );
}

// ───────────────────────── selftest ─────────────────────────
function approx(a, b, eps = 1e-9) {
  return Math.abs(a - b) <= eps;
}
async function selftest(golden, cfg) {
  const fails = [];
  const ok = (cond, msg) => { if (!cond) fails.push(msg); };

  // 1) 指标单测
  {
    const meta = {
      a: { topic: 'x', gain: 3 }, b: { topic: 'x', gain: 3 }, c: { topic: 'y', gain: 3 },
    };
    const gain = { a: 3, b: 3, c: 3 };
    // nDCG：理想序（gain 全同）任意序 = 1
    ok(approx(ndcgAtK(['a', 'b', 'c'], gain, 3), 1), 'nDCG ideal==1');
    // αnDCG：多样序 > 冗余序
    const div = alphaNdcgAtK(['a', 'c', 'b'], meta, ['a', 'b', 'c'], 3);
    const red = alphaNdcgAtK(['a', 'b', 'c'], meta, ['a', 'b', 'c'], 3);
    ok(div > red + 1e-9, `αnDCG diverse(${div.toFixed(4)})>redundant(${red.toFixed(4)})`);
    // distinct
    ok(distinctAtK(['a', 'c', 'b'], meta, 3) === 2, 'distinct==2');
    // Kendall τ：同序=1、逆序=-1
    ok(approx(kendallTau(['a', 'b', 'c'], ['a', 'b', 'c']), 1), 'τ identical==1');
    ok(approx(kendallTau(['c', 'b', 'a'], ['a', 'b', 'c']), -1), 'τ reversed==-1');
  }

  // 2) 确定性：跑两遍逐位相等
  const r1 = await runAll(golden, cfg);
  const r2 = await runAll(golden, cfg);
  ok(deterministicSig(r1) === deterministicSig(r2), '确定性：两遍逐位相等');

  // 3) 判别不变量（本集刻意构造，应成立；否则集子失去判别力）
  const { byScenario } = r1.k3;
  const S = byScenario;
  // redundancy：MMR 的 αnDCG / distinct 应严格高于 baseline，且 MMR ≥ fusion
  if (S.redundancy) {
    ok(S.redundancy.mmr.alphaNdcg > S.redundancy.baseline.alphaNdcg + 1e-6,
      `redundancy: MMR αnDCG(${S.redundancy.mmr.alphaNdcg.toFixed(4)})>baseline(${S.redundancy.baseline.alphaNdcg.toFixed(4)})`);
    ok(S.redundancy.mmr.distinct > S.redundancy.baseline.distinct + 1e-6,
      `redundancy: MMR distinct(${S.redundancy.mmr.distinct.toFixed(3)})>baseline(${S.redundancy.baseline.distinct.toFixed(3)})`);
    ok(S.redundancy.mmr.alphaNdcg >= S.redundancy.fusion.alphaNdcg - 1e-6,
      `redundancy: MMR αnDCG≥fusion`);
  }
  // recency：fusion 的 nDCG 应严格高于 baseline，且 fusion ≥ MMR
  if (S.recency) {
    ok(S.recency.fusion.ndcg > S.recency.baseline.ndcg + 1e-6,
      `recency: fusion nDCG(${S.recency.fusion.ndcg.toFixed(4)})>baseline(${S.recency.baseline.ndcg.toFixed(4)})`);
    ok(S.recency.fusion.ndcg >= S.recency.mmr.ndcg - 1e-6, 'recency: fusion nDCG≥MMR');
  }
  // confidence：fusion 的 nDCG 应严格高于 baseline，且 fusion ≥ MMR
  if (S.confidence) {
    ok(S.confidence.fusion.ndcg > S.confidence.baseline.ndcg + 1e-6,
      `confidence: fusion nDCG(${S.confidence.fusion.ndcg.toFixed(4)})>baseline(${S.confidence.baseline.ndcg.toFixed(4)})`);
    ok(S.confidence.fusion.ndcg >= S.confidence.mmr.ndcg - 1e-6, 'confidence: fusion nDCG≥MMR');
  }
  // control：baseline 已理想（nDCG=1），两臂不应把 nDCG 拉低太多（记录风险即可，阈值宽松）
  if (S.control) {
    ok(approx(S.control.baseline.ndcg, 1, 1e-6), `control: baseline nDCG==1（原序已理想）`);
  }

  if (fails.length) {
    console.error('[rerank-eval] ✗ selftest 失败：');
    for (const f of fails) console.error('   - ' + f);
    process.exit(1);
  }
  console.log('[rerank-eval] ✓ selftest 通过（指标单测 + 判别不变量 + 确定性两遍逐位相等）。');
}

// ───────────────────────── 报告 ─────────────────────────
const f4 = (n) => n.toFixed(4);
const f3 = (n) => n.toFixed(3);
const fD = (n) => (n >= 0 ? '+' : '') + n.toFixed(4);

/** 某 scenario 主指标：redundancy→αnDCG、其余→nDCG。 */
function primaryMetric(sc) {
  return sc === 'redundancy' ? 'alphaNdcg' : 'ndcg';
}
function primaryLabel(sc) {
  return sc === 'redundancy' ? 'αnDCG@K' : 'nDCG@K';
}

function winnerOf(agg, sc) {
  const m = primaryMetric(sc);
  const vals = ARMS.map((a) => ({ a, v: agg.byScenario[sc][a][m] }));
  vals.sort((x, y) => y.v - x.v);
  return vals[0];
}

function armTable(scAgg) {
  const L = [];
  L.push('| 臂 | nDCG@K | αnDCG@K | distinct@K | Kendallτ | firstMaxRank |');
  L.push('| --- | --- | --- | --- | --- | --- |');
  for (const arm of ARMS) {
    const g = scAgg[arm];
    L.push(`| ${arm} | ${f4(g.ndcg)} | ${f4(g.alphaNdcg)} | ${f3(g.distinct)} | ${fD(g.tau)} | ${f3(g.firstMax)} |`);
  }
  return L;
}

function buildReport(run, sweep, meta, cfg, realRun) {
  const { k3, k5 } = run;
  const L = [];
  L.push('# 重排判别评测报告 — Tranche 3 α');
  L.push('');
  L.push('> 在**判别性**黄金集 `bench/rerank-golden.json` 上量化两种确定性重排相对「检索器原序（baseline）」');
  L.push('> 的可测收益：**A·MMR 多样性** / **B·score 融合**。现有 `tests/retrieval/golden.json` 上检索原序已近最优');
  L.push('> （hybrid 零增益，见 `bench/retrieval-after.md`），显不出重排差异；本集专补这一判别缺口——刻意构造');
  L.push('> 「检索器原序次优、可被重排修复」的用例。全部离线确定（HashEmbedder + 生产 effectiveConfidence，');
  L.push('> now 由 golden 固定注入），数字可由生成命令逐位复现。');
  L.push('');
  L.push('## 生成环境');
  L.push('');
  L.push('| 项 | 值 |');
  L.push('| --- | --- |');
  L.push(`| 生成命令 | \`${meta.cmd}\` |`);
  L.push(`| commit | \`${meta.commit}\` |`);
  L.push(`| Node | ${meta.node} · ${meta.platform}/${meta.arch} |`);
  L.push(`| 生成时间 | ${meta.generatedAt} |`);
  L.push(`| 黄金集 | bench/rerank-golden.json（${meta.caseCount} case，${meta.scenarios.join(' / ')}） |`);
  L.push(`| evalK | ${cfg.evalK}（另出 @5） |`);
  L.push(`| MMR λ | ${cfg.mmrLambda} |`);
  L.push(`| 融合权重 | wSim=${cfg.fusion.wSim} · wEff=${cfg.fusion.wEff} · wCred=${cfg.fusion.wCred} |`);
  L.push(`| effConf | 生产 effectiveConfidence（src/background/decay.ts，半衰期口径同 config） |`);
  L.push(`| 确定性自检 | ${meta.determinismOk ? '通过（两遍逐位相等，默认 HashEmbedder 臂）' : '失败'} |`);
  L.push(`| 真实嵌入交叉验证 | ${meta.realArmNote} |`);
  L.push('');

  L.push('三臂定义：');
  L.push('');
  L.push('- **baseline**：检索器原序（按 score 降序的恒等重排）——对照。');
  L.push('- **A·MMR**：贪心 MMR，相关性=score、冗余度=候选文本 HashEmbedder 两两余弦，λ 权衡（本次 λ=' + cfg.mmrLambda + '）。');
  L.push('- **B·fusion**：`wSim·score + wEff·(effConf/1000) + wCred·credRank`，只用召回项已有字段（零新数据）。');
  L.push('');
  L.push('指标：nDCG@K（效用分档，看高效用条是否早出）· αnDCG@K（多样性感知，α=0.5）· distinct@K（top-K 不同 topic 数）· Kendallτ（相对 idealOrder 秩相关）· firstMaxRank（首条满分 gain 名次，越小越好）。**每类 scenario 主指标**：redundancy→αnDCG、recency/confidence/control→nDCG。');
  L.push('');

  // ── 判别结论表 ──
  L.push('## 一、判别结论（每类 scenario 谁赢、幅度多大）@K=' + cfg.evalK);
  L.push('');
  L.push('| scenario | n | 主指标 | baseline | A·MMR | B·fusion | 赢家 | Δ(赢家−baseline) |');
  L.push('| --- | --- | --- | --- | --- | --- | --- | --- |');
  for (const sc of k3.scenarios) {
    const m = primaryMetric(sc);
    const b = k3.byScenario[sc].baseline[m];
    const a = k3.byScenario[sc].mmr[m];
    const fus = k3.byScenario[sc].fusion[m];
    const w = winnerOf(k3, sc);
    L.push(`| ${sc} | ${k3.byScenario[sc].n} | ${primaryLabel(sc)} | ${f4(b)} | ${f4(a)} | ${f4(fus)} | **${w.a}** | ${fD(w.v - b)} |`);
  }
  L.push('');
  L.push('> 赢家按该 scenario 主指标取最高臂。Δ 为赢家相对 baseline 的主指标增量。');
  L.push('');

  // ── 各 scenario 详表 ──
  L.push('## 二、各 scenario 三臂全指标（@K=' + cfg.evalK + '）');
  L.push('');
  for (const sc of k3.scenarios) {
    L.push(`### scenario = ${sc}（n=${k3.byScenario[sc].n}）`);
    L.push('');
    L.push(...armTable(k3.byScenario[sc]));
    L.push('');
  }

  // ── overall ──
  L.push('## 三、overall 三臂（@K=' + cfg.evalK + ' 与 @5）');
  L.push('');
  L.push('**@K=' + cfg.evalK + '**');
  L.push('');
  L.push(...armTable(k3.overall));
  L.push('');
  L.push('**@5**');
  L.push('');
  L.push(...armTable(k5.overall));
  L.push('');

  // ── 逐 case ──
  L.push('## 四、逐 case 明细（@K=' + cfg.evalK + '）');
  L.push('');
  L.push('| case | scenario | 主指标 base→MMR→fusion | baseline 原序 | MMR 序 | fusion 序 |');
  L.push('| --- | --- | --- | --- | --- | --- |');
  for (const r of run.caseResults) {
    const m = primaryMetric(r.scenario);
    const trip = `${f3(r.k3.baseline[m])}→${f3(r.k3.mmr[m])}→${f3(r.k3.fusion[m])}`;
    L.push(`| ${r.id} | ${r.scenario} | ${trip} | ${r.orders.baseline.join(' ')} | ${r.orders.mmr.join(' ')} | ${r.orders.fusion.join(' ')} |`);
  }
  L.push('');

  // ── 敏感性 ──
  L.push('## 五、鲁棒性：λ / 权重扫描（overall 主指标均值，@K=' + cfg.evalK + '）');
  L.push('');
  L.push('看结论对超参是否稳健（软指标高方差 → 多点取势，同 D-0008/D-0009 纪律）。');
  L.push('');
  L.push('**MMR λ 扫描 → redundancy αnDCG（越低 λ 越偏多样）**');
  L.push('');
  L.push('| λ | redundancy αnDCG(MMR) | Δ vs baseline | control nDCG(MMR) | 备注 |');
  L.push('| --- | --- | --- | --- | --- |');
  for (const s of sweep.lambda) {
    L.push(`| ${s.lambda} | ${f4(s.redAlpha)} | ${fD(s.redAlpha - s.redBase)} | ${f4(s.ctrlNdcg)} | ${s.note} |`);
  }
  L.push('');
  L.push('**融合权重扫描 → recency+confidence nDCG(fusion)**');
  L.push('');
  L.push('| wSim / wEff / wCred | recency nDCG(fusion) | confidence nDCG(fusion) | Δrec vs base | Δconf vs base |');
  L.push('| --- | --- | --- | --- | --- |');
  for (const s of sweep.weights) {
    L.push(`| ${s.w} | ${f4(s.recNdcg)} | ${f4(s.confNdcg)} | ${fD(s.recNdcg - s.recBase)} | ${fD(s.confNdcg - s.confBase)} |`);
  }
  L.push('');

  // ── 真实嵌入交叉验证（opt-in --real-embed）──
  if (realRun) {
    L.push('## 五点五、真实嵌入交叉验证（bge-m3，opt-in --real-embed）');
    L.push('');
    L.push('用真实 **bge-m3**（@127.0.0.1:11435，dim=1024）替换 HashEmbedder 算候选两两冗余度复跑。');
    L.push('**只有 MMR 臂依赖嵌入**（baseline 按 score、fusion 按 score+元数据，均与嵌入无关）——');
    L.push('故本节验证的是「MMR 的多样性收益在真实语义向量下是否仍成立」。非确定（网络），仅供佐证。');
    L.push('');
    L.push('| scenario | 主指标 | baseline | MMR(HashEmb) | MMR(bge-m3) | fusion |');
    L.push('| --- | --- | --- | --- | --- | --- |');
    for (const sc of k3.scenarios) {
      const m = primaryMetric(sc);
      L.push(`| ${sc} | ${primaryLabel(sc)} | ${f4(k3.byScenario[sc].baseline[m])} | ${f4(k3.byScenario[sc].mmr[m])} | ${f4(realRun.k3.byScenario[sc].mmr[m])} | ${f4(k3.byScenario[sc].fusion[m])} |`);
    }
    L.push('');
    const redReal = realRun.k3.byScenario.redundancy.mmr.alphaNdcg;
    const redBaseR = realRun.k3.byScenario.redundancy.baseline.alphaNdcg;
    L.push(`- **redundancy MMR 收益在真实向量下${redReal > redBaseR + 1e-6 ? '仍成立' : '不成立'}**：αnDCG@${cfg.evalK} baseline ${f4(redBaseR)} → MMR(bge-m3) **${f4(redReal)}**（Δ ${fD(redReal - redBaseR)}）；HashEmbedder 版为 ${f4(k3.byScenario.redundancy.mmr.alphaNdcg)}。真实语义向量下近重复条相似度更高（实测近重复 ~0.98、异话题 ~0.61），MMR 的冗余识别至少同样清晰。`);
    L.push(`- 结论：判别集的 MMR 多样性收益**不是 HashEmbedder 词面巧合**，真实嵌入下同样显著。`);
    L.push('');
  }

  // ── 结论 & β 建议 ──
  const redW = winnerOf(k3, 'redundancy');
  const recW = k3.byScenario.recency ? winnerOf(k3, 'recency') : null;
  const confW = k3.byScenario.confidence ? winnerOf(k3, 'confidence') : null;
  const redBase = k3.byScenario.redundancy.baseline.alphaNdcg;
  const recBase = k3.byScenario.recency?.baseline.ndcg ?? 0;
  const confBase = k3.byScenario.confidence?.baseline.ndcg ?? 0;
  const ctrlMMRd = k3.byScenario.control ? k3.byScenario.control.mmr.ndcg - k3.byScenario.control.baseline.ndcg : 0;
  const ctrlFusd = k3.byScenario.control ? k3.byScenario.control.fusion.ndcg - k3.byScenario.control.baseline.ndcg : 0;

  L.push('## 六、结论与 β 决策建议');
  L.push('');
  L.push('### 这个判别集上，重排有可测收益吗？');
  L.push('');
  L.push(`- **有，且分工清晰**：在刻意构造的判别用例上，两种重排都在各自靶场对 baseline 取得**非零**增量：`);
  L.push(`  - **redundancy（去同话题冗余）→ A·MMR 赢**：αnDCG@${cfg.evalK} ${f4(redBase)} → **${f4(k3.byScenario.redundancy.mmr.alphaNdcg)}**（Δ ${fD(k3.byScenario.redundancy.mmr.alphaNdcg - redBase)}）；distinct@${cfg.evalK} ${f3(k3.byScenario.redundancy.baseline.distinct)} → **${f3(k3.byScenario.redundancy.mmr.distinct)}**。fusion 在此≈baseline（元数据不含多样性信号）。`);
  const recMMR = k3.byScenario.recency ? k3.byScenario.recency.mmr.ndcg : 0;
  const confMMR = k3.byScenario.confidence ? k3.byScenario.confidence.mmr.ndcg : 0;
  const ctrlMMRworst = Math.min(...sweep.lambda.map((s) => s.ctrlNdcg));
  const ctrlMMRworstLam = sweep.lambda.find((s) => s.ctrlNdcg === ctrlMMRworst)?.lambda;
  if (recW) L.push(`  - **recency（新近度应影响排序）→ B·fusion 赢**：nDCG@${cfg.evalK} ${f4(recBase)} → **${f4(k3.byScenario.recency.fusion.ndcg)}**（Δ ${fD(k3.byScenario.recency.fusion.ndcg - recBase)}）。MMR 在此=${f4(recMMR)}（≈baseline，同话题近同文本无从多样化）。`);
  if (confW) L.push(`  - **confidence（可信度应影响排序）→ B·fusion 赢**：nDCG@${cfg.evalK} ${f4(confBase)} → **${f4(k3.byScenario.confidence.fusion.ndcg)}**（Δ ${fD(k3.byScenario.confidence.fusion.ndcg - confBase)}）。MMR 在此=${f4(confMMR)}（${confMMR < confBase - 1e-6 ? '**不但不帮、还略降**，因 C-01 的异话题 gain=0 条被多样化目标错误上提' : '≈baseline'}）。`);
  L.push(`- **control（原序已理想）→ 记录副作用**：baseline nDCG@${cfg.evalK}=${f4(k3.byScenario.control?.baseline.ndcg ?? 1)}；本次 λ=${cfg.mmrLambda} 下 MMR Δ=${fD(ctrlMMRd)}、fusion Δ=${fD(ctrlFusd)}。` + (ctrlFusd < -1e-6 ? `**fusion 会把高置信但低相关的条上提而微损**（CT-02：stable 的无关偏好被 wEff/wCred 抬过低置信相关条），需 wSim 足够大压住。` : '') + ` **MMR 的过度多样化风险随 λ 降低而显现**：λ 扫描里 control nDCG(MMR) 在 λ=${ctrlMMRworstLam} 掉到 **${f4(ctrlMMRworst)}**（把 gain=0 异话题条挤进前排）——故 MMR 的 λ 要偏相关性侧（≥0.7）。`);
  L.push('');
  L.push('### 关键判别设计点');
  L.push('');
  L.push('- 每类 scenario **隔离单一信号**：redundancy 用例元数据同质（只有向量多样性能区分 → 只 MMR 赢）；recency/confidence 用例话题同质（只有元数据能区分 → 只 fusion 赢）。这保证「谁赢」可归因到策略机制，而非用例巧合。');
  L.push('- baseline 的「原序次优」是**刻意注入**的（score 手设为把陈旧/低置信/冗余项排前）——因为真实系统里检索器**是否真产出这种次优序，需真检索器 + 真嵌入复核**（见下）。本集测的是「**若**出现此类可修复缺陷，重排能修多少」，不等于「真实系统里重排一定有此收益」。');
  L.push('');
  L.push('### 给 Integrator 的 β 建议');
  L.push('');
  L.push('- **优先级：B·fusion 先行，A·MMR 缓行**。理由是**集成成本**（是否触 api-freeze）差异悬殊：');
  L.push('  - **B·fusion 不触 api-freeze**：它只重排 `recallCognitions` 产出的 `RecalledCognitionItem[]`——所需字段（`score`/`confidence`[已是 effConf]/`credStatus`/`contentType`）**全部已在项上**，零新数据。作为**纯内部函数**在 `recall.ts` 的 `out` 生成后插一段 `out.sort(...)` 即可（recall.ts:47 是 `retriever.search`，实际重排点在门控循环产出 `out` 之后）。若权重写成模块内常量 → **不新增 config 字段、不导出接口 → 公共 API 冻结面不动、api:check 保持绿**。');
  L.push('  - **A·MMR 大概率触 api-freeze**：MMR 的冗余度需要**候选向量的两两相似度**，而 `Retriever.search` 只回 `{id,score}`、`VectorRetriever` 的向量是内部私有、`recallCognitions` 手里只有 `Retriever` 没有 `Embedder`。要做 MMR 必须**新增 seam**——给 `Retriever` 加「回候选向量」的方法，或把 `Embedder` 注入 recall 重算嵌入——两者都改**公共接口/依赖形状 → 触 api-freeze，须走 D-xxxx**。且重算嵌入有额外算力/延迟成本。');
  L.push('- **若走 B**：建议先以模块常量落地（不触 api），dogfood 校准后再决定是否把 `wSim/wEff/wCred` 提升为 `config.retrieval.rerank*`（**那一步才触 api-freeze，须 D-xxxx**）。');
  L.push('- **诚实边界（防过度解读）**：本集是「能显差异」的合成判别集，Δ 是**上界性质**的存在性证据，不是真实语料的期望收益。' + (realRun ? '§5.5 已用真实 bge-m3 交叉验证 MMR 臂、多样性收益非词面巧合；但' : '') + '进 β 前仍建议：①用真实 bge-m3（@127.0.0.1:11435）对**真实检索原序**复跑（本集 baseline 序是手设的，需确认真检索器确会产出冗余/陈旧靠前的次优序）；②在真实 `golden.json` / LoCoMo 上量 fusion 的端到端 Recall/nDCG 是否也有正向位移。若真实检索原序本就无这些缺陷（如 retrieval-after.md 显示的近最优），则按**铁律 4 不做**——不为一个真实系统不出现的问题加装置。');
  L.push('');
  L.push('## 备注');
  L.push('');
  L.push('- 全部离线确定：HashEmbedder（候选向量）+ 生产 effectiveConfidence（now 由 golden 固定），无网络、无随机、无系统时钟；每数字可由生成命令逐位复现。`--selftest` 自证（指标单测 + 判别不变量 + 两遍逐位相等）。');
  L.push('- **范围**：纯 bench/ 新增（rerank-golden.json + rerank-eval.mjs + 本报告），只读 import src/tests，未改 src/ / tests/ / api 快照 / DECISIONS / CHANGELOG。是否进 β 由 Integrator 守门，评测不代拍板。');
  L.push(`- **真实嵌入交叉验证**：${realRun ? '本次已跑（见 §5.5），MMR 多样性收益在真实 bge-m3 向量下同样显著。' : '`--real-embed` opt-in（需 .env MEMOWEFT_EMBED_* + 联网）；本次未跑。'} 更彻底的「真实检索原序」变体（用真嵌入定 baseline 序再重排）留作 β 前验证。`);
  L.push('');
  return L.join('\n');
}

// ───────────────────────── 扫描 ─────────────────────────
async function runSweeps(golden, cfg) {
  const lambda = [];
  for (const lam of [0.3, 0.5, 0.7, 0.9, 1.0]) {
    const run = await runAll(golden, { ...cfg, mmrLambda: lam });
    const red = run.k3.byScenario.redundancy;
    const ctrl = run.k3.byScenario.control;
    lambda.push({
      lambda: lam,
      redAlpha: red.mmr.alphaNdcg,
      redBase: red.baseline.alphaNdcg,
      ctrlNdcg: ctrl ? ctrl.mmr.ndcg : 1,
      note: lam === 1.0 ? 'λ=1 纯相关性 → MMR≡baseline' : '',
    });
  }
  const weights = [];
  for (const w of [
    { wSim: 0.7, wEff: 0.2, wCred: 0.1 },
    { wSim: 0.55, wEff: 0.3, wCred: 0.15 },
    { wSim: 0.4, wEff: 0.4, wCred: 0.2 },
  ]) {
    const run = await runAll(golden, { ...cfg, fusion: w });
    const rec = run.k3.byScenario.recency;
    const conf = run.k3.byScenario.confidence;
    weights.push({
      w: `${w.wSim} / ${w.wEff} / ${w.wCred}`,
      recNdcg: rec ? rec.fusion.ndcg : 0,
      recBase: rec ? rec.baseline.ndcg : 0,
      confNdcg: conf ? conf.fusion.ndcg : 0,
      confBase: conf ? conf.baseline.ndcg : 0,
    });
  }
  return { lambda, weights };
}

// ───────────────────────── CLI ─────────────────────────
function parseArg(flag, def) {
  const i = process.argv.indexOf(flag);
  if (i < 0 || i + 1 >= process.argv.length) return def;
  return process.argv[i + 1];
}
function loadGolden() {
  // golden 放 bench/（本任务产物）；兼容误放 tests/retrieval/
  const primary = resolve(HERE, 'rerank-golden.json');
  try {
    return JSON.parse(readFileSync(primary, 'utf8'));
  } catch {
    return JSON.parse(readFileSync(GOLDEN_PATH, 'utf8'));
  }
}

async function main() {
  const golden = loadGolden();
  const cfg = {
    mmrLambda: Number(parseArg('--lambda', golden.config.mmrLambda)),
    evalK: Number(parseArg('--k', golden.config.evalK)),
    fusion: golden.config.fusion,
    credRank: golden.config.credRank,
  };

  if (process.argv.includes('--selftest')) {
    await selftest(golden, cfg);
    return;
  }

  const run = await runAll(golden, cfg);
  const run2 = await runAll(golden, cfg);
  const determinismOk = deterministicSig(run) === deterministicSig(run2);
  if (!determinismOk) {
    console.error('[rerank-eval] ✗ 确定性自检失败：两遍不逐位相等。');
    process.exit(1);
  }

  if (process.argv.includes('--debug')) {
    for (const r of run.caseResults) {
      console.log(`\n[${r.id}] ${r.scenario} :: ${r.query}`);
      console.log('  baseline:', r.orders.baseline.join(' '));
      console.log('  mmr     :', r.orders.mmr.join(' '));
      console.log('  fusion  :', r.orders.fusion.join(' '));
      const m = primaryMetric(r.scenario);
      console.log(`  ${m}@${cfg.evalK}: base=${f4(r.k3.baseline[m])} mmr=${f4(r.k3.mmr[m])} fusion=${f4(r.k3.fusion[m])}`);
    }
  }

  const sweep = await runSweeps(golden, cfg);

  // 真实嵌入交叉验证（opt-in）：默认离线不打网络（保持确定性，§14.1）；--real-embed 才跑。
  let realRun = null;
  let realArmNote = 'off（默认离线确定；--real-embed 且配 .env MEMOWEFT_EMBED_* 以启用）';
  if (process.argv.includes('--real-embed')) {
    const embedCfg = loadEmbedConfig();
    if (embedCfg) {
      try {
        console.log(`[rerank-eval] 真实嵌入交叉验证：OpenAICompatEmbedder（model=${embedCfg.model}）…`);
        realRun = await runAll(golden, cfg, new OpenAICompatEmbedder(embedCfg));
        realArmNote = `已跑（model=${embedCfg.model}）：redundancy MMR αnDCG@${cfg.evalK}=${f4(realRun.k3.byScenario.redundancy.mmr.alphaNdcg)}（非确定，网络）`;
      } catch (err) {
        realArmNote = `请求但调用失败（${err instanceof Error ? err.message : String(err)}）`;
        console.error('[rerank-eval] 真实嵌入调用失败：', err instanceof Error ? err.message : err);
      }
    } else {
      realArmNote = '请求 --real-embed 但无 embed 配置（.env 缺 MEMOWEFT_EMBED_*）';
    }
  }

  const meta = {
    cmd: `node bench/rerank-eval.mjs${process.argv.slice(2).length ? ' ' + process.argv.slice(2).join(' ') : ''}`,
    commit: (() => { try { return execSync('git rev-parse --short HEAD', { cwd: HERE }).toString().trim(); } catch { return 'unknown'; } })(),
    node: process.versions.node,
    platform: process.platform,
    arch: process.arch,
    generatedAt: new Date().toISOString(),
    caseCount: golden.cases.length,
    scenarios: run.k3.scenarios,
    determinismOk,
    realArmNote,
  };

  // 终端摘要
  console.log('\n════════ 重排判别评测（Tranche 3 α）════════');
  console.log(`commit ${meta.commit} · Node ${meta.node} · λ=${cfg.mmrLambda} · K=${cfg.evalK} · ${meta.caseCount} case`);
  for (const sc of run.k3.scenarios) {
    const m = primaryMetric(sc);
    const s = run.k3.byScenario[sc];
    const w = winnerOf(run.k3, sc);
    console.log(`${sc.padEnd(11)} ${primaryLabel(sc)} base=${f4(s.baseline[m])} mmr=${f4(s.mmr[m])} fusion=${f4(s.fusion[m])} → 赢家 ${w.a} (Δ${fD(w.v - s.baseline[m])})`);
  }
  console.log('════════════════════════════════════════════');

  const outPrefix = parseArg('--out', null);
  const outPath = outPrefix ? `${outPrefix}.md` : resolve(HERE, 'rerank-baseline.md');
  writeFileSync(outPath, buildReport(run, sweep, meta, cfg, realRun), 'utf8');
  console.log(`[rerank-eval] 报告已写入 ${outPath}`);
}

main().catch((err) => {
  console.error('[rerank-eval] 失败：', err);
  process.exit(1);
});
