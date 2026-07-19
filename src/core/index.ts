/**
 * 统一 Core 入口，对外汇出公共能力。
 * Host 优先 import 这里的 createMemoWeftCore，不再散装拼 stores/retriever/llm。
 */
export {
  createMemoWeftCore,
  type MemoWeftCore,
  type CreateCoreOptions,
  type UserMessageInput,
  type ObservationInput,
  type ToolResultInput,
  type RecallInput,
  type ExplainCognitionInput,
  type CognitionExplanation,
  type ConversationInput,
  type RecordAssistantReplyInput,
  type UpdateProfileInput,
  type PortableAPI,
  type MemoryGraphAPI,
  type HealthReport,
  type UsageReport,
} from './createCore.ts';
