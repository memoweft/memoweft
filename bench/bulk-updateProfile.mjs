/**
 * Offline updateProfile and recall performance benchmark. This is a manual
 * diagnostic, not a CI quality gate.
 *
 * The runner imports the built package entry point. Build before running:
 *
 *       npm run build && npm run bench
 *
 * Measurements:
 *   - updateProfile timing from the public `result.timings` fields after N ingested evidence items;
 *   - average `core.recall` latency with an explicitly injected NullRetriever, excluding embedding latency.
 *
 * A deterministic stub supplies valid model responses. The run needs no model
 * credentials or network access and uses an in-memory database.
 */
import { createMemoWeftCore, NullRetriever } from '../dist/index.js';

// Configuration
const N = Number(process.env.BENCH_N) || 10_000;
const RECALL_ROUNDS = Number(process.env.BENCH_RECALL_ROUNDS) || 20;

/**
 * Deterministic LLMClient stub. Distillation receives fixed text and
 * consolidation receives an empty but valid result object.
 */
function createStubLLM() {
  let calls = 0;
  return {
    get callCount() {
      return calls;
    },
    async chat(messages) {
      calls++;
      const system = messages[0]?.content ?? '';
      // Consolidation expects JSON; distillation expects text.
      if (system.includes('JSON')) {
        return '{"new":[],"reinforce":[],"correct":[],"conflict":[]}';
      }
      return '用户依次表达了若干日常信息（基准脚本 stub 摘要）。';
    },
  };
}

function fmt(ms) {
  return `${ms.toFixed(1)} ms`;
}

async function main() {
  console.log(
    `[bench] Node ${process.versions.node} · platform ${process.platform} ${process.arch}`,
  );
  console.log(
    `[bench] 灌 ${N.toLocaleString('en-US')} 条 evidence，recall 采样 ${RECALL_ROUNDS} 次（取平均）。`,
  );

  // In-memory database, deterministic model stub, and explicit no-op retrieval.
  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: createStubLLM(),
    retriever: new NullRetriever(),
  });

  try {
    // Ingest through the public API with stable chronological ordering.
    const base = Date.parse('2020-01-01T00:00:00.000Z');
    const seedStart = performance.now();
    for (let i = 0; i < N; i++) {
      await core.ingestUserMessage({
        content: `基准语料第 ${i} 条：我今天做了一些事，记录一下 #${i}`,
        occurredAt: new Date(base + i * 1000).toISOString(),
      });
    }
    const seedMs = performance.now() - seedStart;
    console.log(
      `[bench] 灌库完成：${fmt(seedMs)}（约 ${(N / (seedMs / 1000)).toFixed(0)} 条/秒）。`,
    );

    // Use the timings returned by updateProfile.
    const up = await core.updateProfile();
    const t = up.timings;
    console.log('[bench] updateProfile 各步耗时（读 result.timings，ms）：');
    console.log(`          distill      ${fmt(t.distillMs)}`);
    console.log(`          consolidate  ${fmt(t.consolidateMs)}`);
    console.log(`          attribute    ${fmt(t.attributeMs)}`);
    console.log(`          index        ${fmt(t.indexMs)}`);
    console.log(`          ──────────`);
    console.log(`          total        ${fmt(t.totalMs)}`);

    // Sample the public recall path with the explicitly injected NullRetriever.
    let recallSum = 0;
    for (let r = 0; r < RECALL_ROUNDS; r++) {
      const r0 = performance.now();
      await core.recall({ query: `第 ${r} 轮召回查询` });
      recallSum += performance.now() - r0;
    }
    const recallAvg = recallSum / RECALL_ROUNDS;
    console.log(`[bench] recall 平均延迟（${RECALL_ROUNDS} 次）：${fmt(recallAvg)}`);

    // Compact summary.
    console.log('');
    console.log('[bench] ── Summary ──');
    console.log(
      `        ${N.toLocaleString('en-US')} 条 evidence：updateProfile ≈ ${t.totalMs.toFixed(0)} ms，recall ≈ ${recallAvg.toFixed(1)} ms`,
    );
    console.log(
      `        测试环境：Node ${process.versions.node} · ${process.platform}/${process.arch}`,
    );
  } finally {
    core.close();
  }
}

main().catch((err) => {
  console.error('[bench] 失败：', err);
  process.exitCode = 1;
});
