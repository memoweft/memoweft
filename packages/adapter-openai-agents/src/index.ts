/**
 * @memoweft/adapter-openai-agents · 公开面。
 *
 * 把 MemoWeft 的长期记忆接进 OpenAI Agents SDK（`@openai/agents`）的 run 流程：
 *   一个工厂 createMemoWeftRunner(core, opts) 造三件套，覆盖读写三条路径——
 *     - `callModelInputFilter`：① 召回注入（读，模型调用前把召回块追加进 instructions）；
 *     - `run` 包装器：② 用户原话摄入（写，spoken；闭包捕获未注入的原始 input）
 *        + ③ 工具结果摄入（写，扫 RunResult.newItems 的 tool_call_output_item）；
 *     - `persistToolOutputs`：③ 的可测/手动入口（自驱动 run 的宿主获取 newItems 后可直接调）。
 *   用法：`const mw = createMemoWeftRunner(core); await mw.run(agent, input)`；
 *     或自驱动：`run(agent, input, { callModelInputFilter: mw.callModelInputFilter })` + 事后 `mw.persistToolOutputs(res.newItems)`。
 */
export {
  createMemoWeftRunner,
  spokenTextFromRunInput,
  finalAssistantText,
  recordFinalReply,
  type MemoWeftRunner,
  type MemoWeftRunnerOptions,
  type MemoWeftRunOptions,
  type MemoWeftRunExtras,
} from './runner.ts';

// 召回注入块拼装 + 召回项形状（对外也当独立工具用；隐私口径见文件注释）。
export { buildKnowledgeBlock, type RecalledLike } from './knowledgeBlock.ts';

// 降级语义公开类型：供宿主为注入的 logger 标注类型。
export {
  DEFAULT_RECALL_TIMEOUT_MS,
  type MemoWeftLogger,
  type MemoWeftDegradedEvent,
} from './degrade.ts';
