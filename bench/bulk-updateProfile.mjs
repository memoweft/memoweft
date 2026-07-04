/**
 * Perf 基准脚本（Q4 · 灌 1 万条出真实数字）。手动跑、不进 CI、不设门。
 *
 * ⚠️ 先 build 再跑：本脚本【从 dist/index.js import】（即 package.json 的 "main"），不直接 import src 的 .ts。
 *   理由：项目 src 是 TypeScript，发布物经 dist（纯 JS）；.mjs 直 import .ts 在未 build 或非原生剥类型的 Node 上
 *   跑不起来，验收会飘。所以跑之前必须：
 *
 *       npm run build && npm run bench
 *
 * 量什么：
 *   - updateProfile 耗时：读 core.updateProfile 返回的现成 `timings.totalMs`（distill→consolidate→attribute→重建索引），
 *     不另造计时。这是「灌完 1 万条后跑一次写路径整理」要花多久。
 *   - recall 延迟：走公共入口 core.recall(query) 计时（默认 NullRetriever，无向量库 → 量的是召回入口链本身开销，
 *     不含云端嵌入往返）。
 *
 * 离线纯净：注入一个【离线 stub 大模型】（不打网络、返回合法形状），所以脚本无需 .env、无网络、可复现；
 *   量到的是【真实的库读写 + 编排开销】，把模型往返这个抖动源摘掉——诚实标明这是本机数字、非保证值。
 *
 * 数据卫生（守记忆「冒烟数据必清」）：用 :memory: 一次性内存库，进程退出即销毁，不落任何库文件到仓库。
 */
import { createMemoWeftCore } from '../dist/index.js';

// ── 可调参数（默认 1 万条；跑更小规模可 BENCH_N=1000 npm run bench） ──
const N = Number(process.env.BENCH_N) || 10_000;
const RECALL_ROUNDS = Number(process.env.BENCH_RECALL_ROUNDS) || 20;

/**
 * 离线 stub 大模型：实现 LLMClient 接口（chat + callCount），不打网络。
 * - distill 调它 → 期望一段总结文本：回一句固定摘要即可。
 * - consolidate 调它 → 期望一个 JSON 对象（new/reinforce/correct/conflict）：回空四类的合法 JSON，
 *   保证写路径整段编排真实跑通（解析→事务→标已消化），又不依赖真模型的判断。
 * 注：脚本量的是【库读写 + 编排】的耗时，不是模型判断质量——故 stub 返回极简合法值即可。
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
      // consolidate 的 system 提示里要求输出 JSON 对象；distill 的要求输出总结文本。
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
  console.log(`[bench] Node ${process.versions.node} · platform ${process.platform} ${process.arch}`);
  console.log(`[bench] 灌 ${N.toLocaleString('en-US')} 条 evidence，recall 采样 ${RECALL_ROUNDS} 次（取平均）。`);

  // :memory: 一次性内存库 + 离线 stub 模型 + 默认 NullRetriever（无向量库、无网络）。
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: createStubLLM() });

  try {
    // 1) 灌数据：走公共入口 ingestUserMessage（perceive → put）。用递增 occurredAt 让写路径排序稳定。
    const base = Date.parse('2020-01-01T00:00:00.000Z');
    const seedStart = performance.now();
    for (let i = 0; i < N; i++) {
      await core.ingestUserMessage({
        content: `基准语料第 ${i} 条：我今天做了一些事，记录一下 #${i}`,
        // 每条错开 1 秒，occurredAt 单调递增（distill 按时间排序）。
        occurredAt: new Date(base + i * 1000).toISOString(),
      });
    }
    const seedMs = performance.now() - seedStart;
    console.log(`[bench] 灌库完成：${fmt(seedMs)}（约 ${(N / (seedMs / 1000)).toFixed(0)} 条/秒）。`);

    // 2) updateProfile：读返回的现成 timings，别另造计时。
    const up = await core.updateProfile();
    const t = up.timings;
    console.log('[bench] updateProfile 各步耗时（读 result.timings，ms）：');
    console.log(`          distill      ${fmt(t.distillMs)}`);
    console.log(`          consolidate  ${fmt(t.consolidateMs)}`);
    console.log(`          attribute    ${fmt(t.attributeMs)}`);
    console.log(`          index        ${fmt(t.indexMs)}`);
    console.log(`          ──────────`);
    console.log(`          total        ${fmt(t.totalMs)}   ← 主数字（填进文档）`);

    // 3) recall：走公共入口 core.recall，采样多次取平均（默认 NullRetriever，量入口链开销）。
    let recallSum = 0;
    for (let r = 0; r < RECALL_ROUNDS; r++) {
      const r0 = performance.now();
      await core.recall({ query: `第 ${r} 轮召回查询` });
      recallSum += performance.now() - r0;
    }
    const recallAvg = recallSum / RECALL_ROUNDS;
    console.log(`[bench] recall 平均延迟（${RECALL_ROUNDS} 次）：${fmt(recallAvg)}   ← 填进文档`);

    // 4) 汇总一行，方便复制进文档。
    console.log('');
    console.log('[bench] ── 填文档用（把下面数字抄进 README / docs/perf.md 的占位符）──');
    console.log(`        ${N.toLocaleString('en-US')} 条 evidence：updateProfile ≈ ${t.totalMs.toFixed(0)} ms，recall ≈ ${recallAvg.toFixed(1)} ms`);
    console.log(`        测试环境：Node ${process.versions.node} · ${process.platform}/${process.arch} · <填机器规格：CPU / 内存>`);
  } finally {
    // 数据卫生：关连接（:memory: 库随进程退出销毁，不落文件）。
    core.close();
  }
}

main().catch((err) => {
  console.error('[bench] 失败：', err);
  process.exitCode = 1;
});
