/**
 * MemoWeft 最小可跑示例（写路径 → 读路径的一整个闭环，走【统一入口】createMemoWeftCore）。
 *
 * createMemoWeftCore 一行装配好三层 store + 召回器 + 模型池（都从 .env 读，缺配自动降级、不崩），
 * 是集成 MemoWeft 的【推荐路径】——宿主不必散装拼底层 store（见 src/index.ts 注释、docs/integration.md）。
 *
 * 这段演示 4 件事：
 *   1) 一行建 core。
 *   2) 写一条“用户亲口”证据（ingestUserMessage）。
 *   3) updateProfile 把它沉淀成画像（distill → consolidate → 归因 → 建索引）。
 *   4) handleConversationTurn 处理下一条消息：召回相关画像 → 注入回话。
 *
 * 运行前提：
 *   - Node ≥ 24（本例直接 `node examples/minimal.ts` 跑 .ts，需 Node 原生剥类型 + 内置 node:sqlite；
 *     Node 22 需 22.18+，Node 20 跑不了 .ts）。当库用（import 编译后的包）时 Node 20/22 也行——
 *     那时装可选驱动 better-sqlite3 即可，见 docs/INSTALL.md。
 *   - 项目根有 .env，至少配好对话模型（MEMOWEFT_LLM_* 或兼容旧名 DLA_LLM_*）。
 *     只配对话模型也能跑：没配 MEMOWEFT_EMBED_* 时召回自动降级为空（画像照写、只是回话不注入）。
 *
 * 运行（在仓库根目录）：
 *   node examples/minimal.ts
 *
 * 注意：本例用独立的 ./example.db，不碰你正式的库文件。
 */
import { createMemoWeftCore, MEMOWEFT_VERSION } from '../src/index.ts';

const DB = './example.db'; // 独立示例库
const SUBJECT = 'demo-user'; // 这条画像属于谁

async function main() {
  console.log(`MemoWeft ${MEMOWEFT_VERSION} · 最小示例（createMemoWeftCore）\n`);

  // 1) 一行装配：三层 store + 召回器 + 模型池全从 .env 读，缺配降级不崩。
  const core = createMemoWeftCore({ dbPath: DB });

  const { llmReady, embedReady } = core.health();
  if (!llmReady) console.log('⚠️  未配对话模型（MEMOWEFT_LLM_* / 兼容 DLA_LLM_*）：真调用才会报错，写库部分照跑。');
  if (!embedReady) console.log('⚠️  未配嵌入器（MEMOWEFT_EMBED_*）：召回降级为空，仅演示写路径。');
  console.log();

  // 2) 写一条“用户亲口”证据（授权位、时间由 Core 按规则补默认）。
  await core.ingestUserMessage({
    subjectId: SUBJECT,
    hostId: 'example',
    content: '我晚上写代码效率最高，白天开会太多根本静不下来。',
  });
  console.log('已写入 1 条证据，开始 updateProfile（整理事件 → 画像 → 归因 → 建索引）…');

  // 3) updateProfile = 一键写路径。真调模型，耗时取决于你配的模型（返回 timings 可看慢在哪步）。
  const result = await core.updateProfile({ subjectId: SUBJECT });
  console.log(
    `画像更新完成：新增认知 ${result.consolidated.created.length} 条` +
      `（强化 ${result.consolidated.reinforced} / 纠正 ${result.consolidated.corrected} / 冲突 ${result.consolidated.conflicted}），` +
      `索引 ${result.indexed} 条，耗时 ${result.timings.totalMs}ms` +
      (result.indexError ? `（索引降级：${result.indexError}）` : ''),
  );

  // 看看生成了什么画像（走受控只读接口 memory.listCognitions，不散装碰底层 store）。
  const profile = core.memory.listCognitions({ subjectId: SUBJECT });
  console.log(`\n当前画像（${profile.length} 条）：`);
  for (const c of profile) {
    console.log(`  · [${c.contentType}/${c.credStatus}] ${c.content}  (置信 ${c.confidence})`);
  }

  // 4) 读路径：处理下一条消息，看它会不会召回上面那条画像并注入回话。
  const turn = await core.handleConversationTurn({ subjectId: SUBJECT, message: '帮我安排一下明天的日程。' });
  console.log(`\n用户：帮我安排一下明天的日程。`);
  console.log(`助手：${turn.reply}`);
  if (turn.recall.length) {
    console.log(`（召回并注入的画像：${turn.recall.map((r) => r.content).join(' / ')}）`);
  }
  if (turn.error) console.log(`（回话出错：${turn.error} —— 但你的话已存为证据）`);

  // 5) 收尾（关库 + 关召回器）。
  core.close();
  console.log('\n完成。示例数据在 ./example.db，可删。');
}

main().catch((e) => {
  console.error('示例出错：', e instanceof Error ? e.message : e);
  process.exit(1);
});
