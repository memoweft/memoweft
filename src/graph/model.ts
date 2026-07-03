/**
 * 图谱化记忆视图 · 数据模型（Phase 6-B G1）。
 *
 * 把三层数据（evidence → event → cognition）+ 溯源关系转成前端力导向图能直接吃的
 * { nodes, edges } payload。后端统一产出，前端不直接读库拼图（借鉴 Cytoscape：图展示与图数据结构分开）。
 *
 * 诚实边界（本轮核对真数据模型）：
 *  - 能从库里【导出】的边：belongs_to_subject / distilled_into / supports / contradicts。
 *  - conflicts_with / corrects（认知↔认知）当前【数据没存】——cognition 表没有指向"和谁冲突/被谁纠正"的字段，
 *    只有 credStatus='conflicted' 标记和 invalidAt。故 V1 不生成这两种边（枚举保留，等数据模型补了再产）：
 *    冲突通过节点 credStatus='conflicted' 体现，失效通过 invalidAt 体现。
 */
import type { ContentType, FormedBy, CredStatus } from '../cognition/model.ts';
import type { SourceKind } from '../evidence/model.ts';

export type MemoryGraphNodeKind = 'subject' | 'evidence' | 'event' | 'cognition';

export type MemoryGraphEdgeKind =
  | 'belongs_to_subject' // subject → cognition
  | 'distilled_into' //     evidence → event
  | 'supports' //           evidence → cognition（relation=support）
  | 'contradicts' //        evidence → cognition（relation=contradict）
  | 'conflicts_with' //     cognition ↔ cognition（V1 未生成：数据未存 cognition↔cognition 链）
  | 'corrects'; //          new cognition → old cognition（V1 未生成：同上）

export interface MemoryGraphNode {
  id: string;
  kind: MemoryGraphNodeKind;
  label: string;
  summary?: string;
  // 仅 cognition
  contentType?: ContentType;
  formedBy?: FormedBy;
  confidence?: number; // 0~1000
  credStatus?: CredStatus;
  // 仅 evidence
  sourceKind?: SourceKind;
  allowCloudRead?: boolean;
  allowInference?: boolean;
  // 通用时间
  occurredAt?: string;
  createdAt?: string;
  updatedAt?: string;
  invalidAt?: string | null;
  // 渲染提示（前端可覆盖）
  val?: number;
  colorKey?: string;
}

export interface MemoryGraphEdge {
  id: string;
  source: string;
  target: string;
  kind: MemoryGraphEdgeKind;
  label?: string;
  dashed?: boolean;
}

export interface MemoryGraphStats {
  nodeCount: number;
  edgeCount: number;
  hiddenCount: number;
  activeCognitionCount: number;
  conflictedCount: number;
  hypothesisCount: number;
  observedEvidenceCount: number;
}

export interface MemoryGraphPayload {
  subjectId: string;
  generatedAt: string;
  scope: 'global' | 'local';
  depth: number;
  nodes: MemoryGraphNode[];
  edges: MemoryGraphEdge[];
  stats: MemoryGraphStats;
}
