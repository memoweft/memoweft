/**
 * 召回延迟基准（Phase 5 · §18.5 降级项）：配【真实向量召回器 + bge-m3 嵌入】测 core.recall 的端到端延迟。
 *
 * 为什么单独一支：docs/internals/perf.md 里 recall≈0ms 是 **NullRetriever**（无 embedder → 召回恒空）的数字——
 *   量的是"召回入口链本身"的存储层开销，不含任何语义计算。想评估"真实召回要多久"的读者会被误导：
 *   真实语义召回的耗时【被 embedder 的网络往返主导】（query 要先嵌入，再对库内向量算余弦）。
 *   本脚本把这条真实路径量出来（P50/P95），并与 NullRetriever 的存储层 ≈0ms 做同机对照——不做不对等比较。
 *
 * 直接从 src 的 .ts import（Node ≥24 原生剥类型，无需 build；与 bench/locomo-eval.mjs 同款）。只读依赖，绝不改 src/tests。
 *
 * 量什么（两档同机对照）：
 *   1) VectorRetriever + bge-m3（真实语义召回）：core.recall(query) 端到端延迟，多轮采样取 min/P50/P95/max/mean。
 *      每轮召回都真打一次 embedder（嵌入 query），所以量到的是【真实系统在跑的那条召回】要多久。
 *   2) NullRetriever（存储层基线）：同一批认知、同一个 core.recall 入口，但召回器恒返回空 → 复现文档里的 ≈0ms。
 *
 * 怎么种数据（不碰 src/api，只用公共构造件）：
 *   - 先用 openStores 直接把 N 条认知 put 进 cognition 表（fact/stated/高置信 → 过全部召回门控）。
 *   - 再用 VectorRetriever.indexAll 把这 N 条的向量灌进 vectors 表（真 bge-m3 嵌入，一次性成本，与召回延迟无关）。
 *   - 然后 createMemoWeftCore 在【同一个库文件】上开自己的 stores + VectorRetriever，读到上面种的数据，
 *     于是 core.recall(query) 走的是【真实门面路径】（recallCognitions：retriever.search → 相似度/失效/越界/衰减门控）。
 *
 * 数据卫生（守记忆「冒烟数据必清」）：用 scratchpad 下的一次性临时库文件，finally 删除，不落任何库文件到仓库。
 *
 * 用法：
 *   node bench/perf-recall.mjs --selftest              # 离线自证（HashEmbedder，不打网络；断言召回链通 + P50/P95 有值）
 *   node bench/perf-recall.mjs                         # 真实档：读 .env 的 MEMOWEFT_EMBED_ * / DLA_EMBED_ *（bge-m3）
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

// ── CLI / 参数 ────────────────────────────────────────────────────────────────
const argv = process.argv.slice(2);
const SELFTEST = argv.includes('--selftest');
const N = Number(process.env.PERF_RECALL_N) || (SELFTEST ? 60 : 500);
const ROUNDS = Number(process.env.PERF_RECALL_ROUNDS) || (SELFTEST ? 20 : 30);
const SUBJECT = config.identity.subjectId; // 'owner'（默认）——种入认知与 core.recall 的 subject 必须一致，否则越界门控滤掉
const TOP_K = config.retrieval.topK; // 5（默认）

// ── 语料：小词表拼句，保证 query 与语料有语义/词面重叠（bge-m3 靠语义、HashEmbedder 靠词面）──
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

// ── 分批嵌入包装：把大 batch 拆成小请求，绕开某些 embedder 服务端的大 batch 退化/上限；透传 usage/callCount。──
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

// ── 统计 ──────────────────────────────────────────────────────────────────────
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

// ── 种认知 + 建向量索引（真嵌入一次性成本）──────────────────────────────────────
async function seed(dbFile, embedder) {
  const stores = openStores(dbFile, config);
  const cogs = [];
  try {
    for (let i = 0; i < N; i++) {
      // fact + stated + 高置信 → 过全部召回门控：fact 不衰减（config.background.halfLifeDays 未列 fact），
      //   confidence 900 ≥ minEffectiveConfidence(80)，未 invalid/archived/muted，subjectId 对齐。
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
  // 向量索引：id 与认知一一对应（search 命中 id → cognitionStore.get 取回同条），真 bge-m3 嵌入 N 条（一次性）。
  const vr = new VectorRetriever(dbFile, embedder);
  const t0 = performance.now();
  await vr.indexAll(cogs);
  const indexMs = performance.now() - t0;
  vr.close();
  return { indexMs, count: cogs.length };
}

// ── 测一档 core.recall 延迟（retriever 由 core 依 embedder/注入解析）─────────────
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
  console.log(`[perf-recall] Node ${process.versions.node} · ${process.platform}/${process.arch} · mode=${SELFTEST ? 'SELFTEST(offline)' : 'REAL(bge-m3)'}`);
  console.log(`[perf-recall] N=${N} 认知 · recall 采样 ${ROUNDS} 轮 · topK=${TOP_K} · subject='${SUBJECT}'`);

  // 嵌入器：selftest 走确定性 HashEmbedder（离线）；真实档读 env 的 bge-m3。
  let rawEmbedder;
  if (SELFTEST) {
    rawEmbedder = new HashEmbedder();
  } else {
    const cfg = loadEmbedConfig();
    if (!cfg) {
      console.error('[perf-recall] 未配 embedder（MEMOWEFT_EMBED_* / DLA_EMBED_*）。真实档需要 bge-m3 端点；离线自证请加 --selftest。');
      process.exit(2);
    }
    console.log(`[perf-recall] embedder: ${cfg.model} @ ${cfg.baseUrl}`);
    rawEmbedder = new OpenAICompatEmbedder(cfg);
  }
  const embedder = new ChunkingEmbedder(rawEmbedder, 32);

  // 真实档：先探活（一次 1 条嵌入），端点不可达则如实报告、退出（不伪造数字）。
  if (!SELFTEST) {
    try {
      const probe = await embedder.embed(['探活']);
      if (!probe[0]?.length) throw new Error('嵌入返回空向量');
      console.log(`[perf-recall] 端点探活 OK（向量维度 ${probe[0].length}）。`);
    } catch (e) {
      console.error(`[perf-recall] bge-m3 端点不可达：${e?.message || e}`);
      console.error('[perf-recall] 脚本已就位；端点恢复后重跑 `node bench/perf-recall.mjs` 即出真实数字。');
      process.exit(3);
    }
  }

  const dir = mkdtempSync(join(tmpdir(), 'memoweft-perf-recall-'));
  const dbFile = join(dir, 'bench.db');
  try {
    // 1) 种 N 条认知 + 真向量索引（一次性成本）。
    const { indexMs, count } = await seed(dbFile, embedder);
    console.log(`[perf-recall] 种入 ${count} 条认知 + 建向量索引：${fmt(indexMs)}（一次性，与召回延迟无关）。`);

    // 2) VectorRetriever + bge-m3 档：core 自建 VectorRetriever（读同库的 vectors 表）。
    const vec = await measureRecall(() => createMemoWeftCore({ dbPath: dbFile, embedder }));
    console.log('[perf-recall] ── VectorRetriever + ' + (SELFTEST ? 'HashEmbedder' : 'bge-m3') + '（真实语义召回，含 query 嵌入往返）──');
    console.log(`               每轮命中认知数：${vec.hits}（topK=${TOP_K}）`);
    console.log(`               min ${fmt(vec.min)} · P50 ${fmt(vec.p50)} · P95 ${fmt(vec.p95)} · max ${fmt(vec.max)} · mean ${fmt(vec.mean)}`);

    // 3) NullRetriever 存储层基线（同一批认知、同一 core.recall 入口，召回恒空）→ 复现文档 ≈0ms。
    const nul = await measureRecall(() => createMemoWeftCore({ dbPath: dbFile, retriever: new NullRetriever() }));
    console.log('[perf-recall] ── NullRetriever（存储层基线·无 embedder·召回恒空）──');
    console.log(`               每轮命中认知数：${nul.hits}（预期 0）`);
    console.log(`               P50 ${fmt(nul.p50)} · P95 ${fmt(nul.p95)} · mean ${fmt(nul.mean)}`);

    // 4) 汇总一行，方便抄进 docs/internals/perf.md。
    console.log('');
    console.log('[perf-recall] ── 填文档用 ──');
    console.log(`        ${count} 条认知 · VectorRetriever+${SELFTEST ? 'Hash' : 'bge-m3'}：recall P50 ≈ ${vec.p50.toFixed(1)} ms · P95 ≈ ${vec.p95.toFixed(1)} ms（${ROUNDS} 轮）`);
    console.log(`        对照 NullRetriever（存储层·召回空）：P50 ≈ ${nul.p50.toFixed(2)} ms`);
    console.log(`        环境：Node ${process.versions.node} · ${process.platform}/${process.arch}${SELFTEST ? '' : ' · bge-m3 @ 本机 RTX 3090'}`);

    // 5) selftest 断言：召回链真的通 + 统计有值（离线自证脚本骨架）。
    if (SELFTEST) {
      const fails = [];
      if (!(vec.hits > 0)) fails.push(`Vector 档命中数应 >0，实得 ${vec.hits}（种入/索引/召回链断了）`);
      if (!Number.isFinite(vec.p50) || !Number.isFinite(vec.p95)) fails.push('Vector 档 P50/P95 非有限值');
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
