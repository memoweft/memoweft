/**
 * 受控记忆管理 API 对外汇出。
 * 管理操作走 core.memory.*（每个操作带 reason、落审计行），Host 不再直接摸 Sqlite*Store。
 */
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
  type ReinforceCognitionInput,
  type ReinforceCognitionResult,
  type MergeCognitionInput,
  type MergeCognitionResult,
  type ArchiveCognitionInput,
  type MuteCognitionInput,
  type IntegrityIssue,
  type IntegrityReport,
  type MemoryManagementDeps,
  type ListMemoryInput,
  type CognitionWithMeta,
  type ResetSubjectInput,
  type ResetSubjectResult,
} from './managementApi.ts';
export {
  SqliteManagementLog,
  type ManagementLog,
  type ManagementLogEntry,
} from './managementLog.ts';
