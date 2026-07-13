/**
 * MemoWeft 包入口（地图 cell 11/13：MemoWeft 是被宿主 import 的框架/库）。
 * 阶段 0：证据层 + 存储/召回接口 + 对话源 + 回话编排 + 可观测日志。
 */

// 可观测性
// [experimental] 运行日志器：导出供诊断/回放，接口签名 pre-1.0 可能演进。
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
// [stable] 证据领域形状：门面 recall/list*/storedEvidence 回吐给宿主，形状定型。
export {
  type Evidence,
  type EvidenceInput,
  type SourceKind,
} from './evidence/model.ts';
// [internal] 证据 store 实现件：门面已收口，宿主没理由直接拼。
export { SqliteEvidenceStore, type EvidenceStore } from './evidence/store.ts';

// 事件层（情境化沉淀：原料 → 事件 → 判断）
// [stable] 事件领域形状：Event / EventWithEvidence 由 listEvents 回吐宿主。
export { type Event, type EventWithEvidence } from './event/model.ts';
// [experimental] EventInput：宿主不直接构造（由 distill 内部产），不列入宿主主面。
export { type EventInput } from './event/model.ts';
// [internal] 事件 store 实现 + 蒸馏算子（写路径）：门面已收口。
export { SqliteEventStore, type EventStore } from './event/store.ts';
export { distill, type DistillDeps, type DistillResult } from './distillation/distill.ts';

// 认知层（判断·多维用户模型）
// [stable] 认知领域形状：门面 recall/listCognitions 回吐给宿主，形状定型。
export {
  type Cognition,
  type CognitionWithSources,
  type ContentType,
  type FormedBy,
  type CredStatus,
  type EvidenceLink,
  type EvidenceRelation,
} from './cognition/model.ts';
// [experimental] CognitionInput：宿主不直接构造（由 consolidate 内部产）。
export { type CognitionInput } from './cognition/model.ts';
// [internal] 认知 store 实现件：门面已收口。
export { SqliteCognitionStore, type CognitionStore, type CognitionPatch } from './cognition/store.ts';

// 存储装配：一条共享连接 + 三个 store + 事务器（让写路径多步、多表写能原子化）
// [experimental] openStores/StoreBundle：装配底座，取用形态 pre-1.0 可能变。
export { openStores, type StoreBundle } from './store/openStores.ts';
// [internal] 事务器：门面写路径内部用，宿主没理由直接拼。
export { noopTransaction, type Transaction } from './store/transaction.ts';
// Schema 版本化 / 迁移器（0.2.0）：openStores 自动跑；这里导出供诊断、dry-run 预检、迁移工具用。
// [experimental] LATEST_SCHEMA_VERSION 及迁移器：供诊断/迁移工具，形态 pre-1.0 可能变。
export {
  runMigrations,
  getSchemaVersion,
  LATEST_SCHEMA_VERSION,
  type Migration,
  type MigrationResult,
  type RunMigrationsOptions,
} from './store/migrations.ts';

// 画像生成（写路径）
// [internal] 画像生成算子（consolidate/updateProfile/confidence）：写路径散装件，门面已收口。
export { consolidate, type ConsolidateDeps, type ConsolidateResult } from './consolidation/consolidate.ts';
export { updateProfile, type UpdateProfileDeps, type UpdateProfileResult, type UpdateProfileTimings } from './consolidation/updateProfile.ts';
export { computeConfidence, deriveCredStatus, type ConfidenceInputs } from './consolidation/confidence.ts';

// M4 归因（可解释假设）+ M5 带证据主动询问（阶段 3）
// [internal] 归因/主动询问/冲突复访算子：门面不暴露，写路径散装件（唯一直用点是 dev 测试台）。
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
// [internal] 后台周期算子（decay/expire/trends）：门面/背景流程内部用，宿主没理由直接拼。
export { decayFactor, halfLifeOf, effectiveConfidence } from './background/decay.ts';
export { expire, type ExpireDeps, type ExpireResult } from './background/expire.ts';
export { aggregateTrends, type AggregateTrendsDeps, type TrendResult } from './background/trends.ts';

// 召回底座（可替换）+ 嵌入器（云端优先可替换）
// [experimental] Retriever 扩展点接口 + 内建实现：可替换注入点，接口签名 pre-1.0 可能演进。
export { type Retriever, type RetrievalHit } from './retrieval/retriever.ts';
export { NullRetriever } from './retrieval/nullRetriever.ts';
export { VectorRetriever } from './retrieval/vectorRetriever.ts';
// [experimental] Embedder 扩展点接口 + 内建实现：可替换注入点，接口签名 pre-1.0 可能演进。
export {
  type Embedder,
  type EmbedConfig,
  OpenAICompatEmbedder,
  loadEmbedConfig,
} from './retrieval/embedder.ts';

// LLM 客户端
// [experimental] LLMClient 扩展点接口 + 内建实现/池：可替换注入点，接口签名 pre-1.0 可能演进。
export {
  type LLMClient,
  type ChatMessage,
  type ModelTier,
  type UsageStats,
  OpenAICompatClient,
  loadLLMConfig,
} from './llm/client.ts';
export { loadLLMPool, type LLMPool, type LLMPurpose } from './llm/pool.ts';

// 插件契约 v2（第 7 步）
// [experimental] MemoWeftPlugin / PluginContext / 权限 / hook 类型：pre-1.0 契约，hook 签名可能演进。
export {
  type MemoWeftPlugin,
  type PluginType,
  type PluginContext,
  type PluginPermissions,
  type PluginObservationInput,
  type PluginUserMessage,
} from './plugin/contract.ts';

// [internal] jsonRepair 散装函数：门面/算子内部用，宿主没理由直接拼。
export {
  extractJsonObject,
  parseJsonObject,
  parseJsonObjectWithRepair,
  type ParseWithRepairDeps,
} from './llm/jsonRepair.ts';

// 统一 Core 入口（架构归位·批次2）：Host 优先经它调 Core，不散装拼底层件
// [stable] 统一 Core 入口：createMemoWeftCore / MemoWeftCore 及各 *Input/返回类型，宿主主入口。
export {
  createMemoWeftCore,
  type MemoWeftCore,
  type CreateCoreOptions,
  type UserMessageInput,
  type ObservationInput,
  type ToolResultInput,
  type RecallInput,
  type ConversationInput,
  type UpdateProfileInput,
  type PortableAPI,
  type MemoryGraphAPI,
  type HealthReport,
  type UsageReport,
} from './core/index.ts';

// 受控记忆管理 API（批次2）：7 操作 + 审计表，管理操作带 reason 留痕
// [stable] 受控记忆管理 API：MemoryManagementAPI 接口 + 各入出参类型（门面 core.memory 面）。
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
  type ListMemoryInput,
  type CognitionWithMeta,
  type ResetSubjectInput,
  type ResetSubjectResult,
} from './memory/index.ts';
// [experimental] ManagementLogEntry：弱类型审计条目（op/targetKind 为 string），门面不暴露读审计路径。
export { type ManagementLogEntry } from './memory/index.ts';
// [internal] 管理 API 依赖装配 + 审计表实现：门面已收口，宿主没理由直接拼。
export {
  type MemoryManagementDeps,
  SqliteManagementLog,
  type ManagementLog,
} from './memory/index.ts';

// 共享召回（批次2 抽取）：Conversation 与 core.recall 共用的同一段召回语义
// [internal] recallCognitions 散装函数：门面 recall/Conversation 内部共用，已被门面收口。
export { recallCognitions, type RecallDeps, type RecalledCognitionItem } from './retrieval/recall.ts';

// 管线 / 会话编排
// [stable] 会话返回形状：TurnOutcome / RecalledCognition 由 handleConversationTurn/recall 回吐宿主。
export { type TurnOutcome, type RecalledCognition, type RecalledEvidence } from './pipeline/conversation.ts';
// [internal] Conversation 类 + 其 Deps：门面 handleConversationTurn 内包，宿主不直接 new。
export { Conversation, type ConversationDeps } from './pipeline/conversation.ts';
// [internal] perceive / WorkingMemory：管线内部件，门面已收口。
export { perceive, type PerceiveOptions } from './pipeline/perceive.ts';
export { WorkingMemory, type Turn } from './pipeline/workingMemory.ts';

// 感知 / 多源摄入（阶段 4-A）：Core 只保留【通用观察摄入口】（generic Observation + ingestObservations）。
// [stable] Observation：跨层契约「采集插件→Host→Core」，ingestObservation 入参（meta 字段/kind 开放集见契约 experimental）。
export {
  type Observation,
} from './perception/ingest.ts';
// [internal] ingestObservations 算子 + 其 Deps/Result：门面 ingestObservation 内部用。
export {
  ingestObservations,
  type IngestDeps,
  type IngestResult,
} from './perception/ingest.ts';
// 真实采集（活动窗口等）已【整体迁出 Core】到采集插件 plugins/collector-active-window/（架构归位，boundaries.md §4.1）：
// 窗口采样、样本→Observation 映射、采集循环都是 Plugin 层知识；采集插件经 Host /api/observe → core.ingestObservation 落库。

// 配置
// [stable] 配置形状：MemoWeftConfig「有哪些配置项」形状定型（0.4.0 加可选 language:'zh'|'en'，additive 非破坏）；cloudReadDefault() 稳定；Lang 供宿主设 config.language。DlaConfig 是 @deprecated 别名（勿删）。
export { cloudReadDefault, type MemoWeftConfig, type DlaConfig, type Lang } from './config.ts';
// [experimental] 可注入时钟（Phase 4）：Clock 类型 + systemClock 缺省。宿主注入以求确定性 / 时间旅行；只产时间戳、不进置信度自算。
export { systemClock, type Clock } from './clock.ts';
// [experimental] config 取用方式：单例访问「怎么拿到 config」pre-1.0 可能变（作者拍板⑥，预留 P2-5 去单例）；配置项形状本身 stable（见上）。
export { config } from './config.ts';

// 便携记忆包（导入/导出/备份/恢复 · Phase 5-A）：让用户记忆成为可迁移资产
// [stable] 便携包格式常量、包结构类型 + 门面 core.portable 收口的导入/导出/校验入出参形状。
export {
  type ExportOptions,
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
// [internal] 散装 exportBundle/importBundle/validateBundle 函数 + 其 Deps：已被门面 core.portable 收口。
export {
  exportBundle,
  type ExportDeps,
  validateBundle,
  importBundle,
  type ImportDeps,
} from './portable/index.ts';

// 图谱化记忆视图（Phase 6-B）：三层数据 → 力导向图 payload
// [stable] 图谱 payload 类型：门面 core.graph 收口的 MemoryGraphPayload 及节点/边/统计形状（conflicts_with/corrects 边见契约 experimental）。
export {
  type MemoryGraphNode,
  type MemoryGraphEdge,
  type MemoryGraphNodeKind,
  type MemoryGraphEdgeKind,
  type MemoryGraphStats,
  type MemoryGraphPayload,
} from './graph/index.ts';
// [internal] 散装 buildMemoryGraph 函数 + 其 Deps/Options：已被门面 core.graph 收口。
export {
  buildMemoryGraph,
  type BuildGraphDeps,
  type BuildGraphOptions,
} from './graph/index.ts';

// [stable] 版本常量：宿主可读的包版本号。
import { MEMOWEFT_VERSION } from './version.ts';
export { MEMOWEFT_VERSION };
/** @deprecated 用 MEMOWEFT_VERSION；保留旧名兼容已引用 DLA_VERSION 的宿主。 */
export const DLA_VERSION = MEMOWEFT_VERSION;
