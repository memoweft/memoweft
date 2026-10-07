/** Portable Memory Bundle v4 data, identity and import-plan contracts. */
import { createHash } from 'node:crypto';
import type { Evidence } from '../evidence/model.ts';
import type { Event } from '../event/model.ts';
import type { Cognition, EvidenceRelation } from '../cognition/model.ts';
import type { InteractionContext, SemanticResolution } from '../interaction/model.ts';

export const BUNDLE_FORMAT = 'memoweft-bundle';
export const BUNDLE_SCHEMA_VERSION = 4;
export const PLAN_SCHEMA_VERSION = 1;

export interface EventEvidenceLink {
  eventId: string;
  evidenceId: string;
}

export interface CognitionEvidenceLink {
  cognitionId: string;
  evidenceId: string;
  relation: EvidenceRelation;
}

export interface PortableEvidence extends Evidence {
  deletedAt?: string | null;
}

export interface PortableEntity {
  id: string;
  worldId: string;
  kind: string;
  canonicalName: string;
  aliases: string[];
  invalidAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface EntityEvidenceLink {
  entityId: string;
  evidenceId: string;
  relation: 'support';
  start: number | null;
  end: number | null;
}

export interface PortableRelationship {
  id: string;
  worldId: string;
  sourceEntityId: string;
  targetEntityId: string;
  relationType: string;
  content: string;
  formedBy: string;
  confidence: number;
  credStatus: string;
  invalidAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface PortableWorldEvent {
  id: string;
  worldId: string;
  content: string;
  occurredAt: string | null;
  timeExpression: string | null;
  participants: Array<{ canonicalName: string; kind: string }>;
  objects: Array<{ canonicalName: string; kind: string }>;
  formedBy: string;
  confidence: number;
  credStatus: string;
  invalidAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface PortableRetraction {
  id: string;
  priorCognitionId: string | null;
  priorRelationshipId: string | null;
  priorEventId: string | null;
  reason: string;
  revision: number;
  createdAt: string;
}

export interface PortableCognitionTransition {
  id: string;
  priorCognitionId: string;
  replacementCognitionId: string;
  reason: string;
  revision: number;
}

export interface PortableWorldItemLifecycle {
  subjectId: string;
  objectKind: 'entity' | 'relationship' | 'event' | 'cognition';
  itemId: string;
  archivedAt: string | null;
  mutedAt: string | null;
  updatedAt: string;
}

export interface MemoryBundle {
  format: string;
  schemaVersion: number;
  /** v4 only. v2/v3 readers accept bundles without this field. */
  bundleId?: string;
  exportedAt: string;
  memoWeftVersion: string;
  subjectId: string;
  /** v4 only; equals subjectId in source bytes and survives target remap planning. */
  sourceSubjectId?: string;
  worldRevision?: number;
  worldSnapshotHash?: string;
  source: { hostId: string; exportMode: 'full' };
  data: {
    evidence: PortableEvidence[];
    events: Event[];
    eventEvidence: EventEvidenceLink[];
    cognitions: Cognition[];
    cognitionEvidence: CognitionEvidenceLink[];
    unconsolidatedEventIds: string[];
    interactionContexts?: InteractionContext[];
    semanticResolutions?: SemanticResolution[];
    /** v3+ World sections. */
    entities?: PortableEntity[];
    /** v4 entity formation provenance retained from the durable ledger. */
    entityEvidence?: EntityEvidenceLink[];
    relationships?: PortableRelationship[];
    relationshipEvidence?: Array<{
      relationshipId: string;
      evidenceId: string;
      relation: EvidenceRelation;
    }>;
    worldEvents?: PortableWorldEvent[];
    worldEventEvidence?: Array<{
      worldEventId: string;
      evidenceId: string;
      relation: EvidenceRelation;
    }>;
    cognitionTargets?: Array<{
      cognitionId: string;
      targetEntityId: string;
      perspectiveEntityId: string | null;
    }>;
    /** v4 durable history/currentness. */
    retractions?: PortableRetraction[];
    cognitionTransitions?: PortableCognitionTransition[];
    worldItemLifecycle?: PortableWorldItemLifecycle[];
  };
  metadata: {
    counts: {
      evidence: number;
      events: number;
      cognitions: number;
      entities?: number;
      entityEvidence?: number;
      relationships?: number;
      worldEvents?: number;
      retractions?: number;
      cognitionTransitions?: number;
      worldItemLifecycle?: number;
    };
    notes: string[];
  };
}

export type ImportMode = 'dryRun' | 'merge';

export interface ValidateResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export interface ImportPlan {
  mode: ImportMode;
  valid: boolean;
  errors: string[];
  warnings: string[];
  counts: {
    evidence: number;
    events: number;
    cognitions: number;
    eventEvidence: number;
    cognitionEvidence: number;
    interactionContexts: number;
    semanticResolutions: number;
    entities?: number;
    entityEvidence?: number;
    relationships?: number;
    worldEvents?: number;
    relationshipEvidence?: number;
    worldEventEvidence?: number;
    cognitionTargets?: number;
    retractions?: number;
    cognitionTransitions?: number;
    worldItemLifecycle?: number;
    evidenceTombstones?: number;
  };
  duplicates: {
    evidence: number;
    events: number;
    cognitions: number;
    entities?: number;
    relationships?: number;
    worldEvents?: number;
    retractions?: number;
    cognitionTransitions?: number;
    worldItemLifecycle?: number;
  };
  bundleId?: string;
  sourceSubjectId?: string;
  targetSubjectId?: string;
  targetWorldRevision?: number;
  targetSnapshotHash?: string;
  conflicts?: Array<{ kind: string; id: string; code: string }>;
  wouldAdvanceRevision?: boolean;
  planHash?: string;
  commandId?: string;
  receiptId?: string;
}

function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item) => canonicalValue(item));
  if (value !== null && typeof value === 'object') {
    const source = value as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(source).sort()) {
      if (source[key] !== undefined) out[key] = canonicalValue(source[key]);
    }
    return out;
  }
  return value;
}

export function canonicalJson(value: unknown): string {
  const encoded = JSON.stringify(canonicalValue(value));
  if (encoded === undefined) throw new TypeError('value_is_not_json');
  return encoded;
}

export function canonicalSha256(value: unknown): string {
  return createHash('sha256').update(canonicalJson(value), 'utf8').digest('hex');
}

export function deriveBundleId(bundle: unknown): string {
  if (bundle === null || typeof bundle !== 'object' || Array.isArray(bundle))
    throw new TypeError('bundle_must_be_object');
  const payload = { ...(bundle as Record<string, unknown>) };
  delete payload.bundleId;
  return `portable:v4:${canonicalSha256(payload)}`;
}

export function derivePlanIds(payload: unknown): {
  planHash: string;
  commandId: string;
  receiptId: string;
} {
  const planHash = canonicalSha256(payload);
  return {
    planHash,
    commandId: `portable:command:v1:${planHash}`,
    receiptId: `portable:receipt:v1:${planHash}`,
  };
}

/** Cross-language fresh-target plan oracle used by the shared v4 fixture.
 * Runtime imports still perform real target collision queries in importBundle/Core. */
export function deriveFreshPortableV4Plan(
  bundle: MemoryBundle,
  targetSubjectId: string,
  targetWorldRevision = 0,
  targetSnapshotHash = '',
): ReturnType<typeof derivePlanIds> & { payload: Record<string, unknown> } {
  const data = bundle.data;
  const counts = {
    evidence: data.evidence.length,
    events: data.events.length,
    cognitions: data.cognitions.length,
    eventEvidence: data.eventEvidence.length,
    cognitionEvidence: data.cognitionEvidence.length,
    interactionContexts: (data.interactionContexts ?? []).length,
    semanticResolutions: (data.semanticResolutions ?? []).length,
    entities: (data.entities ?? []).length,
    entityEvidence: (data.entityEvidence ?? []).length,
    relationships: (data.relationships ?? []).length,
    worldEvents: (data.worldEvents ?? []).length,
    relationshipEvidence: (data.relationshipEvidence ?? []).length,
    worldEventEvidence: (data.worldEventEvidence ?? []).length,
    cognitionTargets: (data.cognitionTargets ?? []).length,
    retractions: (data.retractions ?? []).length,
    cognitionTransitions: (data.cognitionTransitions ?? []).length,
    worldItemLifecycle: (data.worldItemLifecycle ?? []).length,
    evidenceTombstones: data.evidence.filter((item) => item.deletedAt != null).length,
  };
  const duplicates = {
    evidence: 0,
    events: 0,
    cognitions: 0,
    entities: 0,
    relationships: 0,
    worldEvents: 0,
    retractions: 0,
    cognitionTransitions: 0,
    worldItemLifecycle: 0,
  };
  const writeSet = {
    evidence: data.evidence.map((item) => item.id),
    events: data.events.map((item) => item.id),
    cognitions: data.cognitions.map((item) => item.id),
    eventEvidence: data.eventEvidence.map((item) => `${item.eventId}/${item.evidenceId}`),
    cognitionEvidence: data.cognitionEvidence.map(
      (item) => `${item.cognitionId}/${item.evidenceId}/${item.relation}`,
    ),
    interactionContexts: (data.interactionContexts ?? []).map((item) => item.id),
    semanticResolutions: (data.semanticResolutions ?? []).map((item) => item.id),
    entities: (data.entities ?? []).map((item) => item.id),
    entityEvidence: (data.entityEvidence ?? []).map(
      (item) =>
        `${item.entityId}/${item.evidenceId}/${item.relation}/${item.start ?? ''}/${item.end ?? ''}`,
    ),
    relationships: (data.relationships ?? []).map((item) => item.id),
    worldEvents: (data.worldEvents ?? []).map((item) => item.id),
    relationshipEvidence: (data.relationshipEvidence ?? []).map(
      (item) => `${item.relationshipId}/${item.evidenceId}/${item.relation}`,
    ),
    worldEventEvidence: (data.worldEventEvidence ?? []).map(
      (item) => `${item.worldEventId}/${item.evidenceId}/${item.relation}`,
    ),
    cognitionTargets: (data.cognitionTargets ?? []).map(
      (item) => `${item.cognitionId}/${item.targetEntityId}/${item.perspectiveEntityId ?? ''}`,
    ),
    retractions: (data.retractions ?? []).map((item) => item.id),
    cognitionTransitions: (data.cognitionTransitions ?? []).map((item) => item.id),
    worldItemLifecycle: (data.worldItemLifecycle ?? []).map(
      (item) => `${item.objectKind}/${item.itemId}`,
    ),
    evidenceTombstones: data.evidence
      .filter((item) => item.deletedAt != null)
      .map((item) => item.id),
  };
  for (const values of Object.values(writeSet)) values.sort();
  const payload: Record<string, unknown> = {
    planVersion: PLAN_SCHEMA_VERSION,
    bundleId: bundle.bundleId,
    sourceSubjectId: bundle.sourceSubjectId ?? bundle.subjectId,
    targetSubjectId,
    targetWorldRevision,
    targetSnapshotHash,
    valid: true,
    conflicts: [],
    warnings: [],
    counts,
    duplicates,
    writeSet,
    wouldAdvanceRevision: Object.values(counts).some((value) => value > 0),
  };
  return { payload, ...derivePlanIds(payload) };
}
