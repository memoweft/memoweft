/**
 * 最小可跑示例：把 MemoWeft 长期记忆接进 LlamaIndex（`@llamaindex/core` 记忆块 + `@llamaindex/workflow` agent 流）。
 *
 * 一个工厂造读写四件套（`{ memoryBlock, persistFromAgentStream, persistUserTurn, formatKnowledge }`），覆盖三条路径：
 *   ① 召回注入（读）= `memoryBlock`（`MemoWeftMemoryBlock extends BaseMemoryBlock`）加入 `createMemory({ memoryBlocks:[block] })`
 *      → `agent({ llm, tools, memory })`。每次模型调用前 Memory 调 `block.get(messages)`，本块做语义召回、把中性知识块
 *      作为一条 `role:'memory'` 消息注入。宿主什么都不用做——注入是 Memory 机制自动完成的。
 *   ② 用户原话（写）= `persistFromAgentStream(stream, { userMessage })` 内摄（注入前持有的原话，显式传入）；
 *      或宿主闭包 `persistUserTurn({ text, originId })` 单独落原话。
 *   ③ 工具结果（写）= `persistFromAgentStream(agent.runStream(userText), extras)` 透传式 async generator：
 *      原样 re-yield `runStream` 全部事件、顺路【只认】`agentToolCallResultEvent` → 摄工具返回结果
 *      （`agentToolCallEvent` 调用意图只 re-yield 不摄——tool-result-only ingestion boundary·by-construction）。
 *
 * 从源码检出运行：
 *   git clone https://github.com/memoweft/memoweft.git && cd memoweft
 *   npm ci && npm run build && npm run build --workspace @memoweft/adapter-llamaindex
 *   # 真实 agent 还需一个 ToolCallLLM（如 @llamaindex/openai 的 OpenAI）+ 鉴权（OPENAI_API_KEY）+ 至少一个 tool
 *   node --experimental-strip-types packages/adapter-llamaindex/examples/basic.ts
 * 只想看接线、不连模型：本示例的记忆三件（memoryBlock.get / persistFromAgentStream / persistUserTurn）都能离线单独调；
 *   真正的 `agent(...).runStream(...)` 段用注释示意（宿主自带 llm + tools），示例只渲染出"会被注入的记忆"并传入一条
 *   构造事件流演示写路径接线。
 */
import { createMemory } from '@llamaindex/core/memory';
import type { MemoryMessage } from '@llamaindex/core/memory';
import { agentToolCallResultEvent } from '@llamaindex/workflow';
import type { WorkflowEventData } from '@llamaindex/workflow';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftLlamaIndex } from '@memoweft/adapter-llamaindex';

// 1) 起一个 Core（这里用一次性内存库；真实宿主传自己的 dbPath）。
const core = createMemoWeftCore({ dbPath: ':memory:' });

// 2) 造 MemoWeft 读写四件套。lang 只影响注入块的说明文字（沿用 Core 中性措辞），不改 Core 行为。
//    可选：onRecall 获取完整召回面（带 id/contentType/score，explain 时还带 provenance 授权位）供宿主观测/自筛。
const mw = createMemoWeftLlamaIndex(core, {
  lang: 'en',
  onRecall: (items) => console.log(`[memoweft] recalled ${items.length} cognition(s)`),
});

// 3) 召回注入（读）接线：把 memoryBlock 加入 createMemory → agent。
//    每次模型调用前 Memory 自动调 block.get(messages)、把召回记忆作为一条 role:'memory' 消息注入 prompt——宿主零介入。
const _memory = createMemory({ memoryBlocks: [mw.memoryBlock] });
//    真实 agent（宿主自带 llm + tools；人格/语气是宿主的事——Core 无头，适配器不自造人设）：
//
//      import { agent } from '@llamaindex/workflow';
//      import { OpenAI } from '@llamaindex/openai';
//      const myAgent = agent({ llm: new OpenAI({ model: 'gpt-4o' }), tools: [/* …你的工具… */], memory: _memory });
//

// 4) 一整轮对话：读写三条路径接线。
async function chatTurn(userText: string, turnId: string) {
  // ① 读：离线示意"这一轮 memoryBlock 会注入什么"。真实链里这一步由 Memory 在模型调用前自动做，宿主无须手调 get()。
  //    降级：召回超时/抛错 → block.get() 返回 []（不注入）、对话不中断。
  const userMsg = { id: turnId, role: 'user', content: userText } as MemoryMessage;
  const injected = await mw.memoryBlock.get([userMsg]);
  console.log(
    '[injected memory]\n' +
      (injected.length ? '  ' + String(injected[0]!.content).replace(/\n/g, '\n  ') : '  (none)'),
  );

  // ②③ 写：透传式包住 agent.runStream(userText)，原样消费事件、顺路摄原话（②）+ 工具结果（③）。
  //    真实链：
  //
  //      for await (const ev of mw.persistFromAgentStream(myAgent.runStream(userText), { userMessage: userText, originId: turnId })) {
  //        // …正常消费 ev（事件被原样透传，摄入成败完全不影响这里）…
  //      }
  //
  //    本示例不带 agent，传入一条【构造的工具返回结果事件】演示写路径接线（真实里这些事件来自 runStream）：
  const fakeStream = demoRunStream(userText);
  for await (const ev of mw.persistFromAgentStream(fakeStream, {
    userMessage: userText,
    originId: turnId,
  })) {
    // 透传：消费者原样获取每个事件（这里只数一下）。
    void ev;
  }
}

/** 离线演示用：模拟 agent.runStream 吐出的事件流——一条工具返回结果事件（真实里由 agent 产出）。 */
async function* demoRunStream(userText: string): AsyncGenerator<WorkflowEventData<unknown>> {
  yield agentToolCallResultEvent.with({
    toolName: 'note_lookup',
    toolKwargs: { q: userText },
    toolId: `tool-${Date.now()}`,
    toolOutput: { id: 'r1', result: `(demo) looked up context for: ${userText}`, isError: false },
    returnDirect: false,
    raw: {},
  }) as WorkflowEventData<unknown>;
}

// 用两轮演示"这一轮存进去、下一轮召回出来"（第二轮 memoryBlock 会注入第一轮沉淀的记忆）。
await chatTurn('I strongly prefer short, direct answers.', 'turn-1');
await chatTurn('Now explain recursion.', 'turn-2');

core.close();

/*
 * 为何读走 memory-block、写走 stream-tap（而非接入其他不合适的扩展点）：
 *   - 读：LlamaIndex 的 Memory 机制会在每次模型调用前调各 memory-block 的 get() 取「记忆上下文」拼进 prompt——
 *     这正是召回注入的缝。本块只在 get() 里做召回、把中性知识块作为 role:'memory' 消息返回；隐私：
 *     provenance（证据原文 + 授权位）绝不进 block 输出，只经 onRecall 交宿主。
 *   - 写：BaseMemoryBlock 的 put() 会被 Memory 接收【整段会话消息】(含助手回话 / 已注入的记忆)——在此落库会把助手输出、
 *     甚至注入的记忆当"证据"存回去（脏数据）。故本块 put() 是空实现；写全走 persistFromAgentStream：用户原话由宿主
 *     在注入前显式传入、工具结果只认 agentToolCallResultEvent（tool-result-only ingestion boundary）。
 */
