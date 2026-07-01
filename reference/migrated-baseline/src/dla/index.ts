/**
 * DLA 包入口（D-023：以 npm 包形式被宿主 import）。
 * 对外导出 DLA 公共接口：宿主（如星瑶）import 本文件即用。
 * 阶段：随各阶段把对外接口在此导出。
 */

// Event 真相底座（TASK-01）
export { EventStore } from './event/store.ts';
export type {
  Event,
  EventInput,
  EventForm,
  Sentiment,
  SourceType,
  TemporalOrientation,
} from './event/model.ts';

// 大模型封装（TASK-02，D-017 换模型只动这里）
export {
  OpenAICompatClient,
  loadLLMConfig,
} from './llm/client.ts';
export type { LLMClient, ChatMessage, LLMConfig } from './llm/client.ts';

// 主链路最短闭环（TASK-02）
export { runPipeline } from './pipeline/runner.ts';
export type { PipelineDeps, PipelineResult } from './pipeline/runner.ts';

// 短期对话窗口层（TASK-03，D-024）—— 新的对话主入口
export { createConversation } from './pipeline/runner.ts';
export type { Conversation, HandleResult, Sedimented } from './pipeline/runner.ts';
export { WorkingMemory } from './pipeline/workingMemory.ts';
export type { Turn } from './pipeline/workingMemory.ts';
export { config, estimateTokens } from './config.ts';

// 权重计算（TASK-05，D-007）—— 召回排序用
export { computeWeight } from './event/weight.ts';
