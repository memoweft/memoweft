/**
 * 检索评测（Phase 1 · §14.2 基线 + §14.6 三臂消融）。手动跑、不进 CI、不设门。
 *
 * 两种模式：
 *   1) 默认 `node bench/eval-retrieval.mjs`：量【当前 vector-only 系统】（VectorRetriever + 确定性
 *      HashEmbedder）在黄金集 `tests/retrieval/golden.json` 上的召回，落 `retrieval-baseline.md`。
 *      这是 §14.3/14.4 加 BM25+RRF hybrid 之后做对比的基准——「先测量后优化」。**行为与数字不变**。
 *   2) `node bench/eval-retrieval.mjs --ablation`：跑 §14.6 **三臂消融**
 *      （vector-only / keyword-only / hybrid），量 hybrid 相对 vector-only 基线的增益，
 *      落 `retrieval-after.md`——判断是否值得把 hybrid 接进公共 API（§14.4b）的依据。
 *
 * 直接从 src/tests 的 .ts import（Node ≥24 原生剥类型，无需 build）。只读依赖，绝不改它们。
 *
 * 指标（每条 case，topK=10）：
 *   - top5      = hits.slice(0,5) 的 id
 *   - recall5   = (expect 落在 top5 的个数) / expect.length
 *   - hit5      = expect 中任一 id ∈ top5 ? 1 : 0
 *   - firstRank = expect 中任一 id 在 hits 前 10 的最小 1-based 排名；rr10 = firstRank ? 1/firstRank : 0
 *   汇总 mean：overall + 按 kind(direct/paraphrase/multihop) + 按语言(含 CJK=zh 否则 en)。
 *   latency：全体 P50 / P95（ms，nearest-rank）。
 *
 * 确定性自检：每臂各跑两遍，断言两次【指标】逐位相等（HashEmbedder/BM25/RRF 均确定，latency 除外）；
 *   不等则 process.exit(1)。
 *
 * 真实臂（opt-in，默认关）：设 EVAL_REAL_ARM=1 或 --real，且 env(.env) 配了 MEMOWEFT_EMBED_* 才额外跑
 *   真实嵌入臂（OpenAICompatEmbedder）；默认离线、不打网络（保持复现的确定性，§14.1）。
 *
 * 用法：node bench/eval-retrieval.mjs                          # 默认纯离线确定性 vector-only 基线
 *       node bench/eval-retrieval.mjs --ablation               # 三臂消融（离线确定性）
 *       EVAL_REAL_ARM=1 node bench/eval-retrieval.mjs          # 基线 + 额外真实嵌入臂（需 .env + 联网）
 *       EVAL_REAL_ARM=1 node bench/eval-retrieval.mjs --ablation # 三臂 + 真实嵌入臂（需 .env + 联网）
 *
 * A3 新增两个 CLI 口子（§15.4，只加 CLI、不动评测逻辑与数字）：
 *   --out <prefix>       报告写到 <prefix>.md；不给则同旧（baseline→retrieval-baseline.md / ablation→retrieval-after.md）。
 *   --require-real-arm   请求了真实臂（EVAL_REAL_ARM=1 或 --real）却最终 pending（无 embed 配置 / 调用失败 / 未请求）
 *                        → 打印明确原因并 exit 1；不给此标志时行为同旧（pending 不算失败）。
 *                        供 test:live：embed 端点挂了不许悄悄变绿。
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { VectorRetriever } from '../src/retrieval/vectorRetriever.ts';
import { KeywordRetriever } from '../src/retrieval/keywordRetriever.ts';
import { HybridRetriever } from '../src/retrieval/hybridRetriever.ts';
import { loadEmbedConfig, OpenAICompatEmbedder } from '../src/retrieval/embedder.ts';
import { HashEmbedder, DEFAULT_DIM } from '../tests/retrieval/hashEmbedder.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const GOLDEN_PATH = resolve(HERE, '../tests/retrieval/golden.json');
const REPORT_PATH = resolve(HERE, 'retrieval-baseline.md');
const AFTER_REPORT_PATH = resolve(HERE, 'retrieval-after.md');
// 生成命令:反映【实际调用】——EVAL_REAL_ARM 前缀 + 全部 flag(--ablation / --out / --real 等),不再硬编码(ROADMAP Next)。
const INVOKED_CMD = `${process.env.EVAL_REAL_ARM ? 'EVAL_REAL_ARM=1 ' : ''}node bench/eval-retrieval.mjs${process.argv.slice(2).length ? ' ' + process.argv.slice(2).join(' ') : ''}`;
const TOP_K = 10;

/** 9 条纯 2 字中文 direct 用例（验证向量 char-bigram 能否兜住 trigram 关键词通道够不着的 2 字词）。 */
const TWO_CHAR_CASES = ['G-004', 'G-008', 'G-009', 'G-010', 'G-013', 'G-015', 'G-016', 'G-018', 'G-019'];

/** 基线 overall Recall@5（committed retrieval-baseline.md），+10% 目标据此算。 */
const BASELINE_OVERALL_RECALL5 = 0.7154;
/** +10% 目标线：0.7154 × 1.10 = 0.78694 → ≥ 0.7869。 */
const TARGET_RECALL5 = 0.7869;

const CJK = /\p{Script=Han}/u;
const langOf = (query) => (CJK.test(query) ? 'zh' : 'en');

/** nearest-rank 百分位（sorted 升序）。P50 of n=65 → rank ceil(.5*65)=33 → idx 32。 */
function percentile(sortedAsc, p) {
  if (sortedAsc.length === 0) return 0;
  const rank = Math.ceil((p / 100) * sortedAsc.length);
  const idx = Math.min(sortedAsc.length - 1, Math.max(0, rank - 1));
  return sortedAsc[idx];
}

/** 一组 case 结果求 mean（Recall@5 / Hit@5 / MRR@10）。 */
function aggregate(results) {
  const n = results.length;
  if (n === 0) return { n: 0, recall5: 0, hit5: 0, mrr10: 0 };
  const mean = (f) => results.reduce((a, r) => a + f(r), 0) / n;
  return {
    n,
    recall5: mean((r) => r.recall5),
    hit5: mean((r) => r.hit5),
    mrr10: mean((r) => r.rr10),
  };
}

/**
 * 用给定 retriever 工厂跑整套黄金集。每次调用 makeRetriever() 都 new 一个干净的 `:memory:` retriever
 * （单一职责：本函数只吃 Retriever 接口，不认识具体通道类型——vector/keyword/hybrid 都同一路径）。
 * 返回每条 case 的逐项指标 + 分组汇总。latency 用 performance.now() 计 search(query, TOP_K)；不进确定性对比。
 */
async function runEvalWith(makeRetriever, cognitions, cases) {
  const retriever = makeRetriever();
  try {
    await retriever.indexAll(cognitions.map((c) => ({ id: c.id, text: c.content })));

    const results = [];
    for (const c of cases) {
      const t0 = performance.now();
      const hits = await retriever.search(c.query, TOP_K);
      const latency = performance.now() - t0;

      const expect = c.expect;
      const top5 = hits.slice(0, 5).map((h) => h.id);
      const top5Set = new Set(top5);
      const inTop5 = expect.filter((id) => top5Set.has(id)).length;
      const recall5 = inTop5 / expect.length;
      const hit5 = expect.some((id) => top5Set.has(id)) ? 1 : 0;

      let firstRank = 0;
      for (let i = 0; i < hits.length; i++) {
        if (expect.includes(hits[i].id)) {
          firstRank = i + 1;
          break;
        }
      }
      const rr10 = firstRank ? 1 / firstRank : 0;

      results.push({
        id: c.id,
        query: c.query,
        kind: c.kind,
        lang: langOf(c.query),
        expect,
        top5,
        recall5,
        hit5,
        firstRank,
        rr10,
        latency,
      });
    }

    const byKind = {};
    for (const kind of ['direct', 'paraphrase', 'multihop']) {
      byKind[kind] = aggregate(results.filter((r) => r.kind === kind));
    }
    const byLang = {};
    for (const lang of ['zh', 'en']) {
      byLang[lang] = aggregate(results.filter((r) => r.lang === lang));
    }
    const overall = aggregate(results);
    const latencies = results.map((r) => r.latency).sort((a, b) => a - b);
    const latency = { p50: percentile(latencies, 50), p95: percentile(latencies, 95) };

    return { results, overall, byKind, byLang, latency };
  } finally {
    if (typeof retriever.close === 'function') retriever.close();
  }
}

/** 抽取【确定性签名】：逐 case 的 recall5/hit5/rr10 + 分组 mean（不含 latency），用于两遍逐位比对。 */
function deterministicSig(run) {
  return JSON.stringify({
    perCase: run.results
      .map((r) => ({ id: r.id, recall5: r.recall5, hit5: r.hit5, rr10: r.rr10 }))
      .sort((a, b) => a.id.localeCompare(b.id)),
    overall: { recall5: run.overall.recall5, hit5: run.overall.hit5, mrr10: run.overall.mrr10 },
    byKind: run.byKind,
    byLang: run.byLang,
  });
}

/** 跑一臂并做确定性自检：同一 makeRetriever 跑两遍，比对指标签名。返回 { run, determinismOk }。 */
async function runArmWithSelfCheck(name, makeRetriever, cognitions, cases) {
  const run1 = await runEvalWith(makeRetriever, cognitions, cases);
  const run2 = await runEvalWith(makeRetriever, cognitions, cases);
  const determinismOk = deterministicSig(run1) === deterministicSig(run2);
  if (!determinismOk) {
    console.error(`[eval-retrieval] ✗ 确定性自检失败（臂=${name}）：两遍指标不逐位相等。`);
    console.error('run1:', deterministicSig(run1));
    console.error('run2:', deterministicSig(run2));
    process.exit(1);
  }
  console.log(`[eval-retrieval] ✓ 确定性自检通过（臂=${name}）：两遍指标逐位相等。`);
  return { run: run1, determinismOk };
}

const f4 = (n) => n.toFixed(4);
const f3 = (n) => n.toFixed(3);
/** 带符号的 Δ 格式（正数补 +，用于 hybrid − vector 对比）。 */
const fDelta = (n) => (n >= 0 ? '+' : '') + n.toFixed(4);

/** 某臂在 2 字中文子集上的汇总。 */
function twoCharAgg(run) {
  return aggregate(run.results.filter((r) => TWO_CHAR_CASES.includes(r.id)));
}

function metricRow(label, agg) {
  return `| ${label} | ${agg.n} | ${f4(agg.recall5)} | ${f4(agg.hit5)} | ${f4(agg.mrr10)} |`;
}

function buildReport(run, meta) {
  const { overall, byKind, byLang, latency } = run;
  const L = [];
  L.push('# 检索基线报告（vector-only）— Phase 1 §14.2');
  L.push('');
  L.push('> 本报告量的是【当前 vector-only 系统】在黄金集上的召回，作为 §14.3/14.4 加 BM25+RRF');
  L.push('> hybrid 后对比的**基准**。先入库基线，才动优化（先测量后优化）。');
  L.push('');
  L.push('## 生成环境');
  L.push('');
  L.push('| 项 | 值 |');
  L.push('| --- | --- |');
  L.push(`| 生成命令 | \`${INVOKED_CMD}\` |`);
  L.push(`| commit | \`${meta.commit}\` |`);
  L.push(`| Node | ${meta.node} |`);
  L.push(`| 平台 | ${meta.platform}/${meta.arch} |`);
  L.push(`| 生成时间 | ${meta.generatedAt} |`);
  L.push(`| 臂 | HashEmbedder（dim=${DEFAULT_DIM}，确定性词袋哈希） |`);
  L.push(`| topK | ${TOP_K} |`);
  L.push(`| 黄金集 | tests/retrieval/golden.json（${meta.cognitionCount} 条 cognition，${meta.caseCount} 条 case） |`);
  L.push(`| 确定性自检 | ${meta.determinismOk ? '通过（两遍指标逐位相等）' : '失败'} |`);
  L.push(`| 真实臂 | ${meta.realArm} |`);
  L.push('');
  L.push('## 总体指标');
  L.push('');
  L.push('| 分组 | n | Recall@5 | Hit@5 | MRR@10 |');
  L.push('| --- | --- | --- | --- | --- |');
  L.push(metricRow('overall', overall));
  L.push('');
  L.push('## 按 kind 分组');
  L.push('');
  L.push('| kind | n | Recall@5 | Hit@5 | MRR@10 |');
  L.push('| --- | --- | --- | --- | --- |');
  L.push(metricRow('direct', byKind.direct));
  L.push(metricRow('paraphrase', byKind.paraphrase));
  L.push(metricRow('multihop', byKind.multihop));
  L.push('');
  L.push('## 按语言分组（query 含 CJK=zh，否则 en）');
  L.push('');
  L.push('| lang | n | Recall@5 | Hit@5 | MRR@10 |');
  L.push('| --- | --- | --- | --- | --- |');
  L.push(metricRow('zh', byLang.zh));
  L.push(metricRow('en', byLang.en));
  L.push('');
  L.push('## Latency（全体 search，ms）');
  L.push('');
  L.push('| 分位 | ms |');
  L.push('| --- | --- |');
  L.push(`| P50 | ${f3(latency.p50)} |`);
  L.push(`| P95 | ${f3(latency.p95)} |`);
  L.push('');
  L.push('> latency 为本机测量、非确定量，不参与确定性自检；仅供量级参考。');
  L.push('');

  // 重点结论 1：direct vs paraphrase Recall 差
  const dR = byKind.direct.recall5;
  const pR = byKind.paraphrase.recall5;
  const diff = dR - pR;
  L.push('## 重点结论');
  L.push('');
  L.push('### 1. direct vs paraphrase 的 Recall 差');
  L.push('');
  L.push(`- direct Recall@5 = **${f4(dR)}**，paraphrase Recall@5 = **${f4(pR)}**，差值 = **${f4(diff)}**。`);
  L.push('- 预期 direct 高、paraphrase 低：HashEmbedder 只做**词面匹配**（FNV-1a 词袋哈希 + char-bigram），');
  L.push('  paraphrase 靠换词/近义/跨语言表达，词面重叠少，语义召回够不着。');
  L.push('- 这正是 Phase 1 §14.3/14.4 的靶子——paraphrase 的语义缺口要靠**真实嵌入臂**与 **BM25+RRF hybrid** 补。');
  L.push('');

  // 重点结论 2：9 条纯 2 字中文 direct
  const twoChar = run.results.filter((r) => TWO_CHAR_CASES.includes(r.id));
  const twoCharHit = twoChar.filter((r) => r.hit5 === 1).length;
  const twoCharRecall = aggregate(twoChar);
  L.push('### 2. 9 条纯 2 字中文 direct 用例的召回');
  L.push('');
  L.push('验证向量 char-bigram 能否兜住 trigram 关键词通道够不着的 2 字词（G-004/G-008/G-009/G-010/G-013/G-015/G-016/G-018/G-019）。');
  L.push('');
  L.push(`- 命中（Hit@5=1）：**${twoCharHit}/${twoChar.length}**；这组 Recall@5 = **${f4(twoCharRecall.recall5)}**，MRR@10 = **${f4(twoCharRecall.mrr10)}**。`);
  L.push('');
  L.push('| case | query | expect | firstRank | top5 命中? | recall5 | rr10 | top5（截断） |');
  L.push('| --- | --- | --- | --- | --- | --- | --- | --- |');
  for (const r of twoChar) {
    const hitMark = r.hit5 ? '✓' : '✗';
    const rank = r.firstRank ? String(r.firstRank) : '—';
    L.push(
      `| ${r.id} | ${r.query} | ${r.expect.join(', ')} | ${rank} | ${hitMark} | ${f4(r.recall5)} | ${f4(r.rr10)} | ${r.top5.join(', ')} |`,
    );
  }
  L.push('');

  L.push('## 备注');
  L.push('');
  L.push('- **vector-only 基线**：只有 VectorRetriever（余弦）+ 确定性 HashEmbedder，无 BM25、无 hybrid、无 rerank。');
  L.push(`- **真实臂 ${meta.realArmPending ? 'pending' : '已跑'}**：${meta.realArm}`);
  L.push('- **先入库基线，才动优化**：本报告数字是 §14.3/14.4 优化前的对照基准，每个数字可由生成命令复现（HashEmbedder 确定性）。');
  L.push('');
  return L.join('\n');
}

function printConsole(run, meta) {
  const { overall, byKind, byLang, latency } = run;
  console.log('');
  console.log('════════ 检索基线评测（vector-only · Phase 1 §14.2）════════');
  console.log(`commit ${meta.commit} · Node ${meta.node} · ${meta.platform}/${meta.arch} · 臂 HashEmbedder(dim=${DEFAULT_DIM}) · topK=${TOP_K}`);
  console.log(`黄金集：${meta.cognitionCount} cognition / ${meta.caseCount} case`);
  console.log('');
  console.log('── 总体 ──');
  console.log(`overall   n=${overall.n}  Recall@5=${f4(overall.recall5)}  Hit@5=${f4(overall.hit5)}  MRR@10=${f4(overall.mrr10)}`);
  console.log('── 按 kind ──');
  for (const k of ['direct', 'paraphrase', 'multihop']) {
    const a = byKind[k];
    console.log(`${k.padEnd(11)} n=${a.n}  Recall@5=${f4(a.recall5)}  Hit@5=${f4(a.hit5)}  MRR@10=${f4(a.mrr10)}`);
  }
  console.log('── 按语言 ──');
  for (const l of ['zh', 'en']) {
    const a = byLang[l];
    console.log(`${l.padEnd(11)} n=${a.n}  Recall@5=${f4(a.recall5)}  Hit@5=${f4(a.hit5)}  MRR@10=${f4(a.mrr10)}`);
  }
  console.log('── latency（ms）──');
  console.log(`P50=${f3(latency.p50)}  P95=${f3(latency.p95)}`);
  console.log('');
  const dR = byKind.direct.recall5;
  const pR = byKind.paraphrase.recall5;
  console.log(`结论① direct Recall@5=${f4(dR)} vs paraphrase Recall@5=${f4(pR)}，差=${f4(dR - pR)}`);
  const twoChar = run.results.filter((r) => TWO_CHAR_CASES.includes(r.id));
  const twoCharHit = twoChar.filter((r) => r.hit5 === 1).length;
  console.log(`结论② 9 条纯 2 字中文 direct：命中 ${twoCharHit}/${twoChar.length}，Recall@5=${f4(aggregate(twoChar).recall5)}`);
  console.log('════════════════════════════════════════════════════════');
  console.log('');
}

// ══════════════════════════════════════════════════════════════════════════
// §14.6 三臂消融（vector-only / keyword-only / hybrid）
// ══════════════════════════════════════════════════════════════════════════

/** 三臂的 makeRetriever 工厂（每次 new 干净的 `:memory:` retriever）。顺序即报告行序。 */
const ABLATION_ARMS = [
  {
    key: 'vector-only',
    label: 'vector-only',
    desc: 'VectorRetriever（余弦）+ HashEmbedder（dim=256，确定性词袋哈希 + char-bigram）',
    make: () => new VectorRetriever(':memory:', new HashEmbedder()),
  },
  {
    key: 'keyword-only',
    label: 'keyword-only',
    desc: 'KeywordRetriever（FTS5 trigram 分词 + BM25 排序，纯词面/子串信号，无嵌入）',
    make: () => new KeywordRetriever(':memory:'),
  },
  {
    key: 'hybrid',
    label: 'hybrid',
    desc: 'HybridRetriever（RRF 融合 vector+keyword，kCandidate=50，rrfK=60）',
    make: () =>
      new HybridRetriever([
        new VectorRetriever(':memory:', new HashEmbedder()),
        new KeywordRetriever(':memory:'),
      ]),
  },
];

/** 三臂对比表（一个指标提取器 pick(agg) → 数字）：行=臂，末行 Δ(hybrid−vector)。 */
function armMetricTable(arms, pickAgg) {
  const L = [];
  L.push('| 臂 | n | Recall@5 | Hit@5 | MRR@10 |');
  L.push('| --- | --- | --- | --- | --- |');
  for (const a of arms) {
    const g = pickAgg(a.run);
    L.push(`| ${a.label} | ${g.n} | ${f4(g.recall5)} | ${f4(g.hit5)} | ${f4(g.mrr10)} |`);
  }
  const v = pickAgg(arms.find((a) => a.key === 'vector-only').run);
  const h = pickAgg(arms.find((a) => a.key === 'hybrid').run);
  L.push(
    `| **Δ hybrid−vector** | — | ${fDelta(h.recall5 - v.recall5)} | ${fDelta(h.hit5 - v.hit5)} | ${fDelta(h.mrr10 - v.mrr10)} |`,
  );
  return L;
}

function buildAblationReport(arms, meta) {
  const vector = arms.find((a) => a.key === 'vector-only').run;
  const keyword = arms.find((a) => a.key === 'keyword-only').run;
  const hybrid = arms.find((a) => a.key === 'hybrid').run;

  // ── 诊断事实（供诚实结论，皆确定性、可复现）──
  // keyword「点火」的 case：返回了至少一个候选（top5 非空）。FTS5 trigram phrase-match 需整条
  // spaceless query 连续命中某 doc → 自然语言/2 字中文 query 多数空召回。
  const kwFire = keyword.results.filter((r) => r.top5.length > 0);
  const kwFireIds = kwFire.map((r) => r.id);
  // hybrid 与 vector 的 top5 是否逐 case 相同（判断 RRF 在本离线配置下是否为 no-op）。
  const top5Diffs = vector.results.filter((vr) => {
    const hr = hybrid.results.find((x) => x.id === vr.id);
    return !hr || JSON.stringify(vr.top5) !== JSON.stringify(hr.top5);
  });
  const hybEqVec = top5Diffs.length === 0;
  // 各分组 hybrid−vector 的 Recall@5 Δ 绝对值最大者（判断是否「零增益」）。
  const groupRecallDeltas = [
    hybrid.overall.recall5 - vector.overall.recall5,
    ...['direct', 'paraphrase', 'multihop'].map((k) => hybrid.byKind[k].recall5 - vector.byKind[k].recall5),
    ...['zh', 'en'].map((l) => hybrid.byLang[l].recall5 - vector.byLang[l].recall5),
  ];
  const maxAbsDelta = Math.max(...groupRecallDeltas.map((d) => Math.abs(d)));
  const noGain = hybEqVec && maxAbsDelta < 1e-9;

  const L = [];
  L.push('# 检索三臂消融报告 — Phase 1 §14.6');
  L.push('');
  L.push('> vector-only / keyword-only / hybrid 三臂在黄金集上的对比，量 **hybrid 相对 vector-only');
  L.push('> 基线的增益**——判断是否值得把 hybrid 接进公共 API（§14.4b）的依据。所有确定性臂数字可由');
  L.push('> 生成命令逐位复现（HashEmbedder/BM25/RRF 均确定，无网络、无随机、无系统时钟）。');
  L.push('');

  // ── 生成环境 ──
  L.push('## 生成环境');
  L.push('');
  L.push('| 项 | 值 |');
  L.push('| --- | --- |');
  L.push(`| 生成命令 | \`${INVOKED_CMD}\` |`);
  L.push(`| commit | \`${meta.commit}\` |`);
  L.push(`| Node | ${meta.node} |`);
  L.push(`| 平台 | ${meta.platform}/${meta.arch} |`);
  L.push(`| 生成时间 | ${meta.generatedAt} |`);
  L.push(`| topK | ${TOP_K} |`);
  L.push(`| 黄金集 | tests/retrieval/golden.json（${meta.cognitionCount} 条 cognition，${meta.caseCount} 条 case） |`);
  L.push(`| 确定性自检 | ${arms.map((a) => `${a.label} ${a.determinismOk ? '✓' : '✗'}`).join(' / ')}（三臂各两遍逐位相等） |`);
  L.push(`| 真实臂 | ${meta.realArm} |`);
  L.push('');
  L.push('三臂定义：');
  L.push('');
  for (const a of arms) L.push(`- **${a.label}**：${a.desc}`);
  L.push('');

  // ── 三臂对比总表 ──
  L.push('## 一、三臂对比总表（overall）');
  L.push('');
  L.push(...armMetricTable(arms, (run) => run.overall));
  L.push('');
  const dOverallR = hybrid.overall.recall5 - vector.overall.recall5;
  L.push(
    `- hybrid overall Recall@5 = **${f4(hybrid.overall.recall5)}**，vector-only = **${f4(vector.overall.recall5)}**，Δ = **${fDelta(dOverallR)}**。`,
  );
  L.push(
    `- keyword-only 仅在 **${kwFire.length}/${meta.caseCount}** 条 case 上有候选（${kwFireIds.join(', ') || '无'}）——` +
      'FTS5 trigram phrase-match 需整条 query 连续命中某 doc，自然语言/2 字中文 query 多数空召回。',
  );
  L.push(
    `- hybrid 与 vector-only 的 top5 在 **${meta.caseCount - top5Diffs.length}/${meta.caseCount}** 条 case 上逐 case 相同` +
      `${hybEqVec ? '（**全同** → RRF 在本离线配置下是 no-op，详见「六、诚实结论」）' : `（${top5Diffs.length} 条不同：${top5Diffs.map((r) => r.id).join(', ')}）`}。`,
  );
  L.push('');

  // ── 按 kind ──（重点）
  L.push('## 二、按 kind 分组三臂对比（重点）');
  L.push('');
  for (const kind of ['direct', 'paraphrase', 'multihop']) {
    L.push(`### kind = ${kind}`);
    L.push('');
    L.push(...armMetricTable(arms, (run) => run.byKind[kind]));
    L.push('');
  }
  L.push('**Δ(hybrid − vector) 按 kind 汇总**（看 hybrid 在各 kind 抬了多少）：');
  L.push('');
  L.push('| kind | ΔRecall@5 | ΔHit@5 | ΔMRR@10 |');
  L.push('| --- | --- | --- | --- |');
  for (const kind of ['direct', 'paraphrase', 'multihop']) {
    const v = vector.byKind[kind];
    const h = hybrid.byKind[kind];
    L.push(`| ${kind} | ${fDelta(h.recall5 - v.recall5)} | ${fDelta(h.hit5 - v.hit5)} | ${fDelta(h.mrr10 - v.mrr10)} |`);
  }
  L.push('');

  // ── 按 lang ──
  L.push('## 三、按语言分组三臂对比（query 含 CJK=zh，否则 en）');
  L.push('');
  for (const lang of ['zh', 'en']) {
    L.push(`### lang = ${lang}`);
    L.push('');
    L.push(...armMetricTable(arms, (run) => run.byLang[lang]));
    L.push('');
  }

  // ── 9 条纯 2 字中文子集 ──
  L.push('## 四、9 条纯 2 字中文子集三臂表现');
  L.push('');
  L.push('子集：G-004/G-008/G-009/G-010/G-013/G-015/G-016/G-018/G-019（纯 2 字 direct）。');
  L.push('预期：vector 靠 char-bigram 兜住（≈1.0）；keyword 因 FTS5 trigram 需 ≥3 字符、够不着 2 字词（≈0）；');
  L.push('hybrid 看是否被 keyword 的空召回拖累、还是仍由 vector 兜住。');
  L.push('');
  L.push(...armMetricTable(arms, (run) => twoCharAgg(run)));
  L.push('');
  // 逐 case 三臂 Hit@5 对照（直观看 keyword 空、vector/hybrid 是否兜住）
  const hitOf = (run, id) => (run.results.find((r) => r.id === id)?.hit5 === 1 ? '✓' : '✗');
  const rankOf = (run, id) => {
    const fr = run.results.find((r) => r.id === id)?.firstRank ?? 0;
    return fr ? String(fr) : '—';
  };
  L.push('| case | query | expect | vec Hit@5 | kw Hit@5 | hyb Hit@5 | hyb firstRank |');
  L.push('| --- | --- | --- | --- | --- | --- | --- |');
  for (const id of TWO_CHAR_CASES) {
    const c = vector.results.find((r) => r.id === id);
    L.push(
      `| ${id} | ${c.query} | ${c.expect.join(', ')} | ${hitOf(vector, id)} | ${hitOf(keyword, id)} | ${hitOf(hybrid, id)} | ${rankOf(hybrid, id)} |`,
    );
  }
  L.push('');
  const vecTwo = twoCharAgg(vector);
  const kwTwo = twoCharAgg(keyword);
  const hybTwo = twoCharAgg(hybrid);
  L.push(
    `- vector-only 子集 Recall@5 = **${f4(vecTwo.recall5)}**，keyword-only = **${f4(kwTwo.recall5)}**，hybrid = **${f4(hybTwo.recall5)}**。`,
  );
  if (hybTwo.recall5 >= vecTwo.recall5 - 1e-9) {
    L.push('- **hybrid 未被 keyword 的空召回拖累**：RRF 融合下 keyword 对 2 字词无候选贡献，vector 的名次照常进入融合，hybrid 仍靠 vector 兜住这组（子集内 keyword-only Recall@5=' + f4(kwTwo.recall5) + '，印证 trigram 够不着 2 字词）。');
  } else {
    L.push(`- **hybrid 被 keyword 拖累**：子集 Recall@5 从 vector 的 ${f4(vecTwo.recall5)} 掉到 ${f4(hybTwo.recall5)}（keyword 空召回稀释了 RRF 排序）。`);
  }
  L.push('');

  // ── +10% 判定 ──
  L.push('## 五、对 +10% 目标的判定');
  L.push('');
  L.push(`- 基线 overall Recall@5 = **${f4(BASELINE_OVERALL_RECALL5)}**（committed retrieval-baseline.md，vector-only）。`);
  L.push(`- +10% 目标线 = ${f4(BASELINE_OVERALL_RECALL5)} × 1.10 = ${(BASELINE_OVERALL_RECALL5 * 1.1).toFixed(5)} → **≥ ${f4(TARGET_RECALL5)}**。`);
  L.push(`- 本次确定性 hybrid overall Recall@5 = **${f4(hybrid.overall.recall5)}**。`);
  const reached = hybrid.overall.recall5 >= TARGET_RECALL5;
  if (reached) {
    L.push(`- **判定：达标 ✓** — 确定性 hybrid（${f4(hybrid.overall.recall5)}）≥ 目标（${f4(TARGET_RECALL5)}）。`);
  } else {
    L.push(`- **判定：未达标 ✗** — 确定性 hybrid（${f4(hybrid.overall.recall5)}）< 目标（${f4(TARGET_RECALL5)}），缺口 **${f4(TARGET_RECALL5 - hybrid.overall.recall5)}**。`);
    L.push('- **+10% 的达成主要落在真实嵌入臂**，待联网 nightly 补测（见下「诚实结论」）。不粉饰：确定性两臂都是词面/子串信号，抬不动语义/跨语言缺口。');
  }
  L.push('');

  // ── 诚实结论（数据驱动，不预设方向、不粉饰）──
  L.push('## 六、诚实结论');
  L.push('');
  const dParaR = hybrid.byKind.paraphrase.recall5 - vector.byKind.paraphrase.recall5;
  const dEnR = hybrid.byLang.en.recall5 - vector.byLang.en.recall5;
  if (noGain) {
    L.push(
      '- **确定性 hybrid ≡ vector-only 基线（零增益）**：三臂对比中 hybrid 相对 vector 的 Recall@5 Δ 在 overall 及全部 kind/lang 分组上均为 **+0.0000**，' +
        `且两者 top5 在全部 ${meta.caseCount} 条 case 上逐 case 相同——RRF 融合在本离线配置下是 **no-op**。这是如实测量结果，未凭空造增益、也未掩盖。`,
    );
    L.push(
      `- **为什么是 no-op**：keyword-only 仅在 ${kwFire.length}/${meta.caseCount} 条 case 上有候选（${kwFireIds.join(', ') || '无'}，均为「整条 query 恰是某 doc 子串」的关键词式 direct），` +
        '且这几条命中的 doc 恰是 vector 已排 #1 的 doc → RRF 只是叠加确认，top5 不变；其余 case keyword 空召回，hybrid 完全退化为 vector。',
    );
    L.push(
      '- **确定性 hybrid 的天花板 = vector-only 基线**：keyword（FTS5 trigram/BM25）与 HashEmbedder（char-bigram）两条离线臂**本质同源**——都靠字面/子串重叠，' +
        '且 keyword 能命中处 vector 必也命中；RRF 只能重排两臂各自能召回的 doc，无法凭空生出 vector 之外的召回，故在此黄金集上抬不动任何一格。',
    );
  } else {
    const dDirectR = hybrid.byKind.direct.recall5 - vector.byKind.direct.recall5;
    const dMultihopR = hybrid.byKind.multihop.recall5 - vector.byKind.multihop.recall5;
    L.push(
      `- **确定性 hybrid 相对 vector 有位移**：direct ΔRecall@5=${fDelta(dDirectR)}、multihop ΔRecall@5=${fDelta(dMultihopR)}、` +
        `paraphrase ΔRecall@5=${fDelta(dParaR)}（top5 在 ${top5Diffs.length} 条 case 上与 vector 不同）——` +
        'keyword 的 BM25 子串信号与 vector char-bigram 在 RRF 下互补，词面命中的 doc 名次叠加上浮。',
    );
  }
  L.push(
    `- **paraphrase 与 en 是语义/跨语言缺口，确定性 hybrid 抬不动**：paraphrase vector 基线仅 **${f4(vector.byKind.paraphrase.recall5)}**（Δhyb=${fDelta(dParaR)}）、` +
      `en vector 基线仅 **${f4(vector.byLang.en.recall5)}**（Δhyb=${fDelta(dEnR)}）——换词/近义/翻译后词面重叠稀薄，keyword 与 HashEmbedder 两条**词面/子串信号**都够不着。`,
  );
  L.push(
    '- **语义/跨语言缺口需真实嵌入臂**（pending）：设 `EVAL_REAL_ARM=1`、配 `.env` 的 `MEMOWEFT_EMBED_*` 并联网后，' +
      '用真实嵌入替换 HashEmbedder 通道（real-vector + real-hybrid），才可能补 paraphrase/en。**待联网 nightly 补测**。',
  );
  if (!reached) {
    L.push(
      `- **+10% 目标落在真实臂**：确定性 hybrid（overall Recall@5=${f4(hybrid.overall.recall5)}）**未达** ≥${f4(TARGET_RECALL5)}` +
        `（缺口 ${f4(TARGET_RECALL5 - hybrid.overall.recall5)}）；本报告不将其记为已达标，+10% 的达成主要落在真实嵌入臂，待联网 nightly 补测。`,
    );
  } else {
    L.push(`- **+10% 目标**：确定性 hybrid（overall Recall@5=${f4(hybrid.overall.recall5)}）已达 ≥${f4(TARGET_RECALL5)}；仍需核实增益来源是否为语义召回而非词面巧合。`);
  }
  L.push('');

  // ── 备注 ──
  L.push('## 备注');
  L.push('');
  L.push('- **确定性臂**：vector-only（HashEmbedder）/ keyword-only（FTS5 BM25）/ hybrid（RRF），全部离线、无网络、无随机、无系统时钟；每个数字可由生成命令逐位复现。');
  L.push(`- **真实臂仍 opt-in**：${meta.realArm}`);
  L.push('- **API 决策交回 Integrator 守门**：本报告只呈现数据（增益 Δ、+10% 判定），是否把 hybrid 接进公共 API（§14.4b）由 Integrator 裁决，评测不代拍板。');
  L.push('- 默认 `node bench/eval-retrieval.mjs` 仍产出 vector-only 基线（retrieval-baseline.md），行为与数字不变。');
  L.push('');
  return L.join('\n');
}

function printAblationConsole(arms, meta) {
  const vector = arms.find((a) => a.key === 'vector-only').run;
  const hybrid = arms.find((a) => a.key === 'hybrid').run;
  console.log('');
  console.log('════════ 检索三臂消融评测（vector/keyword/hybrid · Phase 1 §14.6）════════');
  console.log(`commit ${meta.commit} · Node ${meta.node} · ${meta.platform}/${meta.arch} · topK=${TOP_K}`);
  console.log(`黄金集：${meta.cognitionCount} cognition / ${meta.caseCount} case`);
  console.log('');
  console.log('── overall（Recall@5 / Hit@5 / MRR@10）──');
  for (const a of arms) {
    const g = a.run.overall;
    console.log(`${a.label.padEnd(13)} Recall@5=${f4(g.recall5)}  Hit@5=${f4(g.hit5)}  MRR@10=${f4(g.mrr10)}`);
  }
  console.log(`Δ hybrid−vector  Recall@5=${fDelta(hybrid.overall.recall5 - vector.overall.recall5)}`);
  console.log('── 按 kind：Recall@5（vec / kw / hyb / Δhyb-vec）──');
  const kw = arms.find((a) => a.key === 'keyword-only').run;
  for (const k of ['direct', 'paraphrase', 'multihop']) {
    const v = vector.byKind[k].recall5;
    const h = hybrid.byKind[k].recall5;
    console.log(`${k.padEnd(11)} ${f4(v)} / ${f4(kw.byKind[k].recall5)} / ${f4(h)} / ${fDelta(h - v)}`);
  }
  console.log('── 9 条 2 字中文子集：Recall@5（vec / kw / hyb）──');
  console.log(`${f4(twoCharAgg(vector).recall5)} / ${f4(twoCharAgg(kw).recall5)} / ${f4(twoCharAgg(hybrid).recall5)}`);
  console.log('── +10% 目标 ──');
  console.log(`基线 ${f4(BASELINE_OVERALL_RECALL5)} → 目标 ≥${f4(TARGET_RECALL5)}；hybrid=${f4(hybrid.overall.recall5)} → ${hybrid.overall.recall5 >= TARGET_RECALL5 ? '达标 ✓' : '未达标 ✗'}`);
  console.log('════════════════════════════════════════════════════════════════════');
  console.log('');
}

// ══════════════════════════════════════════════════════════════════════════
// 入口
// ══════════════════════════════════════════════════════════════════════════

function collectMeta(cognitions, cases) {
  return {
    commit: (() => {
      try {
        return execSync('git rev-parse --short HEAD', { cwd: HERE }).toString().trim();
      } catch {
        return 'unknown';
      }
    })(),
    node: process.versions.node,
    platform: process.platform,
    arch: process.arch,
    generatedAt: new Date().toISOString(),
    cognitionCount: cognitions.length,
    caseCount: cases.length,
  };
}

/** 真实臂配置探测（opt-in，默认关）。返回 { wantRealArm, embedCfg }。 */
function resolveRealArm() {
  // --require-real-arm 蕴含「请求真实臂」：否则「没请求」也会被算作 pending 而失败，
  // 退出码虽对、打印的原因却是「真实臂 off（默认离线）」——答非所问。带上它就是要真跑。
  const wantRealArm =
    process.env.EVAL_REAL_ARM === '1' ||
    process.argv.includes('--real') ||
    process.argv.includes('--require-real-arm');
  const embedCfg = wantRealArm ? loadEmbedConfig() : null;
  return { wantRealArm, embedCfg };
}

/** 默认模式：vector-only 基线，落 retrieval-baseline.md（行为与数字不变）。--out 给则改落 <prefix>.md。 */
async function mainBaseline(cognitions, cases, meta, outPrefix) {
  // ── 确定性自检：跑两遍，逐位比对指标（latency 除外）──
  const makeBaseline = () => new VectorRetriever(':memory:', new HashEmbedder());
  const run1 = await runEvalWith(makeBaseline, cognitions, cases);
  const run2 = await runEvalWith(makeBaseline, cognitions, cases);
  const sig1 = deterministicSig(run1);
  const sig2 = deterministicSig(run2);
  const determinismOk = sig1 === sig2;
  meta.determinismOk = determinismOk;
  if (!determinismOk) {
    console.error('[eval-retrieval] ✗ 确定性自检失败：两遍指标不逐位相等（HashEmbedder 应为确定性）。');
    console.error('run1:', sig1);
    console.error('run2:', sig2);
    process.exit(1);
  }
  console.log('[eval-retrieval] ✓ 确定性自检通过：两遍指标逐位相等。');

  // ── 真实臂（OpenAICompatEmbedder）：opt-in，默认关（默认纯离线、不打网络，§14.1）──
  const { wantRealArm, embedCfg } = resolveRealArm();
  if (embedCfg) {
    try {
      console.log(`[eval-retrieval] 真实臂：OpenAICompatEmbedder（model=${embedCfg.model}）跑黄金集…`);
      const realRun = await runEvalWith(
        () => new VectorRetriever(':memory:', new OpenAICompatEmbedder(embedCfg)),
        cognitions,
        cases,
      );
      meta.realArm = `OpenAICompatEmbedder（model=${embedCfg.model}）overall Recall@5=${f4(realRun.overall.recall5)} Hit@5=${f4(realRun.overall.hit5)} MRR@10=${f4(realRun.overall.mrr10)}`;
      meta.realArmPending = false;
      console.log(`[eval-retrieval] 真实臂 overall Recall@5=${f4(realRun.overall.recall5)} Hit@5=${f4(realRun.overall.hit5)} MRR@10=${f4(realRun.overall.mrr10)}`);
    } catch (err) {
      meta.realArm = `配置存在但调用失败（${err instanceof Error ? err.message : String(err)}）— pending`;
      meta.realArmPending = true;
      console.error('[eval-retrieval] 真实臂调用失败：', err instanceof Error ? err.message : err);
    }
  } else if (wantRealArm) {
    console.log('[eval-retrieval] 真实臂已请求，但无 embed 配置（.env 缺 MEMOWEFT_EMBED_*）');
    meta.realArm = '请求（EVAL_REAL_ARM/--real）但无 embed 配置 — pending';
    meta.realArmPending = true;
  } else {
    console.log('[eval-retrieval] 真实臂 off（默认离线；设 EVAL_REAL_ARM=1 或 --real 且有 .env MEMOWEFT_EMBED_* 以启用）');
    meta.realArm = 'off（默认离线确定；设 EVAL_REAL_ARM=1 或 --real 启用真实嵌入臂）';
    meta.realArmPending = true;
  }

  // ── 报告：终端 + 落盘 ──
  printConsole(run1, meta);
  const report = buildReport(run1, meta);
  const outPath = outPrefix ? `${outPrefix}.md` : REPORT_PATH;
  writeFileSync(outPath, report, 'utf8');
  console.log(`[eval-retrieval] 报告已写入 ${outPath}`);
}

/** 消融模式：三臂（vector/keyword/hybrid），各自确定性自检，落 retrieval-after.md。--out 给则改落 <prefix>.md。 */
async function mainAblation(cognitions, cases, meta, outPrefix) {
  console.log('[eval-retrieval] 三臂消融（§14.6）：vector-only / keyword-only / hybrid');
  const arms = [];
  for (const arm of ABLATION_ARMS) {
    const { run, determinismOk } = await runArmWithSelfCheck(arm.key, arm.make, cognitions, cases);
    arms.push({ ...arm, run, determinismOk });
  }

  // ── 真实臂（opt-in）：默认离线、pending；置位供报告 meta ──
  const { wantRealArm, embedCfg } = resolveRealArm();
  if (embedCfg) {
    // 真实臂在消融中仍 opt-in：跑一遍 real-vector + real-hybrid（无 2 遍自检，避免双倍网络成本）。
    try {
      console.log(`[eval-retrieval] 真实臂（消融，opt-in）：OpenAICompatEmbedder（model=${embedCfg.model}）…`);
      const realVec = await runEvalWith(
        () => new VectorRetriever(':memory:', new OpenAICompatEmbedder(embedCfg)),
        cognitions,
        cases,
      );
      const realHyb = await runEvalWith(
        () =>
          new HybridRetriever([
            new VectorRetriever(':memory:', new OpenAICompatEmbedder(embedCfg)),
            new KeywordRetriever(':memory:'),
          ]),
        cognitions,
        cases,
      );
      meta.realArm =
        `opt-in 已跑（model=${embedCfg.model}）：real-vector overall Recall@5=${f4(realVec.overall.recall5)}，` +
        `real-hybrid overall Recall@5=${f4(realHyb.overall.recall5)}（非确定，未做两遍自检）`;
      meta.realArmPending = false;
      console.log(`[eval-retrieval] 真实臂 real-vector Recall@5=${f4(realVec.overall.recall5)} / real-hybrid Recall@5=${f4(realHyb.overall.recall5)}`);
    } catch (err) {
      meta.realArm = `opt-in 请求但调用失败（${err instanceof Error ? err.message : String(err)}）— pending，待联网 nightly`;
      meta.realArmPending = true;
      console.error('[eval-retrieval] 真实臂调用失败：', err instanceof Error ? err.message : err);
    }
  } else if (wantRealArm) {
    meta.realArm = 'opt-in 请求（EVAL_REAL_ARM/--real）但无 embed 配置（.env 缺 MEMOWEFT_EMBED_*）— pending，待联网 nightly';
    meta.realArmPending = true;
  } else {
    meta.realArm = 'opt-in（EVAL_REAL_ARM=1 + .env MEMOWEFT_EMBED_* + 联网），默认离线 — pending，待联网 nightly 补测';
    meta.realArmPending = true;
  }

  printAblationConsole(arms, meta);
  const report = buildAblationReport(arms, meta);
  const outPath = outPrefix ? `${outPrefix}.md` : AFTER_REPORT_PATH;
  writeFileSync(outPath, report, 'utf8');
  console.log(`[eval-retrieval] 三臂消融报告已写入 ${outPath}`);
}

/** 解析 --out <prefix>（不给返回 null；给了空 / 下一个是 flag → 报错早退，防手滑落成怪路径）。 */
function parseOutPrefix(argv) {
  const i = argv.indexOf('--out');
  if (i < 0) return null;
  const raw = argv[i + 1];
  if (!raw || raw.startsWith('--')) {
    console.error(`[eval-retrieval] --out 需要一个产物路径前缀（收到: ${raw ?? '(空)'}）。`);
    process.exit(1);
  }
  return raw;
}

async function main() {
  const golden = JSON.parse(readFileSync(GOLDEN_PATH, 'utf8'));
  const { cognitions, cases } = golden;
  const meta = collectMeta(cognitions, cases);
  const outPrefix = parseOutPrefix(process.argv);

  if (process.argv.includes('--ablation')) {
    await mainAblation(cognitions, cases, meta, outPrefix);
  } else {
    await mainBaseline(cognitions, cases, meta, outPrefix);
  }

  // --require-real-arm（供 test:live）：它蕴含「请求真实臂」（见 resolveRealArm），故走到这里 pending
  //   只可能是【无 embed 配置】或【调用失败】——两者都该判失败：test:live 不能因为 embed 端点挂了就悄悄变绿。
  //   不给此标志时 pending 不算失败（默认离线跑照旧 exit 0，行为同旧）。meta.realArm* 由上面两个 main 置位。
  if (process.argv.includes('--require-real-arm') && meta.realArmPending) {
    console.error('');
    console.error('[eval-retrieval] ✗ --require-real-arm：真实嵌入臂 pending → 判定为失败（exit 1）。');
    console.error(`  原因：${meta.realArm}`);
    console.error('  排查：需 EVAL_REAL_ARM=1 或 --real 请求真实臂，且 .env 配 MEMOWEFT_EMBED_BASE_URL/_API_KEY/_MODEL（含 DLA_ 回退）且端点可达。');
    process.exit(1);
  }
}

main().catch((err) => {
  console.error('[eval-retrieval] 失败：', err);
  process.exit(1);
});
