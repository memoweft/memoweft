/**
 * MemoWeft 包入口（地图 cell 11/13：MemoWeft 是被宿主 import 的框架/库）。
 * 阶段 0：证据层 + 存储/召回接口 + 对话源 + 回话编排 + 可观测日志。
 */

// 可观测性
export {
  createRunLogger,
  RunLogger,
  type TurnRecord,
  type RecallItem as LogRecallItem,
  type Hypothesis,
  type RunLoggerOptions,
  type ProfileUpdateRecord,
  type ProfileUpdateTimings,
} from './obs/runLog.ts';

// 证据层（真相）
export {
  type Evidence,
  type EvidenceInput,
  type SourceKind,
} from './evidence/model.ts';
export { SqliteEvidenceStore, type EvidenceStore } from './evidence/store.ts';

// 事件层（情境化沉淀：原料 → 事件 → 判断）
export { type Event, type EventInput, type EventWithEvidence } from './event/model.ts';
export { SqliteEventStore, type EventStore } from './event/store.ts';
export { distill, type DistillDeps, type DistillResult } from './distillation/distill.ts';

// 认知层（判断·多维用户模型）
export {
  type Cognition,
  type CognitionInput,
  type CognitionWithSources,
  type ContentType,
  type FormedBy,
  type CredStatus,
  type EvidenceLink,
  type EvidenceRelation,
} from './cognition/model.ts';
export { SqliteCognitionStore, type CognitionStore, type CognitionPatch } from './cognition/store.ts';

// 存储装配：一条共享连接 + 三个 store + 事务器（让写路径多步、多表写能原子化）
export { openStores, type StoreBundle } from './store/openStores.ts';
export { noopTransaction, type Transaction } from './store/transaction.ts';
// Schema 版本化 / 迁移器（0.2.0）：openStores 自动跑；这里导出供诊断、dry-run 预检、迁移工具用。
export {
  runMigrations,
  getSchemaVersion,
  LATEST_SCHEMA_VERSION,
  type Migration,
  type MigrationResult,
  type RunMigrationsOptions,
} from './store/migrations.ts';

// 画像生成（写路径）
export { consolidate, type ConsolidateDeps, type ConsolidateResult } from './consolidation/consolidate.ts';
export { updateProfile, type UpdateProfileDeps, type UpdateProfileResult, type UpdateProfileTimings } from './consolidation/updateProfile.ts';
export { computeConfidence, deriveCredStatus, type ConfidenceInputs } from './consolidation/confidence.ts';

// M4 归因（可解释假设）+ M5 带证据主动询问（阶段 3）
export {
  attribute,
  type AttributeDeps,
  type AttributeResult,
  type AttributedHypothesis,
} from './attribution/attribute.ts';
export {
  proposeAsk,
  type ProposeAskDeps,
  type ProposeAskOptions,
  type ProposeAskResult,
  type AskProposal,
  type AskPolicy,
} from './asking/proposeAsk.ts';
export {
  revisitConflicts,
  type RevisitDeps,
  type RevisitResult,
} from './asking/revisitConflicts.ts';

// 周期后台（阶段 4-B）：分型衰减（读时算有效置信）+ 自然过期 + 跨会话趋势
export { decayFactor, halfLifeOf, effectiveConfidence } from './background/decay.ts';
export { expire, type ExpireDeps, type ExpireResult } from './background/expire.ts';
export { aggregateTrends, type AggregateTrendsDeps, type TrendResult } from './background/trends.ts';

// 召回底座（可替换）+ 嵌入器（云端优先可替换）
export { type Retriever, type RetrievalHit } from './retrieval/retriever.ts';
export { NullRetriever } from './retrieval/nullRetriever.ts';
export { VectorRetriever } from './retrieval/vectorRetriever.ts';
export {
  type Embedder,
  type EmbedConfig,
  OpenAICompatEmbedder,
  loadEmbedConfig,
} from './retrieval/embedder.ts';

// LLM 客户端
export {
  type LLMClient,
  type ChatMessage,
  OpenAICompatClient,
  loadLLMConfig,
} from './llm/client.ts';
export { loadLLMPool, type LLMPool, type LLMPurpose } from './llm/pool.ts';
export {
  extractJsonObject,
  parseJsonObject,
  parseJsonObjectWithRepair,
  type ParseWithRepairDeps,
} from './llm/jsonRepair.ts';

// 统一 Core 入口（架构归位·批次2）：Host 优先经它调 Core，不散装拼底层件
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
} from './core/index.ts';

// 受控记忆管理 API（批次2）：7 操作 + 审计表，管理操作带 reason 留痕
export {
  createMemoryManagementAPI,
  type MemoryManagementAPI,
  type InvalidateCognitionInput,
  type UpdateEvidenceAuthorizationInput,
  type RemoveEvidenceSafelyInput,
  type RemoveEvidenceResult,
  type RemovalBlocker,
  type RemoveCognitionSafelyInput,
  type RemoveCognitionResult,
  type MergeCognitionInput,
  type MergeCognitionResult,
  type ArchiveCognitionInput,
  type IntegrityIssue,
  type IntegrityReport,
  type MemoryManagementDeps,
  type ListMemoryInput,
  type CognitionWithMeta,
  type ResetSubjectInput,
  type ResetSubjectResult,
  SqliteManagementLog,
  type ManagementLog,
  type ManagementLogEntry,
} from './memory/index.ts';

// 共享召回（批次2 抽取）：Conversation 与 core.recall 共用的同一段召回语义
export { recallCognitions, type RecallDeps, type RecalledCognitionItem } from './retrieval/recall.ts';

// 管线 / 会话编排
export { Conversation, type ConversationDeps, type TurnOutcome, type RecalledCognition } from './pipeline/conversation.ts';
export { perceive, type PerceiveOptions } from './pipeline/perceive.ts';
export { WorkingMemory, type Turn } from './pipeline/workingMemory.ts';

// 感知 / 多源摄入（阶段 4-A）：Core 只保留【通用观察摄入口】（generic Observation + ingestObservations）。
export {
  ingestObservations,
  type Observation,
  type IngestDeps,
  type IngestResult,
} from './perception/ingest.ts';
// 真实采集（活动窗口等）已【整体迁出 Core】到采集插件 plugins/collector-active-window/（架构归位，boundaries.md §4.1）：
// 窗口采样、样本→Observation 映射、采集循环都是 Plugin 层知识；采集插件经 Host /api/observe → core.ingestObservation 落库。

// 配置
export { config, cloudReadDefault, type MemoWeftConfig, type DlaConfig } from './config.ts';

// 便携记忆包（导入/导出/备份/恢复 · Phase 5-A）：让用户记忆成为可迁移资产
export {
  exportBundle,
  type ExportDeps,
  type ExportOptions,
  validateBundle,
  importBundle,
  type ImportDeps,
  type ImportOptions,
  BUNDLE_FORMAT,
  BUNDLE_SCHEMA_VERSION,
  type MemoryBundle,
  type EventEvidenceLink,
  type CognitionEvidenceLink,
  type ImportMode,
  type ImportPlan,
  type ValidateResult,
} from './portable/index.ts';

// 图谱化记忆视图（Phase 6-B）：三层数据 → 力导向图 payload
export {
  buildMemoryGraph,
  type BuildGraphDeps,
  type BuildGraphOptions,
  type MemoryGraphNode,
  type MemoryGraphEdge,
  type MemoryGraphNodeKind,
  type MemoryGraphEdgeKind,
  type MemoryGraphStats,
  type MemoryGraphPayload,
} from './graph/index.ts';

import { MEMOWEFT_VERSION } from './version.ts';
export { MEMOWEFT_VERSION };
/** @deprecated 用 MEMOWEFT_VERSION；保留旧名兼容已引用 DLA_VERSION 的宿主。 */
export const DLA_VERSION = MEMOWEFT_VERSION;
