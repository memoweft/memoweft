/**
 * 统一 Core 入口（架构归位·批次2）对外汇出。
 * Host 优先 import 这里的 createMemoWeftCore，不再散装拼 stores/retriever/llm。
 */
export {
  createMemoWeftCore,
  type MemoWeftCore,
  type CreateCoreOptions,
  type UserMessageInput,
  type ObservationInput,
  type RecallInput,
  type ConversationInput,
  type UpdateProfileInput,
  type PortableAPI,
  type MemoryGraphAPI,
  type HealthReport,
} from './createCore.ts';
