/**
 * 导入便携记忆包（Phase 5-A）。
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
import type { Transaction } from '../store/transaction.ts';
import type { EvidenceLink } from '../cognition/model.ts';
import { validateBundle } from './validateBundle.ts';
import type { ImportMode, ImportPlan, MemoryBundle } from './model.ts';

export interface ImportDeps {
  evidenceStore: EvidenceStore;
  eventStore: EventStore;
  cognitionStore: CognitionStore;
  /** 可选事务器：merge 的写入包进一个事务，中途失败整体回滚，避免污染库。 */
  transaction?: Transaction;
}

export interface ImportOptions {
  mode: ImportMode;
}

export function importBundle(bundle: MemoryBundle, deps: ImportDeps, opts: ImportOptions): ImportPlan {
  const { evidenceStore, eventStore, cognitionStore } = deps;

  const validation = validateBundle(bundle);
  const plan: ImportPlan = {
    mode: opts.mode,
    valid: validation.valid,
    errors: [...validation.errors],
    warnings: [...validation.warnings],
    counts: { evidence: 0, events: 0, cognitions: 0, eventEvidence: 0, cognitionEvidence: 0 },
    duplicates: { evidence: 0, events: 0, cognitions: 0 },
  };
  if (!validation.valid) return plan; // 结构/引用错 → 绝不写库

  const data = bundle.data;

  // ── 判重（evidence：按 id；额外防 originId 唯一约束撞车）──
  // unresolvedEvidence：包里这条 evidence 的 originId 撞了库中【另一条不同 id】的记录，
  //   无法按原 id 落库，其 id 在目标库不存在 → 引用它的 join 行会悬空，必须一并丢弃。
  const unresolvedEvidence = new Set<string>();
  const newEvidence = data.evidence.filter((e) => {
    if (evidenceStore.get(e.id)) {
      plan.duplicates.evidence++; // 同 id 已在 → 跳过（join 仍指向它，安全）
      return false;
    }
    if (e.originId != null && evidenceStore.findByOrigin(e.originId)) {
      plan.duplicates.evidence++;
      unresolvedEvidence.add(e.id);
      plan.warnings.push(`evidence ${e.id} 的 originId 已被库中另一条占用，跳过（其溯源引用一并丢弃）`);
      return false;
    }
    return true;
  });

  const newEvents = data.events.filter((ev) => {
    if (eventStore.get(ev.id)) {
      plan.duplicates.events++;
      return false;
    }
    return true;
  });
  const newCognitions = data.cognitions.filter((c) => {
    if (cognitionStore.get(c.id)) {
      plan.duplicates.cognitions++;
      return false;
    }
    return true;
  });

  // 收集将新建 event 的覆盖证据（丢弃指向悬空 evidence 的链）。
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
      plan.warnings.push(`evidence ${e.id} 的 correctsEvidenceId(${cid}) 在目标库无法解析，导入时置空`);
      return { ...e, correctsEvidenceId: null };
    }
    return e;
  });

  plan.counts = {
    evidence: newEvidence.length,
    events: newEvents.length,
    cognitions: newCognitions.length,
    eventEvidence: eventEvidenceCount,
    cognitionEvidence: cognitionEvidenceCount,
  };

  if (opts.mode === 'dryRun') return plan; // 只算不写

  // ── merge：实际写入 ──
  // 顺序：先 evidence，再 event（挂证据），最后 cognition（挂溯源）——被引方先落库。
  // consolidated 按源包保真：不在 unconsolidatedEventIds 里的事件标已消化；在里面的还原为未消化（防漏消化）。
  const unconsolidatedSet = new Set(bundle.data.unconsolidatedEventIds ?? []);
  const write = () => {
    for (const e of evidenceToInsert) evidenceStore.insert(e);
    for (const ev of newEvents) eventStore.insert(ev, eventEvidenceOf.get(ev.id) ?? [], { consolidated: !unconsolidatedSet.has(ev.id) });
    for (const c of newCognitions) cognitionStore.insert(c, cognitionSourcesOf.get(c.id) ?? []);
  };
  // 事务优先（openStores 提供）：中途抛错整体回滚，库不留残。无事务无法回滚——把异常收进 plan.errors 并提示，
  // 不把裸异常抛给调用方（保住"返回 ImportPlan"的契约）。包内重复 id 等最常见抛错源已由 validateBundle 提前挡下。
  try {
    if (deps.transaction) deps.transaction(write);
    else write();
  } catch (e) {
    plan.valid = false;
    plan.errors.push(`导入写入失败：${e instanceof Error ? e.message : String(e)}`);
    if (!deps.transaction) plan.warnings.push('未提供 transaction，写入中途失败可能已残留部分数据（建议用 openStores 的 transaction）');
    plan.counts = { evidence: 0, events: 0, cognitions: 0, eventEvidence: 0, cognitionEvidence: 0 };
    return plan;
  }

  return plan;
}
