/**
 * 便携记忆包（Portable Memory Bundle）对外汇出。
 * 让 MemoWeft 的用户记忆成为可导出 / 备份 / 迁移 / 恢复的资产。
 */
export { exportBundle, type ExportDeps, type ExportOptions } from './exportBundle.ts';
export { validateBundle } from './validateBundle.ts';
export {
  importBundle,
  type ImportDeps,
  type ImportOptions,
  type PortableV4Adapter,
  type PortableV4AdapterPlan,
} from './importBundle.ts';
export {
  BUNDLE_FORMAT,
  BUNDLE_SCHEMA_VERSION,
  PLAN_SCHEMA_VERSION,
  canonicalJson,
  canonicalSha256,
  deriveBundleId,
  derivePlanIds,
  deriveFreshPortableV4Plan,
  type MemoryBundle,
  type EventEvidenceLink,
  type CognitionEvidenceLink,
  type PortableEvidence,
  type PortableEntity,
  type PortableRelationship,
  type PortableWorldEvent,
  type PortableRetraction,
  type PortableCognitionTransition,
  type PortableWorldItemLifecycle,
  type ImportMode,
  type ImportPlan,
  type ValidateResult,
} from './model.ts';
