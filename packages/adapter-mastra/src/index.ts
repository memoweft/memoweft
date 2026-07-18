/**
 * @memoweft/adapter-mastra · 公开面。
 *
 * 把 MemoWeft 的长期记忆接进 Mastra Agent 的 Processor 面（@mastra/core/processors）：
 *   - createMemoWeftProcessor(core, opts) → 同一实例注册进 Agent 的 inputProcessors（读·召回注入 system 通道）
 *       与 outputProcessors（写·落库用户原话 spoken + 工具结果 tool + AI 回复 recordAssistantReply[0.6 面]）。
 *
 * peer 兼容 memoweft ^0.5 || ^0.6：recordAssistantReply 是 0.6 的会话上下文面，运行时能力探测——
 *   0.6 宿主自动启用「附和/短回答」上下文线，0.5 宿主降级为基础摄入+召回，均不报错。
 */
export {
  createMemoWeftProcessor,
  lastUserMessage,
  type MemoWeftProcessorOptions,
} from './processor.ts';

export { buildKnowledgeBlock, type RecalledLike } from './knowledgeBlock.ts';

// 降级语义公开类型：供宿主为注入的 logger 标注类型。
export {
  DEFAULT_RECALL_TIMEOUT_MS,
  type MemoWeftLogger,
  type MemoWeftDegradedEvent,
} from './degrade.ts';
