/**
 * 最小可跑示例：把 MemoWeft 长期记忆接进 LangChain（`@langchain/core`）的一轮 RAG 对话。
 *
 * 一个工厂造读写四件套（`{ retriever, writeCallback, formatKnowledge, persistUserTurn }`），覆盖三条路径：
 *   ① 召回注入（读）= `retriever.invoke(query)` → `Document[]` → `formatKnowledge(docs)` 中性块 → 宿主拼进 prompt。
 *      框架行为：LangChain callbacks 是【仅观察】（CallbackManager 丢弃 handler 返回值），故召回【不能】走 callback，
 *      必须走 BaseRetriever（Runnable）——宿主获取 Document[] 后自行拼进 prompt。
 *   ② 用户原话（写）= `persistUserTurn({ text, originId })`——由宿主在调用点（召回注入前）持原话显式传入 → spoken 证据。
 *   ③ 工具结果（写）= `writeCallback` 挂进 `config.callbacks` → 链里工具跑完自动摄入 tool 证据
 *      （只 `handleToolEnd` 读工具【返回结果】，绝不碰调用意图/入参——tool-result-only ingestion boundary）。
 *
 * 从源码检出运行：
 *   git clone https://github.com/memoweft/memoweft.git && cd memoweft
 *   npm ci && npm run build && npm run build --workspace @memoweft/adapter-langchain
 *   # 真实 RAG 链还需一个 chat model（如 @langchain/openai 的 ChatOpenAI）+ 鉴权（OPENAI_API_KEY）
 *   node --experimental-strip-types packages/adapter-langchain/examples/basic.ts
 * 只想看接线、不连模型：本示例的记忆三件（recall / persistUserTurn / writeCallback）都能离线单独调；
 *   真正的 `model.invoke` 段用注释示意（宿主自带 model），示例只渲染出"注入了记忆的 prompt"演示接线。
 */
import { ChatPromptTemplate } from '@langchain/core/prompts';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftLangChain } from '@memoweft/adapter-langchain';

// 1) 起一个 Core（这里用一次性内存库；真实宿主传自己的 dbPath）。
const core = createMemoWeftCore({ dbPath: ':memory:' });

// 2) 造 MemoWeft 读写四件套。lang 只影响注入块的说明文字（沿用 Core 中性措辞），不改 Core 行为。
//    可选：onRecall 获取完整召回面（带 id/contentType/score，explain 时还带 provenance 授权位）供宿主观测/自筛。
const mw = createMemoWeftLangChain(core, {
  lang: 'en',
  onRecall: (items) => console.log(`[memoweft] recalled ${items.length} cognition(s)`),
});

// 3) 一个最小 RAG prompt（人格/语气是宿主的事——Core 无头，适配器不自造人设）。
//    `{memory}` 占位吃召回注入块（system 语境层）；`{question}` 吃用户这轮问题。
const prompt = ChatPromptTemplate.fromMessages([
  ['system', 'You are a helpful assistant.{memory}'],
  ['human', '{question}'],
]);

// 4) 一整轮对话：读写三条路径手动接线（自驱动最直观；备选的 LCEL 图接法见文末）。
async function chatTurn(userText: string, turnId: string) {
  // ② 写：先存这轮【用户原话】(spoken)。turnId 是稳定幂等键，同一轮重放只落一条。
  //    显式传入原始文本，不从链事件重新派生；模型 prompt 已包含召回记忆，重新派生会将注入内容错误地存为用户证据。
  await mw.persistUserTurn({ text: userText, originId: turnId });

  // ① 读：召回相关记忆 → Document[] → 中性注入块（宿主自拼进 prompt）。
  //    降级：召回超时/抛错 → retriever 返回 []（不注入）、对话不中断。
  const memory = mw.formatKnowledge(await mw.retriever.invoke(userText));

  // ③ 写：writeCallback 挂进 config.callbacks → 链里任何工具跑完自动落 tool 证据（只 handleToolEnd）。
  //    真实链把 model 接在 prompt 之后（宿主自带，如 @langchain/openai 的 ChatOpenAI）：
  //
  //      const chain = prompt.pipe(model);
  //      const reply = await chain.invoke(
  //        { memory, question: userText },
  //        { callbacks: [mw.writeCallback] }, // ③ 工具结果自动摄入
  //      );
  //      return reply;
  //
  //    本示例不带 model，仅渲染出"注入了记忆的 prompt"演示接线：
  const messages = await prompt.formatMessages({ memory, question: userText });
  console.log('[prompt]\n' + messages.map((m) => `  ${m.getType()}: ${m.content}`).join('\n'));
}

// 用两轮演示"这一轮存进去、下一轮召回出来"（第二轮的 prompt 会注入第一轮沉淀的记忆）。
await chatTurn('I strongly prefer short, direct answers.', 'turn-1');
await chatTurn('Now explain recursion.', 'turn-2');

core.close();

/*
 * 备选接线：把召回作为 LCEL 链的一环（RunnablePassthrough.assign 求 memory 变量）——
 *   适合已有 LCEL 链、想把召回组进链图时：
 *
 *   import { RunnablePassthrough, RunnableLambda } from '@langchain/core/runnables';
 *   const chain = RunnablePassthrough.assign({
 *     memory: new RunnableLambda({
 *       func: async (x: { question: string }) => mw.formatKnowledge(await mw.retriever.invoke(x.question)),
 *     }),
 *   })
 *     .pipe(prompt)
 *     .pipe(model); // 宿主自带 model
 *   const reply = await chain.invoke({ question: userText }, { callbacks: [mw.writeCallback] });
 *   // ② 用户原话仍需宿主在调用点显式 persistUserTurn（retriever 只读、不写原话）。
 *
 * 为何不实现 LangChain 的 BaseMemory：BaseMemory 是"把历史对话缓冲塞回 prompt"的会话记忆，语义与 MemoWeft
 *   "区分事实/猜测、置信度由规则算、只存用户原话 + 工具结果"的长期记忆正交——强行接入会把助手回话/注入记忆当历史存回
 *   （脏数据，违tool-result-only ingestion boundary +  隐私）。故本适配器走 retriever（读）+ callback/闭包（写）三缝，而非 BaseMemory。
 */
