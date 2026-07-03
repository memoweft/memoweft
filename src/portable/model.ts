/**
 * 便携记忆包（Portable Memory Bundle）· 数据模型（Phase 5-A）。
 *
 * 目标：把某个 subject 的三层记忆（evidence → event → cognition）+ 溯源关系
 * 打成一个可读、可校验、可版本化的 JSON 包，用于导出 / 备份 / 迁移 / 恢复。
 *
 * 保真原则（作者拍板）：保留原 id 与全部时间戳，导入后 get(原id) 仍成立、溯源链不丢。
 * 不含：向量索引（派生物，导入后 retriever.indexAll 重建）、logs、.env / API key、宿主 UI 状态。
 */
import type { Evidence } from '../evidence/model.ts';
import type { Event } from '../event/model.ts';
import type { Cognition, EvidenceRelation } from '../cognition/model.ts';

/** 包格式标记（用于导入前辨认）。 */
export const BUNDLE_FORMAT = 'memoweft-bundle';
/** 包结构版本（结构演进时 +1，配合 validate/migration）。 */
export const BUNDLE_SCHEMA_VERSION = 1;

/** 事件 → 覆盖的原话证据（对应 event_evidence 表一行）。 */
export interface EventEvidenceLink {
  eventId: string;
  evidenceId: string;
}

/** 认知 → 溯源证据 + 关系（对应 cognition_evidence 表一行）。 */
export interface CognitionEvidenceLink {
  cognitionId: string;
  evidenceId: string;
  relation: EvidenceRelation;
}

/** 一个便携记忆包（导出产物 / 导入入参）。 */
export interface MemoryBundle {
  format: string;
  schemaVersion: number;
  exportedAt: string;
  memoWeftVersion: string;
  subjectId: string;
  source: {
    hostId: string;
    exportMode: 'full';
  };
  data: {
    evidence: Evidence[];
    events: Event[];
    eventEvidence: EventEvidenceLink[];
    cognitions: Cognition[];
    cognitionEvidence: CognitionEvidenceLink[];
    /** 导出时尚未消化（consolidated=0）的事件 id；导入按此还原 consolidated 标记（保真，防漏消化）。 */
    unconsolidatedEventIds: string[];
  };
  metadata: {
    counts: {
      evidence: number;
      events: number;
      cognitions: number;
    };
    notes: string[];
  };
}

/** 导入模式：dryRun 只校验不写；merge 合并导入（按 id/originId 去重）。replace 留 V2。 */
export type ImportMode = 'dryRun' | 'merge';

/** 校验结果（validateBundle 产出，也并入 ImportPlan）。 */
export interface ValidateResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

/** 导入计划 / 结果：dryRun 只算不写；merge 反映实际写入。 */
export interface ImportPlan {
  mode: ImportMode;
  valid: boolean;
  errors: string[];
  warnings: string[];
  /** dryRun：将新写入的条数；merge：实际新写入条数（已存在的计入 duplicates，不重复写）。 */
  counts: {
    evidence: number;
    events: number;
    cognitions: number;
    eventEvidence: number;
    cognitionEvidence: number;
  };
  /** 因 id 已存在（或 originId 冲突）而跳过、未重复写入的条数。 */
  duplicates: {
    evidence: number;
    events: number;
    cognitions: number;
  };
}
