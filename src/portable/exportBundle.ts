/**
 * 导出便携记忆包。
 *
 * 读某 subject 的三层数据 + 溯源关系，组装成 MemoryBundle。
 * 保真：保留原 id 与时间戳（用 store 的读接口原样取回）。
 * 不含向量索引 / logs / .env——那些不是记忆本体（向量是派生物，导入后重建）。
 */
import type { EvidenceStore } from '../evidence/store.ts';
import type { EventStore } from '../event/store.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { InteractionContextStore } from '../interaction/interactionContextStore.ts';
import type { SemanticResolutionStore } from '../interaction/semanticResolutionStore.ts';
import { MEMOWEFT_VERSION } from '../version.ts';
import {
  BUNDLE_FORMAT,
  BUNDLE_SCHEMA_VERSION,
  deriveBundleId,
  type MemoryBundle,
  type EventEvidenceLink,
  type CognitionEvidenceLink,
} from './model.ts';

export interface ExportDeps {
  evidenceStore: EvidenceStore;
  eventStore: EventStore;
  cognitionStore: CognitionStore;
  /** 交互上下文 store（v0.6）：按 subject 导出交互上下文快照。 */
  interactionContextStore: InteractionContextStore;
  /** 语义解析 store（v0.6）：按导出的证据集过滤导出语义解析。 */
  semanticResolutionStore: SemanticResolutionStore;
  /** Optional v4 World/history adapter for a host that owns those sections.
   * The legacy TypeScript core supplies no adapter and exports a complete,
   * valid v4 bundle with empty World/history arrays and revision 0. */
  portableV4?: {
    worldRevision: number;
    worldSnapshotHash: string;
    data: Pick<
      MemoryBundle['data'],
      | 'entities'
      | 'entityEvidence'
      | 'relationships'
      | 'relationshipEvidence'
      | 'worldEvents'
      | 'worldEventEvidence'
      | 'cognitionTargets'
      | 'retractions'
      | 'cognitionTransitions'
      | 'worldItemLifecycle'
    >;
  };
}

export interface ExportOptions {
  /** 导出时间戳（ISO）；缺省取当前时间。测试可注入以求确定性。 */
  now?: string;
  /** 记进 source.hostId；缺省 'memoweft'。 */
  hostId?: string;
  /** 记进 metadata.notes（人可读备注）。 */
  notes?: string[];
  /** 覆盖 memoWeftVersion；缺省取包版本常量。 */
  memoWeftVersion?: string;
}

/**
 * 导出某 subject 的完整三层记忆 + 溯源关系为一个 MemoryBundle。
 */
export function exportBundle(
  subjectId: string,
  deps: ExportDeps,
  opts: ExportOptions = {},
): MemoryBundle {
  const {
    evidenceStore,
    eventStore,
    cognitionStore,
    interactionContextStore,
    semanticResolutionStore,
  } = deps;

  // evidenceStore.all() 返回全 subject，这里按 subjectId 收口（证据无 subject 过滤读法，靠 filter）。
  const evidence = evidenceStore
    .all()
    .filter((e) => e.subjectId === subjectId)
    .map((e) => ({ ...e, deletedAt: null }));
  const events = eventStore.all(subjectId);
  const cognitions = cognitionStore.all(subjectId);

  const eventEvidence: EventEvidenceLink[] = [];
  for (const ev of events) {
    for (const evidenceId of eventStore.evidenceOf(ev.id)) {
      eventEvidence.push({ eventId: ev.id, evidenceId });
    }
  }

  const cognitionEvidence: CognitionEvidenceLink[] = [];
  for (const c of cognitions) {
    for (const link of cognitionStore.sourcesOf(c.id)) {
      cognitionEvidence.push({
        cognitionId: c.id,
        evidenceId: link.evidenceId,
        relation: link.relation,
      });
    }
  }

  // 保真 consolidated：记下导出时尚未消化的事件，导入端据此还原（防"未消化事件导入后漏消化"）。
  const unconsolidatedEventIds = eventStore.unconsolidated(subjectId).map((e) => e.id);

  // 交互层（v0.6）：上下文按 subject 全取；语义解析按导出的证据集过滤（通过 evidence_id 关联）。
  const interactionContexts = interactionContextStore.all(subjectId);
  const semanticResolutions = semanticResolutionStore.forEvidenceIds(evidence.map((e) => e.id));

  const v4 = deps.portableV4;
  const entities = v4?.data.entities ?? [];
  const entityEvidence = v4?.data.entityEvidence ?? [];
  const relationships = v4?.data.relationships ?? [];
  const relationshipEvidence = v4?.data.relationshipEvidence ?? [];
  const worldEvents = v4?.data.worldEvents ?? [];
  const worldEventEvidence = v4?.data.worldEventEvidence ?? [];
  const cognitionTargets = v4?.data.cognitionTargets ?? [];
  const retractions = v4?.data.retractions ?? [];
  const cognitionTransitions = v4?.data.cognitionTransitions ?? [];
  const worldItemLifecycle = v4?.data.worldItemLifecycle ?? [];
  const bundle: MemoryBundle = {
    format: BUNDLE_FORMAT,
    schemaVersion: BUNDLE_SCHEMA_VERSION,
    exportedAt: opts.now ?? new Date().toISOString(),
    memoWeftVersion: opts.memoWeftVersion ?? MEMOWEFT_VERSION,
    subjectId,
    sourceSubjectId: subjectId,
    worldRevision: v4?.worldRevision ?? 0,
    worldSnapshotHash: v4?.worldSnapshotHash ?? '',
    source: { hostId: opts.hostId ?? 'memoweft', exportMode: 'full' },
    data: {
      evidence,
      events,
      eventEvidence,
      cognitions,
      cognitionEvidence,
      unconsolidatedEventIds,
      interactionContexts,
      semanticResolutions,
      entities,
      entityEvidence,
      relationships,
      relationshipEvidence,
      worldEvents,
      worldEventEvidence,
      cognitionTargets,
      retractions,
      cognitionTransitions,
      worldItemLifecycle,
    },
    metadata: {
      counts: {
        evidence: evidence.length,
        events: events.length,
        cognitions: cognitions.length,
        entities: entities.length,
        entityEvidence: entityEvidence.length,
        relationships: relationships.length,
        worldEvents: worldEvents.length,
        retractions: retractions.length,
        cognitionTransitions: cognitionTransitions.length,
        worldItemLifecycle: worldItemLifecycle.length,
      },
      notes: opts.notes ?? [],
    },
  };
  bundle.bundleId = deriveBundleId(bundle);
  return bundle;
}
