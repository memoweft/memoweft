/**
 * MemoWeft 最小可跑示例（写路径 → 读路径的一整个闭环）。
 *
 * 这段演示 5 件事：
 *   1) 建三层 store（证据 / 事件 / 认知）——MemoWeft 自有的三层数据。
 *   2) 装配 LLM 池 + 嵌入器（都从 .env 读，缺配自动降级，不崩）。
 *   3) 写一条“亲口证据”，跑 updateProfile 把它沉淀成画像（distill → consolidate → 归因 → 建索引）。
 *   4) 用 Conversation 处理下一条消息：召回相关画像 → 注入回话。
 *   5) 收尾关库。
 *
 * 运行前提：
 *   - Node ≥ 22.6（原生跑 .ts、内置 node:sqlite；作者实测 v24）。
 *   - 项目根有 .env，至少配好对话模型（MEMOWEFT_LLM_* 或兼容旧名 DLA_LLM_*）。
 *     只配对话模型也能跑：没配 MEMOWEFT_EMBED_* 时召回自动降级为空（画像照写、只是回话不注入）。
 *
 * 运行（在仓库根目录）：
 *   node examples/minimal.ts
 *
 * 注意：本例用独立的 ./example.db，不碰你正式的 ./dla.db 数据文件。
 */
import {
  SqliteEvidenceStore,
  SqliteEventStore,
  SqliteCognitionStore,
  VectorRetriever,
  NullRetriever,
  OpenAICompatEmbedder,
  loadEmbedConfig,
  loadLLMPool,
  updateProfile,
  Conversation,
  MEMOWEFT_VERSION,
  type Retriever,
} from '../src/index.ts';

const DB = './example.db'; // 独立示例库，别用正式 ./dla.db
const SUBJECT = 'demo-user'; // 这条画像属于谁

async function main() {
  console.log(`MemoWeft ${MEMOWEFT_VERSION} · 最小示例\n`);

  // 1) 三层 store（都可指向同一个 SQLite 文件；测试可传 ':memory:'）。
  const evidenceStore = new SqliteEvidenceStore(DB);
  const eventStore = new SqliteEventStore(DB);
  const cognitionStore = new SqliteCognitionStore(DB);

  // 2) 模型池 + 嵌入器（都从 .env 读）。
  //    - 写路径(distill/consolidate/归因)用 'write' 那档（没单独配就回退对话大模型）。
  //    - 对话用 'chat' 那档。
  const llmPool = loadLLMPool();
  const writeLLM = llmPool.for('write');
  const chatLLM = llmPool.for('chat');

  //    嵌入器缺配 → 用 NullRetriever 降级（画像照写，只是回话不召回注入）。
  const embedConfig = loadEmbedConfig();
  const retriever: Retriever = embedConfig
    ? new VectorRetriever(DB, new OpenAICompatEmbedder(embedConfig))
    : new NullRetriever();
  if (!embedConfig) {
    console.log('⚠️  未配 MEMOWEFT_EMBED_*（或兼容 DLA_EMBED_*）：召回降级为空，仅演示写路径。\n');
  }

  // 3) 写一条“用户亲口”证据。put 会按规则补默认（时间、授权位）。
  evidenceStore.put({
    subjectId: SUBJECT,
    sourceKind: 'spoken', // 亲口 > 观察 > 推测（来源强度分层）
    hostId: 'example',
    rawContent: '我晚上写代码效率最高，白天开会太多根本静不下来。',
  });
  console.log('已写入 1 条证据，开始 updateProfile（写路径一键：整理事件 → 画像 → 归因 → 建索引）…');

  //    updateProfile = 一键写路径。真调模型，耗时取决于你配的模型（返回 timings 可看慢在哪步）。
  const result = await updateProfile(SUBJECT, {
    evidenceStore,
    eventStore,
    cognitionStore,
    retriever,
    llm: writeLLM,
  });
  console.log(
    `画像更新完成：新增认知 ${result.consolidated.created.length} 条` +
      `（强化 ${result.consolidated.reinforced} / 纠正 ${result.consolidated.corrected} / 冲突 ${result.consolidated.conflicted}），` +
      `索引 ${result.indexed} 条，耗时 ${result.timings.totalMs}ms` +
      (result.indexError ? `（索引降级：${result.indexError}）` : ''),
  );

  //    看看生成了什么画像。
  const profile = cognitionStore.all(SUBJECT);
  console.log(`\n当前画像（${profile.length} 条）：`);
  for (const c of profile) {
    console.log(`  · [${c.contentType}/${c.credStatus}] ${c.content}  (置信 ${c.confidence})`);
  }

  // 4) 读路径：处理下一条消息，看它会不会召回上面那条画像并注入回话。
  const convo = new Conversation({ store: evidenceStore, retriever, cognitionStore, llm: chatLLM });
  const turn = await convo.handle('帮我安排一下明天的日程。', { subjectId: SUBJECT });
  console.log(`\n用户：帮我安排一下明天的日程。`);
  console.log(`助手：${turn.reply}`);
  if (turn.recall.length) {
    console.log(`（召回并注入的画像：${turn.recall.map((r) => r.content).join(' / ')}）`);
  }
  if (turn.error) console.log(`（回话出错：${turn.error} —— 但你的话已存为证据）`);

  // 5) 收尾。
  evidenceStore.close();
  eventStore.close();
  cognitionStore.close();
  console.log('\n完成。示例数据在 ./example.db，可删。');
}

main().catch((e) => {
  console.error('示例出错：', e instanceof Error ? e.message : e);
  process.exit(1);
});
