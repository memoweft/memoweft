/**
 * Recall-latency benchmark for an explicitly configured VectorRetriever and embedding endpoint.
 *
 * The NullRetriever arm is explicitly injected as a storage-path control; it is not the Core's normal no-embedder fallback,
 * which uses local FTS5 keyword recall. The control excludes semantic computation, while the vector arm includes query embedding.
 *   Endpoint-backed recall includes query embedding, so endpoint latency can materially affect the result.
 *   This script reports P50/P95 alongside a same-machine NullRetriever storage-path control.
 *
 * Imports TypeScript source directly and requires Node.js 24+.
 *
 * Measurements:
 *   1) VectorRetriever + configured embedder: core.recall(query) end-to-end latency, sampled as min/P50/P95/max/mean.
 *      Each round embeds the query, so endpoint-backed results include that request.
 *   2) NullRetriever (storage-path control): same cognitions and core.recall entry point, but an explicitly empty retriever.
 *
 * The runner stores N high-confidence fact cognitions, indexes them with
 * VectorRetriever, and opens Core on the same temporary database. Recall then
 * exercises retriever search plus cognition validity and confidence filters.
 *
 * Data hygiene: uses a temporary database directory and removes it in `finally`.
 *
 * Usage:
 *   node bench/perf-recall.mjs --selftest              # offline path check with HashEmbedder
 *   node bench/perf-recall.mjs                         # endpoint-backed mode: reads MEMOWEFT_EMBED_* / DLA_EMBED_*
 *   PERF_RECALL_N=1000 PERF_RECALL_ROUNDS=50 node bench/perf-recall.mjs
 */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { config } from '../src/config.ts';
import { openStores } from '../src/store/openStores.ts';
import { createMemoWeftCore } from '../src/core/createCore.ts';
import { VectorRetriever } from '../src/retrieval/vectorRetriever.ts';
import { NullRetriever } from '../src/retrieval/nullRetriever.ts';
import { OpenAICompatEmbedder, loadEmbedConfig } from '../src/retrieval/embedder.ts';
import { HashEmbedder } from '../tests/retrieval/hashEmbedder.ts';

// CLI
const argv = process.argv.slice(2);
const SELFTEST = argv.includes('--selftest');
const N = Number(process.env.PERF_RECALL_N) || (SELFTEST ? 60 : 500);
const ROUNDS = Number(process.env.PERF_RECALL_ROUNDS) || (SELFTEST ? 20 : 30);
const SUBJECT = config.identity.subjectId;
const TOP_K = config.retrieval.topK;

// Synthetic corpus with lexical and semantic overlap
const TOPICS = ['编程', '摄影', '烹饪', '旅行', '音乐', '健身', '阅读', '绘画', '园艺', '天文'];
const PLACES = ['公园', '海边', '山里', '图书馆', '咖啡馆', '博物馆', '市场', '河边'];
const MOODS = ['放松', '专注', '兴奋', '平静', '好奇'];
function corpusText(i) {
  const t = TOPICS[i % TOPICS.length];
  const p = PLACES[i % PLACES.length];
  const m = MOODS[i % MOODS.length];
  return `记忆条目 #${i}：我很喜欢${t}，周末常去${p}，做这件事时感到${m}。`;
}
function queryText(r) {
  const t = TOPICS[r % TOPICS.length];
  const p = PLACES[(r + 3) % PLACES.length];
  return `我周末想在${p}附近找点跟${t}有关的事做，有什么建议？`;
}

// Chunk large embedding requests while preserving usage counters.
class ChunkingEmbedder {
  constructor(inner, chunk = 32) {
    this.inner = inner;
    this.chunk = chunk;
  }
  get callCount() {
    return this.inner.callCount;
  }
  get usage() {
    return this.inner.usage;
  }
  async embed(texts) {
    const out = [];
    for (let i = 0; i < texts.length; i += this.chunk) {
      out.push(...(await this.inner.embed(texts.slice(i, i + this.chunk))));
    }
    return out;
  }
}

// Statistics
function percentile(sortedAsc, p) {
  if (sortedAsc.length === 0) return NaN;
  const rank = Math.ceil((p / 100) * sortedAsc.length);
  return sortedAsc[Math.min(sortedAsc.length - 1, Math.max(0, rank - 1))];
}
function fmt(ms) {
  return `${ms.toFixed(1)} ms`;
}
function stats(samples) {
  const s = [...samples].sort((a, b) => a - b);
  const mean = s.reduce((a, b) => a + b, 0) / s.length;
  return { min: s[0], p50: percentile(s, 50), p95: percentile(s, 95), max: s[s.length - 1], mean };
}

// Seed cognitions and build the vector index.
async function seed(dbFile, embedder) {
  const stores = openStores(dbFile, config);
  const cogs = [];
  try {
    for (let i = 0; i < N; i++) {
      // High-confidence stated facts pass the standard recall filters.
      const c = stores.cognitionStore.put({
        subjectId: SUBJECT,
        content: corpusText(i),
        contentType: 'fact',
        formedBy: 'stated',
        confidence: 900,
        credStatus: 'stable',
      });
      cogs.push({ id: c.id, text: c.content });
    }
  } finally {
    stores.close();
  }
  // Index identifiers map directly to cognition identifiers.
  const vr = new VectorRetriever(dbFile, embedder);
  const t0 = performance.now();
  await vr.indexAll(cogs);
  const indexMs = performance.now() - t0;
  vr.close();
  return { indexMs, count: cogs.length };
}

// Measure core.recall latency for one retriever configuration.
async function measureRecall(makeCore) {
  const core = makeCore();
  try {
    // 预热一轮（首轮含连接/prepare 冷启动，不计入统计）。
    await core.recall({ query: queryText(0) });
    const samples = [];
    let lastHits = 0;
    for (let r = 0; r < ROUNDS; r++) {
      const t0 = performance.now();
      const res = await core.recall({ query: queryText(r) });
      samples.push(performance.now() - t0);
      lastHits = res.length;
    }
    return { ...stats(samples), hits: lastHits };
  } finally {
    core.close();
  }
}

async function main() {
  console.log(
    `[perf-recall] Node ${process.versions.node} · ${process.platform}/${process.arch} · mode=${SELFTEST ? 'SELFTEST(offline)' : 'ENDPOINT-BACKED'}`,
  );
  console.log(
    `[perf-recall] N=${N} 认知 · recall 采样 ${ROUNDS} 轮 · topK=${TOP_K} · subject='${SUBJECT}'`,
  );

  // 嵌入器：selftest 走确定性 HashEmbedder（离线）；另一路显式读取端点配置。
  let rawEmbedder;
  let embedderLabel;
  if (SELFTEST) {
    rawEmbedder = new HashEmbedder();
    embedderLabel = 'HashEmbedder';
  } else {
    const cfg = loadEmbedConfig();
    if (!cfg) {
      console.error(
        '[perf-recall] 未配 embedder（MEMOWEFT_EMBED_* / DLA_EMBED_*）。端点模式需要显式配置；离线检查请加 --selftest。',
      );
      process.exit(2);
    }
    console.log(`[perf-recall] embedder: ${cfg.model} @ ${cfg.baseUrl}`);
    rawEmbedder = new OpenAICompatEmbedder(cfg);
    embedderLabel = cfg.model;
  }
  const embedder = new ChunkingEmbedder(rawEmbedder, 32);

  // Verify endpoint connectivity with one embedding request.
  if (!SELFTEST) {
    try {
      const probe = await embedder.embed(['探活']);
      if (!probe[0]?.length) throw new Error('嵌入返回空向量');
      console.log(`[perf-recall] Endpoint check passed（向量维度 ${probe[0].length}）。`);
    } catch (e) {
      console.error(`[perf-recall] embedder 端点不可达：${e?.message || e}`);
      console.error(
        '[perf-recall] Retry `node bench/perf-recall.mjs` when the endpoint is available.',
      );
      process.exit(3);
    }
  }

  const dir = mkdtempSync(join(tmpdir(), 'memoweft-perf-recall-'));
  const dbFile = join(dir, 'bench.db');
  try {
    // 1) 种 N 条认知 + 向量索引（一次性成本）。
    const { indexMs, count } = await seed(dbFile, embedder);
    console.log(
      `[perf-recall] 种入 ${count} 条认知 + 建向量索引：${fmt(indexMs)}（一次性，与召回延迟无关）。`,
    );

    // 2) VectorRetriever：core 自建 VectorRetriever（读同库的 vectors 表）。
    const vec = await measureRecall(() => createMemoWeftCore({ dbPath: dbFile, embedder }));
    console.log(
      '[perf-recall] ── VectorRetriever + ' +
        embedderLabel +
        (SELFTEST ? '（离线路径自检）' : '（含 query 嵌入请求）') +
        ' ──',
    );
    console.log(`               每轮命中认知数：${vec.hits}（topK=${TOP_K}）`);
    console.log(
      `               min ${fmt(vec.min)} · P50 ${fmt(vec.p50)} · P95 ${fmt(vec.p95)} · max ${fmt(vec.max)} · mean ${fmt(vec.mean)}`,
    );

    // 3) NullRetriever 存储层基线（同一批认知、同一 core.recall 入口，召回恒空）→ 复现文档 ≈0ms。
    const nul = await measureRecall(() =>
      createMemoWeftCore({ dbPath: dbFile, retriever: new NullRetriever() }),
    );
    console.log('[perf-recall] ── NullRetriever（存储层基线·无 embedder·召回恒空）──');
    console.log(`               每轮命中认知数：${nul.hits}（预期 0）`);
    console.log(`               P50 ${fmt(nul.p50)} · P95 ${fmt(nul.p95)} · mean ${fmt(nul.mean)}`);

    // 4) Compact summary for a run log.
    console.log('');
    console.log('[perf-recall] ── Summary ──');
    console.log(
      `        ${count} 条认知 · VectorRetriever+${embedderLabel}：recall P50 ≈ ${vec.p50.toFixed(1)} ms · P95 ≈ ${vec.p95.toFixed(1)} ms（${ROUNDS} 轮）`,
    );
    console.log(`        对照 NullRetriever（存储层·召回空）：P50 ≈ ${nul.p50.toFixed(2)} ms`);
    console.log(
      `        环境：Node ${process.versions.node} · ${process.platform}/${process.arch}${SELFTEST ? '' : ` · embedder=${embedderLabel}`}`,
    );

    // 5) Offline assertions for retrieval, latency statistics, and the null control.
    if (SELFTEST) {
      const fails = [];
      if (!(vec.hits > 0))
        fails.push(`Vector 档命中数应 >0，实得 ${vec.hits}（种入/索引/召回链断了）`);
      if (!Number.isFinite(vec.p50) || !Number.isFinite(vec.p95))
        fails.push('Vector 档 P50/P95 非有限值');
      if (nul.hits !== 0) fails.push(`NullRetriever 档命中数应 =0，实得 ${nul.hits}`);
      if (fails.length) {
        console.error('[perf-recall] SELFTEST 失败：\n  - ' + fails.join('\n  - '));
        process.exit(1);
      }
      console.log('[perf-recall] SELFTEST 通过 ✓（召回链通、P50/P95 有值、Null 档恒空）。');
    }
  } finally {
    // 数据卫生：删临时库目录（含 -wal/-shm 若有）。
    rmSync(dir, { recursive: true, force: true });
  }
}

main().catch((err) => {
  console.error('[perf-recall] 失败：', err);
  process.exitCode = 1;
});
