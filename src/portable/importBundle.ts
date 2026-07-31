/**
 * 导入便携记忆包。
 *
 * 保真 + 幂等 + 不污染：
 *  - 保真：按【原 id 与时间戳】落库（用 store.insert），溯源链不丢。
 *  - 幂等去重：按 id 判重，已存在则跳过（重复导入不制造重复数据）。
 *  - 引用完整：evidence 因 originId 撞库中另一条不同 id 而无法落库时，标记为悬空、
 *    连带丢弃指向它的 join 行并告警——绝不写出悬空引用。
 *  - 不污染：merge 写入包进一个事务（若传了 transaction），中途失败整体回滚。
 *
 * dryRun：只算不写，返回将写入 / 重复的条数。merge：实际写入。
 *
 * ⚠️ 事务风险（散装调用务必看）：merge 的所有写入只有在 deps.transaction **被传入**时才是原子的。
 *   若不传 transaction 就调 merge，中途任一行失败会留下半截数据、无法回滚（异常收进 plan.errors，
 *   并在 plan.warnings 提示，但残留已成事实）。core 正门（createCore）已固定传 openStores 的 transaction，
 *   无此风险；仅当你绕过 core、手工拼 deps 时才需自己保证传 transaction。
 */
import type { EvidenceStore } from '../evidence/store.ts';
import type { EventStore } from '../event/store.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { InteractionContextStore } from '../interaction/interactionContextStore.ts';
import type { SemanticResolutionStore } from '../interaction/semanticResolutionStore.ts';
import type { Transaction } from '../store/transaction.ts';
import type { EvidenceLink } from '../cognition/model.ts';
import { isEvidenceTombstoned } from '../evidence/tombstoneRegistry.ts';
import { validateBundle } from './validateBundle.ts';
import { resolveLang } from '../config.ts';
import type { ImportMode, ImportPlan, MemoryBundle } from './model.ts';

function sameEvidence(
  left: MemoryBundle['data']['evidence'][number],
  right: MemoryBundle['data']['evidence'][number],
): boolean {
  return (
    left.id === right.id &&
    left.subjectId === right.subjectId &&
    left.sourceKind === right.sourceKind &&
    left.hostId === right.hostId &&
    left.originId === right.originId &&
    left.occurredAt === right.occurredAt &&
    left.recordedAt === right.recordedAt &&
    left.rawContent === right.rawContent &&
    left.summary === right.summary &&
    left.allowLocalRead === right.allowLocalRead &&
    left.allowCloudRead === right.allowCloudRead &&
    left.allowInference === right.allowInference &&
    left.correctsEvidenceId === right.correctsEvidenceId
  );
}

function sameEvent(
  left: MemoryBundle['data']['events'][number],
  right: MemoryBundle['data']['events'][number],
): boolean {
  return (
    left.id === right.id &&
    left.subjectId === right.subjectId &&
    left.summary === right.summary &&
    left.occurredAt === right.occurredAt &&
    left.createdAt === right.createdAt
  );
}

function sameCognition(
  left: MemoryBundle['data']['cognitions'][number],
  right: MemoryBundle['data']['cognitions'][number],
): boolean {
  return (
    left.id === right.id &&
    left.subjectId === right.subjectId &&
    left.content === right.content &&
    left.contentType === right.contentType &&
    left.formedBy === right.formedBy &&
    left.confidence === right.confidence &&
    left.credStatus === right.credStatus &&
    left.scope === right.scope &&
    left.validAt === right.validAt &&
    left.invalidAt === right.invalidAt &&
    left.askedAt === right.askedAt &&
    (left.archivedAt ?? null) === (right.archivedAt ?? null) &&
    (left.mutedAt ?? null) === (right.mutedAt ?? null) &&
    left.createdAt === right.createdAt &&
    left.updatedAt === right.updatedAt
  );
}

function sameStringMultiset(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  const a = [...left].sort();
  const b = [...right].sort();
  return a.every((value, index) => value === b[index]);
}

export interface ImportDeps {
  evidenceStore: EvidenceStore;
  eventStore: EventStore;
  cognitionStore: CognitionStore;
  /** 交互上下文 store（v0.6）：导入交互上下文快照（按 id 判重）。 */
  interactionContextStore: InteractionContextStore;
  /** 语义解析 store（v0.6）：导入语义解析（按 id 判重）。 */
  semanticResolutionStore: SemanticResolutionStore;
  /** 可选事务器：merge 的写入包进一个事务，中途失败整体回滚，避免污染库。 */
  transaction?: Transaction;
}

export interface ImportOptions {
  mode: ImportMode;
}

export function importBundle(
  bundle: MemoryBundle,
  deps: ImportDeps,
  opts: ImportOptions,
): ImportPlan {
  const {
    evidenceStore,
    eventStore,
    cognitionStore,
    interactionContextStore,
    semanticResolutionStore,
  } = deps;
  const lang = resolveLang();

  const validation = validateBundle(bundle);
  const plan: ImportPlan = {
    mode: opts.mode,
    valid: validation.valid,
    errors: [...validation.errors],
    warnings: [...validation.warnings],
    counts: {
      evidence: 0,
      events: 0,
      cognitions: 0,
      eventEvidence: 0,
      cognitionEvidence: 0,
      interactionContexts: 0,
      semanticResolutions: 0,
    },
    duplicates: { evidence: 0, events: 0, cognitions: 0 },
  };
  if (!validation.valid) return plan; // 结构/引用错 → 绝不写库

  const data = bundle.data;
  const unconsolidatedSet = new Set(data.unconsolidatedEventIds ?? []);

  // 同 id 只有在【完整实体 + 自有关系】完全相同时才是安全幂等。若内容、授权或溯源
  // 任一不同仍把包内派生实体接到目标行，会把另一血缘的授权当成许可（authorization
  // laundering）。因此碰撞整包 fail-closed，交给未来显式冲突解决流程，而不是 target-wins。
  const eventEvidenceLinks = new Map<string, string[]>();
  for (const link of data.eventEvidence) {
    const links = eventEvidenceLinks.get(link.eventId) ?? [];
    links.push(link.evidenceId);
    eventEvidenceLinks.set(link.eventId, links);
  }
  const cognitionEvidenceLinks = new Map<string, string[]>();
  for (const link of data.cognitionEvidence) {
    const links = cognitionEvidenceLinks.get(link.cognitionId) ?? [];
    links.push(`${link.evidenceId}\u0000${link.relation}`);
    cognitionEvidenceLinks.set(link.cognitionId, links);
  }

  for (const evidence of data.evidence) {
    const existing = evidenceStore.get(evidence.id);
    if (existing && !sameEvidence(existing, evidence)) {
      plan.errors.push(
        lang === 'zh'
          ? `evidence ${evidence.id} 与目标库同 id 记录内容或授权不一致，拒绝导入`
          : `evidence ${evidence.id} collides with a different target record; import rejected`,
      );
    }
  }
  for (const event of data.events) {
    const existing = eventStore.get(event.id);
    if (!existing) continue;
    const sameLinks = sameStringMultiset(
      eventStore.evidenceOf(event.id),
      eventEvidenceLinks.get(event.id) ?? [],
    );
    const targetUnconsolidated = eventStore
      .unconsolidated(event.subjectId)
      .some((candidate) => candidate.id === event.id);
    if (
      !sameEvent(existing, event) ||
      !sameLinks ||
      targetUnconsolidated !== unconsolidatedSet.has(event.id)
    ) {
      plan.errors.push(
        lang === 'zh'
          ? `event ${event.id} 与目标库同 id 事件的内容、证据关系或消化状态不一致，拒绝导入`
          : `event ${event.id} collides with different target content, evidence links, or consolidation state; import rejected`,
      );
    }
  }
  for (const cognition of data.cognitions) {
    const existing = cognitionStore.get(cognition.id);
    if (!existing) continue;
    const targetSources = cognitionStore
      .sourcesOf(cognition.id)
      .map((link) => `${link.evidenceId}\u0000${link.relation}`);
    if (
      !sameCognition(existing, cognition) ||
      !sameStringMultiset(targetSources, cognitionEvidenceLinks.get(cognition.id) ?? [])
    ) {
      plan.errors.push(
        lang === 'zh'
          ? `cognition ${cognition.id} 与目标库同 id 认知的内容或溯源关系不一致，拒绝导入`
          : `cognition ${cognition.id} collides with different target content or provenance links; import rejected`,
      );
    }
  }
  if (plan.errors.length > 0) {
    plan.valid = false;
    return plan;
  }

  // ── 判重（evidence：按 id；额外防 originId 唯一约束撞车）──
  // unresolvedEvidence：包里这条 evidence 的 originId 撞了库中【另一条不同 id】的记录，
  //   无法按原 id 落库，其 id 在目标库不存在 → 引用它的 join 行会悬空，必须一并丢弃。
  const unresolvedEvidence = new Set<string>();
  const newEvidence = data.evidence.filter((e) => {
    // 删除是单调的：旧备份不能让已明确删除的同 id 证据复活。get() 故意看不见墓碑，
    // 因而恢复路径必须显式辨认它；关联到该 id 的新 join / 解析也一并跳过。
    if (isEvidenceTombstoned(evidenceStore, e.id)) {
      plan.duplicates.evidence++;
      unresolvedEvidence.add(e.id);
      plan.warnings.push(
        lang === 'zh'
          ? `evidence ${e.id} 在目标库已被删除（墓碑），跳过旧备份以保持删除单调性`
          : `evidence ${e.id} is tombstoned in the target database; skipping the older backup to preserve deletion monotonicity`,
      );
      return false;
    }
    if (evidenceStore.get(e.id)) {
      plan.duplicates.evidence++; // 同 id 已在 → 跳过（join 仍指向它，安全）
      return false;
    }
    if (e.originId != null && evidenceStore.findByOrigin(e.originId)) {
      plan.duplicates.evidence++;
      unresolvedEvidence.add(e.id);
      plan.warnings.push(
        lang === 'zh'
          ? `evidence ${e.id} 的 originId 已被库中另一条占用，跳过（其溯源引用一并丢弃）`
          : `evidence ${e.id} originId is already taken by another record in the database; skipping (its provenance links are dropped too)`,
      );
      return false;
    }
    return true;
  });

  const candidateEvents = data.events.filter((ev) => {
    if (eventStore.get(ev.id)) {
      plan.duplicates.events++;
      return false;
    }
    return true;
  });
  const candidateCognitions = data.cognitions.filter((c) => {
    if (cognitionStore.get(c.id)) {
      plan.duplicates.cognitions++;
      return false;
    }
    return true;
  });

  // 删除单调性同样适用于派生层：事件摘要 / 认知内容都由 evidence 派生，不能在其来源
  // 被墓碑或 originId 冲突挡住后借旧 bundle 复活。任一来源未解析即整实体 fail-closed。
  const newEvents = candidateEvents.filter((event) => {
    const unresolved = (eventEvidenceLinks.get(event.id) ?? []).filter((id) =>
      unresolvedEvidence.has(id),
    );
    if (unresolved.length === 0) return true;
    plan.warnings.push(
      lang === 'zh'
        ? `event ${event.id} 依赖未恢复的 evidence ${unresolved.join(', ')}，跳过以防摘要复活`
        : `event ${event.id} depends on unresolved evidence ${unresolved.join(', ')}; skipping to prevent derived summary revival`,
    );
    return false;
  });
  const cognitionEvidenceIds = new Map<string, string[]>();
  for (const link of data.cognitionEvidence) {
    const links = cognitionEvidenceIds.get(link.cognitionId) ?? [];
    links.push(link.evidenceId);
    cognitionEvidenceIds.set(link.cognitionId, links);
  }
  const newCognitions = candidateCognitions.filter((cognition) => {
    const unresolved = (cognitionEvidenceIds.get(cognition.id) ?? []).filter((id) =>
      unresolvedEvidence.has(id),
    );
    if (unresolved.length === 0) return true;
    plan.warnings.push(
      lang === 'zh'
        ? `cognition ${cognition.id} 依赖未恢复的 evidence ${unresolved.join(', ')}，跳过以防派生内容复活`
        : `cognition ${cognition.id} depends on unresolved evidence ${unresolved.join(', ')}; skipping to prevent derived content revival`,
    );
    return false;
  });

  // 收集将新建 event 的覆盖证据。实体筛选已 fail-closed，因此这里不会给残缺事件写 join。
  const newEventIds = new Set(newEvents.map((e) => e.id));
  const eventEvidenceOf = new Map<string, string[]>();
  let eventEvidenceCount = 0;
  for (const link of data.eventEvidence) {
    if (!newEventIds.has(link.eventId)) continue; // 该 event 已存在（其 join 已在库）或不在新建集
    if (unresolvedEvidence.has(link.evidenceId)) continue; // 悬空 → 丢
    const list = eventEvidenceOf.get(link.eventId) ?? [];
    list.push(link.evidenceId);
    eventEvidenceOf.set(link.eventId, list);
    eventEvidenceCount++;
  }

  // 收集将新建 cognition 的溯源链（同理丢弃悬空）。
  const newCognitionIds = new Set(newCognitions.map((c) => c.id));
  const cognitionSourcesOf = new Map<string, EvidenceLink[]>();
  let cognitionEvidenceCount = 0;
  for (const link of data.cognitionEvidence) {
    if (!newCognitionIds.has(link.cognitionId)) continue;
    if (unresolvedEvidence.has(link.evidenceId)) continue;
    const list = cognitionSourcesOf.get(link.cognitionId) ?? [];
    list.push({ evidenceId: link.evidenceId, relation: link.relation });
    cognitionSourcesOf.set(link.cognitionId, list);
    cognitionEvidenceCount++;
  }

  // 悬空 correctsEvidenceId 置空：指向的证据在目标库既非已有、也不在本次新建集 → 落库前置空，绝不写出悬空纠正指针。
  const newEvidenceIds = new Set(newEvidence.map((e) => e.id));
  const evidenceToInsert = newEvidence.map((e) => {
    const cid = e.correctsEvidenceId;
    if (cid != null && !evidenceStore.get(cid) && !newEvidenceIds.has(cid)) {
      plan.warnings.push(
        lang === 'zh'
          ? `evidence ${e.id} 的 correctsEvidenceId(${cid}) 在目标库无法解析，导入时置空`
          : `evidence ${e.id} correctsEvidenceId(${cid}) cannot be resolved in the target database; cleared on import`,
      );
      return { ...e, correctsEvidenceId: null };
    }
    return e;
  });

  // 交互层（v0.6）：按 id 判重（跳过已存在的）；向后兼容 v1 包（无这两段 → 空数组）。
  const newInteractionContexts = (data.interactionContexts ?? []).filter(
    (c) => !interactionContextStore.get(c.id),
  );
  // semantic_resolution 的读取契约是一证据一解析（ofEvidence 稳定返回最早一条）。
  // v2 文件若有重复由 validateBundle 拒绝；目标库已有解析时保持既有结果，不再插第二条。
  const newSemanticResolutions = (data.semanticResolutions ?? []).filter((r) => {
    if (unresolvedEvidence.has(r.evidenceId)) {
      plan.warnings.push(
        lang === 'zh'
          ? `semanticResolution ${r.id} 指向未恢复的 evidence ${r.evidenceId}，跳过`
          : `semanticResolution ${r.id} references unresolved evidence ${r.evidenceId}; skipping`,
      );
      return false;
    }
    if (semanticResolutionStore.get(r.id)) return false;
    if (semanticResolutionStore.ofEvidence(r.evidenceId)) {
      plan.warnings.push(
        lang === 'zh'
          ? `evidence ${r.evidenceId} 在目标库已有 semanticResolution，跳过 ${r.id} 以保持一证据一解析`
          : `evidence ${r.evidenceId} already has a semanticResolution in the target database; skipping ${r.id} to preserve one resolution per evidence`,
      );
      return false;
    }
    return true;
  });

  plan.counts = {
    evidence: newEvidence.length,
    events: newEvents.length,
    cognitions: newCognitions.length,
    eventEvidence: eventEvidenceCount,
    cognitionEvidence: cognitionEvidenceCount,
    interactionContexts: newInteractionContexts.length,
    semanticResolutions: newSemanticResolutions.length,
  };

  if (opts.mode === 'dryRun') return plan; // 只算不写

  // ── merge：实际写入 ──
  // 顺序：先 evidence，再 event（挂证据），最后 cognition（挂溯源）——被引方先落库。
  // consolidated 按源包保真：不在 unconsolidatedEventIds 里的事件标已消化；在里面的还原为未消化（防漏消化）。
  const write = () => {
    for (const e of evidenceToInsert) evidenceStore.insert(e);
    for (const ev of newEvents)
      eventStore.insert(ev, eventEvidenceOf.get(ev.id) ?? [], {
        consolidated: !unconsolidatedSet.has(ev.id),
      });
    for (const c of newCognitions) cognitionStore.insert(c, cognitionSourcesOf.get(c.id) ?? []);
    // 交互层（v0.6）：独立表，无溯源 join；按原 id 原样落库。interaction_context 含 AI 文本但仍是独立记录、
    //   永不进 consolidate 白名单（结构墙）；semantic_resolution 通过 evidence_id 关联（弱引用，无外键）。
    for (const c of newInteractionContexts) interactionContextStore.insert(c);
    for (const r of newSemanticResolutions) semanticResolutionStore.insert(r);
  };
  // 事务优先（openStores 提供）：中途抛错整体回滚，库不留残。无事务无法回滚——把异常收进 plan.errors 并提示，
  // 将写入错误转换为 ImportPlan 警告，以保持结构化返回契约；常见的重复 id 已由 validateBundle 提前拦截。
  try {
    if (deps.transaction) deps.transaction(write);
    else write();
  } catch (e) {
    plan.valid = false;
    plan.errors.push(
      lang === 'zh'
        ? `导入写入失败：${e instanceof Error ? e.message : String(e)}`
        : `Import write failed: ${e instanceof Error ? e.message : String(e)}`,
    );
    if (!deps.transaction) {
      plan.warnings.push(
        lang === 'zh'
          ? '未提供 transaction，写入中途失败可能已残留部分数据（建议用 openStores 的 transaction）'
          : 'No transaction provided; a mid-write failure may have left partial data (use the transaction from openStores)',
      );
    }
    plan.counts = {
      evidence: 0,
      events: 0,
      cognitions: 0,
      eventEvidence: 0,
      cognitionEvidence: 0,
      interactionContexts: 0,
      semanticResolutions: 0,
    };
    return plan;
  }

  return plan;
}
