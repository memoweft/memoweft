/**
 * 构建图谱 payload。
 *
 * 结构（边方向）：
 *   subject --belongs_to_subject--> cognition
 *   evidence --supports/contradicts--> cognition   （来自 cognition_evidence.relation）
 *   evidence --distilled_into--> event             （来自 event_evidence）
 * 事件与认知【不直接连】——只通过共享证据间接相连（这是真数据结构，别硬造直接边）。
 *
 * 默认策略（防毛线球）：includeEvidence=false 时只出 subject + 活跃认知 + belongs_to_subject，
 * 前端点开某认知再传 includeEvidence=true 展开它的证据链。
 */
import type { EvidenceStore } from '../evidence/store.ts';
import type { EventStore } from '../event/store.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { Cognition, ContentType, CredStatus } from '../cognition/model.ts';
import type { Evidence, SourceKind } from '../evidence/model.ts';
import type { MemoryGraphNode, MemoryGraphEdge, MemoryGraphPayload } from './model.ts';

export interface BuildGraphDeps {
  evidenceStore: EvidenceStore;
  eventStore: EventStore;
  cognitionStore: CognitionStore;
}

export interface BuildGraphOptions {
  /** 生成时间戳（ISO）；缺省取当前时间。测试可注入以求确定性。 */
  now?: string;
  scope?: 'global' | 'local';
  depth?: number;
  /** 是否展开 evidence / event 层。默认 true（建全图）；前端默认高层视图可传 false。 */
  includeEvidence?: boolean;
  /** 是否含已失效认知（invalidAt≠null）。默认 false = 只活跃。 */
  includeInvalid?: boolean;
  /** 是否含已归档认知（archivedAt≠null）。默认 false，与 invalid 项采用相同的图谱过滤语义。 */
  includeArchived?: boolean;
  /** 按认知内容类型过滤。 */
  contentType?: ContentType | ContentType[];
  /** 按可信状态过滤。 */
  credStatus?: CredStatus | CredStatus[];
  /** 按证据来源过滤。 */
  sourceKind?: SourceKind | SourceKind[];
  /** 只留 allowCloudRead=false 的证据（云端受限）。 */
  onlyCloudBlocked?: boolean;
  /** 只留冲突中的认知。 */
  onlyConflicts?: boolean;
  /** 只留假设类认知。 */
  onlyHypotheses?: boolean;
  /** 关键词（命中认知内容，大小写不敏感）。 */
  q?: string;
}

const SUBJECT_PREFIX = 'subject:';

function asArray<T>(v: T | T[] | undefined): T[] | undefined {
  if (v === undefined) return undefined;
  return Array.isArray(v) ? v : [v];
}

function truncate(s: string, n: number): string {
  return s.length <= n ? s : s.slice(0, n - 1) + '…';
}

function cognitionColorKey(c: Cognition): string {
  if (c.credStatus === 'conflicted') return 'conflicted';
  if (c.credStatus === 'contested') return 'contested'; // 单独标色：有争议但支撑仍占优，不与完全冲突同色
  if (c.invalidAt != null) return 'invalid';
  if (c.archivedAt != null) return 'archived'; // 仅 includeArchived=true 时可见（默认被过滤）
  return c.contentType;
}

export function buildMemoryGraph(
  subjectId: string,
  deps: BuildGraphDeps,
  opts: BuildGraphOptions = {},
): MemoryGraphPayload {
  const { evidenceStore, eventStore, cognitionStore } = deps;
  const includeEvidence = opts.includeEvidence ?? true;
  const includeInvalid = opts.includeInvalid ?? false;
  const includeArchived = opts.includeArchived ?? false;
  const contentTypes = asArray(opts.contentType);
  const credStatuses = asArray(opts.credStatus);
  const sourceKinds = asArray(opts.sourceKind);
  const q = opts.q?.trim().toLowerCase();

  const nodes: MemoryGraphNode[] = [];
  const edges: MemoryGraphEdge[] = [];
  let hiddenCount = 0;

  // subject 中心节点
  const subjectNodeId = SUBJECT_PREFIX + subjectId;
  nodes.push({
    id: subjectNodeId,
    kind: 'subject',
    label: subjectId,
    val: 12,
    colorKey: 'subject',
  });

  // ── 认知筛选 ──
  const cognitions = cognitionStore.all(subjectId).filter((c) => {
    let keep = true;
    if (!includeInvalid && c.invalidAt != null) keep = false;
    if (keep && !includeArchived && c.archivedAt != null) keep = false; // 归档默认不进图（invalid 同款待遇）
    if (keep && contentTypes && !contentTypes.includes(c.contentType)) keep = false;
    if (keep && credStatuses && !credStatuses.includes(c.credStatus)) keep = false;
    // onlyConflicts 收两档：contested 的那些在中间态引入【之前】就是 conflicted、本来会被选中，
    //   只留 conflicted 会让它们从这个视图里静默消失——那是回归，不是收窄。用户要看的是"有争议的记忆"。
    if (keep && opts.onlyConflicts && c.credStatus !== 'conflicted' && c.credStatus !== 'contested')
      keep = false;
    if (keep && opts.onlyHypotheses && c.contentType !== 'hypothesis') keep = false;
    if (keep && q && !c.content.toLowerCase().includes(q)) keep = false;
    if (!keep) hiddenCount++;
    return keep;
  });
  const includedCognitionIds = new Set(cognitions.map((c) => c.id));

  for (const c of cognitions) {
    nodes.push({
      id: c.id,
      kind: 'cognition',
      label: truncate(c.content, 40),
      summary: c.content,
      contentType: c.contentType,
      formedBy: c.formedBy,
      confidence: c.confidence,
      credStatus: c.credStatus,
      createdAt: c.createdAt,
      updatedAt: c.updatedAt,
      invalidAt: c.invalidAt,
      archivedAt: c.archivedAt ?? null,
      val: 4 + Math.max(1, c.confidence / 150),
      colorKey: cognitionColorKey(c),
    });
    edges.push({
      id: `bs:${c.id}`,
      source: subjectNodeId,
      target: c.id,
      kind: 'belongs_to_subject',
      dashed: true,
    });
  }

  // ── 证据 / 事件（provenance）：仅 includeEvidence 时展开 ──
  const includedEvidenceIds = new Set<string>();
  if (includeEvidence) {
    // 被包含认知引用的证据 + 关系
    const links: Array<{
      evidenceId: string;
      cognitionId: string;
      relation: 'support' | 'contradict';
    }> = [];
    for (const c of cognitions) {
      for (const l of cognitionStore.sourcesOf(c.id)) {
        links.push({ evidenceId: l.evidenceId, cognitionId: c.id, relation: l.relation });
      }
    }

    // 取回候选证据、按证据侧过滤后建节点
    const evidenceById = new Map<string, Evidence>();
    for (const eid of new Set(links.map((l) => l.evidenceId))) {
      const e = evidenceStore.get(eid);
      if (!e) continue; // 溯源指向的证据可能已被删（防御）
      let keep = true;
      if (sourceKinds && !sourceKinds.includes(e.sourceKind)) keep = false;
      if (keep && opts.onlyCloudBlocked && e.allowCloudRead) keep = false;
      if (!keep) {
        hiddenCount++;
        continue;
      }
      evidenceById.set(eid, e);
    }
    for (const e of evidenceById.values()) {
      includedEvidenceIds.add(e.id);
      nodes.push({
        id: e.id,
        kind: 'evidence',
        label: truncate(e.summary || e.rawContent, 40),
        summary: e.summary,
        sourceKind: e.sourceKind,
        allowCloudRead: e.allowCloudRead,
        allowInference: e.allowInference,
        occurredAt: e.occurredAt,
        createdAt: e.recordedAt,
        val: 2,
        colorKey:
          e.sourceKind === 'observed' || e.sourceKind === 'tool' ? e.sourceKind : 'evidence',
      });
    }

    // supports / contradicts 边（两端都在才连）
    for (const l of links) {
      if (!includedEvidenceIds.has(l.evidenceId) || !includedCognitionIds.has(l.cognitionId))
        continue;
      edges.push({
        id: `ev:${l.evidenceId}:${l.cognitionId}:${l.relation}`,
        source: l.evidenceId,
        target: l.cognitionId,
        kind: l.relation === 'support' ? 'supports' : 'contradicts',
        dashed: l.relation === 'contradict',
      });
    }

    // 事件层：覆盖了任一被包含证据的事件 → 建节点 + distilled_into 边
    for (const ev of eventStore.all(subjectId)) {
      const covered = eventStore.evidenceOf(ev.id).filter((eid) => includedEvidenceIds.has(eid));
      if (covered.length === 0) continue;
      nodes.push({
        id: ev.id,
        kind: 'event',
        label: truncate(ev.summary, 40),
        summary: ev.summary,
        occurredAt: ev.occurredAt,
        createdAt: ev.createdAt,
        val: 4,
        colorKey: 'event',
      });
      for (const eid of covered) {
        edges.push({
          id: `di:${eid}:${ev.id}`,
          source: eid,
          target: ev.id,
          kind: 'distilled_into',
        });
      }
    }
  }

  return {
    subjectId,
    generatedAt: opts.now ?? new Date().toISOString(),
    scope: opts.scope ?? 'global',
    depth: opts.depth ?? (includeEvidence ? 2 : 1),
    nodes,
    edges,
    stats: {
      nodeCount: nodes.length,
      edgeCount: edges.length,
      hiddenCount,
      activeCognitionCount: cognitions.filter((c) => c.invalidAt == null).length,
      conflictedCount: cognitions.filter((c) => c.credStatus === 'conflicted').length,
      contestedCount: cognitions.filter((c) => c.credStatus === 'contested').length,
      hypothesisCount: cognitions.filter((c) => c.contentType === 'hypothesis').length,
      observedEvidenceCount: nodes.filter(
        (n) => n.kind === 'evidence' && n.sourceKind === 'observed',
      ).length,
      toolEvidenceCount: nodes.filter((n) => n.kind === 'evidence' && n.sourceKind === 'tool')
        .length,
    },
  };
}
