/**
 * 最小可跑示例：把 MemoWeft 长期记忆接进 OpenAI Agents SDK（`@openai/agents`）的一轮对话。
 *
 * 一个工厂造三件套（`{ run, callModelInputFilter, persistToolOutputs }`），覆盖读写三条路径：
 *   ① 召回注入（读）= `callModelInputFilter`（模型调用前把召回块追加进 instructions，末条 user 时注一次）；
 *   ② 用户原话（写）= `run` 包装器闭包捕获【未注入的原始 input】→ 存 spoken；
 *   ③ 工具结果（写）= run 结束后扫 `RunResult.newItems`，只摄 `tool_call_output_item`（绝不碰调用意图/入参——铁律 3a）。
 *
 * 跑法（示意——`run` 会拉起真实 OpenAI Agents SDK 运行时，需你已装好 SDK 并配好鉴权，如 OPENAI_API_KEY）：
 *   npm i @openai/agents memoweft @memoweft/adapter-openai-agents
 *   node --experimental-strip-types examples/basic.ts   # Node 22（Node 23+ 原生剥类型，直接 node examples/basic.ts）
 * 只想看接线、不连模型：把下面 chatTurn 里的 `await mw.run(...)` 段注释掉即可——
 *   createMemoWeftRunner 的返回值是纯离线数据，callModelInputFilter / persistToolOutputs 都能直接单独调。
 */
import { Agent } from '@openai/agents';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftRunner } from '@memoweft/adapter-openai-agents';

// 1) 起一个 Core（这里用一次性内存库；真实宿主传自己的 dbPath）。
const core = createMemoWeftCore({ dbPath: ':memory:' });

// 2) 造 MemoWeft 读写三件套。lang 只影响注入块的说明文字（照 Core 中性措辞），不改 Core 行为。
//    可选：onRecall 拿到完整召回面（带 id/contentType/score，explain 时还带 provenance 授权位）供宿主观测/自筛。
const mw = createMemoWeftRunner(core, {
  lang: 'en',
  onRecall: (items) => console.log(`[memoweft] recalled ${items.length} cognition(s)`),
});

// 3) 一个 Agent（人格/语气是宿主的事——Core 无头，适配器不自造人设）。
const agent = new Agent({ name: 'Assistant', instructions: 'You are a helpful assistant.' });

// 4) 一整轮对话：用 `mw.run` 直接替换 SDK 的 `run`。
//    它会：先存这轮【用户原话】(spoken)、把召回 chain 进 callModelInputFilter 注入本轮、run 结束后存工具结果。
//    memoweft.spokenOriginId 是这轮用户原话的稳定幂等键（宿主的 turnId/messageId），同一轮重放只落一条。
async function chatTurn(prompt: string, turnId: string) {
  const result = await mw.run(agent, prompt, { memoweft: { spokenOriginId: turnId } });
  return result.finalOutput;
}

// 用两轮演示"这一轮存进去、下一轮召回出来"（第二轮的模型调用会被注入第一轮沉淀的记忆）。
console.log(await chatTurn('I strongly prefer short, direct answers.', 'turn-1'));
console.log(await chatTurn('Now explain recursion.', 'turn-2'));

core.close();

/*
 * 备选接线：自己驱动 SDK 的 `run`（不用 mw.run 包装器）——把召回注入 filter 组进 RunConfig，事后手动写工具结果。
 * 适合你已有自己的 callModelInputFilter / RunConfig、或想完全掌控 run 调用时：
 *
 *   import { run } from '@openai/agents';
 *   const result = await run(agent, 'Now explain recursion.', {
 *     callModelInputFilter: mw.callModelInputFilter, // ① 召回注入（已 chain 宿主 opts.callModelInputFilter）
 *   });
 *   await mw.persistToolOutputs(result.newItems);     // ③ 扫 newItems 写工具结果（只摄 tool_call_output_item）
 *   // ② 用户原话此路径需宿主自行沉淀（mw.run 包装器才自动捕获 run 的 input 原文）。
 *
 * 降级（§16.2）：召回超时/抛错 → 本轮不注入、对话不中断；写失败重试一次仍失败静默吞——绝不向 SDK 抛。
 */
