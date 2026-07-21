/**
 * 受控记忆管理 API。
 *
 * 记忆管理不是普通 CRUD：每个操作负责引用完整性 + 原因留痕（审计表 management_log）。
 * Host 的记忆管理页应调这里，不再直接摸 Sqlite*Store。
 *
 * 审计口径：只给【真实发生的变更】落审计行——被拒绝的操作（如有引用未 force、目标不存在）
 * 什么都没改，不落行（审计表回答"我的记忆被怎么了"，不是操作尝试日志）。
 * checkIntegrity 只读不改，因此不落审计，也不需要 reason 参数。
 */
import { config, resolveLang, type MemoWeftConfig } from '../config.ts';
import type { StoreBundle } from '../store/openStores.ts';
import type { Cognition, EvidenceLink, EvidenceRelation } from '../cognition/model.ts';
import type { Evidence } from '../evidence/model.ts';
import type { EventWithEvidence } from '../event/model.ts';
import type { Retriever } from '../retrieval/retriever.ts';
import {
  computeConfidence,
  deriveCredStatus,
  isHedgedStated,
} from '../consolidation/confidence.ts';
import { effectiveConfidence } from '../background/decay.ts';
import { systemClock, type Clock } from '../clock.ts';

// ── 输入 / 结果类型 ──

export interface InvalidateCognitionInput {
  cognitionId: string;
  /** 操作原因（必填，进审计行）：如 'user_rejected'、'过时了'。 */
  reason: string;
}

export interface UpdateEvidenceAuthorizationInput {
  evidenceId: string;
  allowCloudRead?: boolean;
  allowInference?: boolean;
  /** 操作原因（必填，进审计行）——授权位是隐私敏感操作，与其余管理操作同一口径。 */
  reason: string;
}

export interface RemoveEvidenceSafelyInput {
  evidenceId: string;
  reason: string;
  /** true = 即便有事件/认知引用也删（清关联链 + blockers 快照进审计 detail）；缺省 false = 有引用则拒绝。 */
  force?: boolean;
}

/** 拦下删除（或被 force 清掉）的一条引用。 */
export interface RemovalBlocker {
  /** 引用来自哪条链：event（event_evidence）或 cognition（cognition_evidence）。 */
  kind: 'event' | 'cognition';
  /** 引用方 id（事件 id 或认知 id）。 */
  id: string;
  /** 认知链上的关系（support/contradict）；事件链无此字段。 */
  relation?: string;
}

export interface RemoveEvidenceResult {
  removed: boolean;
  /** 影响面：引用这条证据的事件/认知。拒绝时 = 拦下原因；force 删除时 = 已被清链的快照。
   *  removed=false 且 blockers 为空 = 证据不存在（拒绝只发生在有引用时）。 */
  blockers: RemovalBlocker[];
}

export interface RemoveCognitionSafelyInput {
  cognitionId: string;
  reason: string;
}

export interface RemoveCognitionResult {
  removed: boolean;
  /** 影响面：随认知一并删除的溯源链（证据本体不动，只断链）。 */
  removedLinks: EvidenceLink[];
}

export interface MergeCognitionInput {
  /** 被合并方：链搬走后标 invalidAt（不硬删，保留可追溯）。 */
  sourceId: string;
  /** 合并落点：吸收 source 的溯源链并重算置信度；content 不动。 */
  targetId: string;
  reason: string;
}

export interface MergeCognitionResult {
  merged: boolean;
  /** 真正搬到 target 的链数（去重后）。 */
  movedLinks: number;
  /** 因 (evidenceId, relation) 已在 target 链上而去重丢弃的条数。 */
  duplicateLinks: number;
  /** 重算置信度后的 target。 */
  target: Cognition;
  /** 已标失效的 source。 */
  source: Cognition;
}

export interface ReinforceCognitionInput {
  /** 要补证据的认知。已失效/已归档的拒绝（它们的置信度是历史快照）。 */
  cognitionId: string;
  /** 已落库的证据 id（须同 subject）。这里【不】摄入新证据——证据的入口是 ingest*，
   *  管理面只负责把已有证据挂到认知上，免得开出第二条绕过 perceive 的证据写入路径。 */
  evidenceId: string;
  /** 缺省 'support'。确认式 UI 的「不对」要能落成反证，故也允许 'contradict'。 */
  relation?: EvidenceRelation;
  reason: string;
}

export interface ReinforceCognitionResult {
  /** 真的改动了（加了链并重算）。幂等命中时为 false。 */
  reinforced: boolean;
  /** 该 (evidenceId, relation) 已在链上 → 幂等：没加链、没重算、没落审计。 */
  duplicate: boolean;
  /** 重算后的认知；幂等命中时是原样。 */
  cognition: Cognition;
}

export interface ArchiveCognitionInput {
  cognitionId: string;
  reason: string;
}

export interface MuteCognitionInput {
  cognitionId: string;
  /** true = 静音（mutedAt=now，召回跳过）；false = 取消静音（mutedAt=null，恢复召回）。 */
  muted: boolean;
  reason: string;
}

/** 完整性问题一条：某张溯源链的 join 行指向了不存在的行。 */
export interface IntegrityIssue {
  kind: 'orphan_event_evidence' | 'orphan_cognition_evidence';
  eventId?: string;
  cognitionId?: string;
  evidenceId: string;
  /** 哪一端悬空（指向的行不存在）。 */
  missing: 'event' | 'cognition' | 'evidence';
}

export interface IntegrityReport {
  ok: boolean;
  issues: IntegrityIssue[];
  checkedAt: string;
}

// ── 只读列取输入 / 返回类型 ──

/** 列取入参：只按 subjectId 过滤；缺省取 cfg.identity.subjectId（v1 单人单宿主）。 */
export interface ListMemoryInput {
  subjectId?: string;
}

/** 一条认知 + 溯源链 + 读时算的有效置信（记忆管理页/友好版抽屉展示用）。 */
export interface CognitionWithMeta extends Cognition {
  /** 这条认知靠哪些证据支持 / 反对。 */
  sources: EvidenceLink[];
  /** 有效置信 = confidence × 衰减因子（读时算，不持久化；见 background/decay.ts）。 */
  effectiveConfidence: number;
}

// ── 恢复出厂输入 / 返回类型 ──

export interface ResetSubjectInput {
  subjectId?: string;
  /** 操作原因；本方法整表清审计（连自己这行也不留，见实现注释），reason 仅备语义、不落库。 */
  reason?: string;
}

export interface ResetSubjectResult {
  evidenceRemoved: number;
  eventRemoved: number;
  cognitionRemoved: number;
  auditRemoved: number;
}

/** 受控记忆管理 API（core.memory）。8 个操作 + 独立审计表。 */
export interface MemoryManagementAPI {
  /** 标失效（invalidAt=now）+ 审计。召回本就跳过 invalid，无需额外动索引。不存在返回 null（不审计）。 */
  invalidateCognition(input: InvalidateCognitionInput): Cognition | null;
  /** 改证据授权位（allowCloudRead / allowInference）+ 审计（detail 记 before/after）。不存在返回 null；
   *  零变更（两位都没动）原样返回、不落审计——审计只记真实发生的变更。 */
  updateEvidenceAuthorization(input: UpdateEvidenceAuthorizationInput): Evidence | null;
  /** 安全删证据：先查 event_evidence / cognition_evidence 引用；有引用且未 force → 拒绝并返回影响面；
   *  force → 事务内删证据 + 清关联链 + 审计（blockers 快照进 detail）。 */
  removeEvidenceSafely(input: RemoveEvidenceSafelyInput): RemoveEvidenceResult;
  /** 删认知（连溯源链，包现有 remove）+ 审计；返回影响面（被断掉的链）。
   *  审计 detail 只存元数据 {contentType, formedBy, credStatus, linkCount}、【不存内容原文】
   *  （删除后不在审计记录中保留被删内容）。 */
  removeCognitionSafely(input: RemoveCognitionSafelyInput): RemoveCognitionResult;
  /** 给指定认知补一条已有证据并【当场】按新链重算置信度 + 重导出 credStatus（确认式 UI 的落点）。
   *  同 (evidenceId, relation) 已在链上 → 幂等：不加链、不重算、不落审计
   *  （否则连点两下会把把握度越推越高）。认知/证据不存在、跨 subject、认知已失效或已归档 → 抛错。 */
  reinforceCognition(input: ReinforceCognitionInput): ReinforceCognitionResult;
  /** 合并认知：仅同 subjectId；source 的链搬到 target（按 evidenceId+relation 去重）、
   *  target 置信度按合并后链重算（computeConfidence 同 consolidate 强化路径）、source 标失效不硬删。
   *  source/target 不存在、跨 subject、target 已失效/已归档 → 抛错（什么都不改）。 */
  mergeCognition(input: MergeCognitionInput): MergeCognitionResult;
  /** 归档（archivedAt=now）+ 审计。召回跳过 archived（共享召回函数门控）；数据保留、可经 update 恢复。 */
  archiveCognition(input: ArchiveCognitionInput): Cognition | null;
  /** 召回负反馈：静音/取消静音 + 审计。muted:true → mutedAt=now（召回跳过，但认知仍 active、仍参与 consolidation/画像演化，
   *  区别于 archive 从全部活动路径排除）；muted:false → mutedAt=null（恢复召回）。静音只改变召回资格，不改变 confidence。不存在返回 null。 */
  muteCognition(input: MuteCognitionInput): Cognition | null;
  /** 完整性检查 v1：只报告不修——孤儿 event_evidence / cognition_evidence（指向不存在的行）。 */
  checkIntegrity(): IntegrityReport;

  // ── 只读列取：只读不写、不落审计——展示用，不是管理变更 ──
  /** 列某 subject 的全部证据（不含删改）。缺省 subjectId = cfg.identity.subjectId。 */
  listEvidence(input?: ListMemoryInput): Evidence[];
  /** 列某 subject 的全部认知，每条配溯源链 + 读时算的有效置信。缺省 subjectId = cfg.identity.subjectId。 */
  listCognitions(input?: ListMemoryInput): CognitionWithMeta[];
  /** 列某 subject 的全部事件，每条配它覆盖的证据 id 列表。缺省 subjectId = cfg.identity.subjectId。 */
  listEvents(input?: ListMemoryInput): EventWithEvidence[];

  /** 恢复出厂（破坏性·收口）：清某 subject 的三层数据 + 清审计表 + 清向量索引。
   *  库内三层 + 审计包在一个事务里（全成或全滚）；向量索引 indexAll([]) 是外部异步索引、在事务外，
   *  所以"原子"仅指库内四张表。有 retriever 才清索引，无则跳过。
   *  审计不为本次操作留行——整表都清了，留了也被 clear() 冲掉（恢复出厂后无历史）。 */
  resetSubject(input: ResetSubjectInput): ResetSubjectResult;
}

/** 装配受控管理 API 的可选依赖。 */
export interface MemoryManagementDeps {
  /** 召回器：resetSubject 清向量索引要它（indexAll([])）；不传则出厂时跳过清索引（见 resetSubject 注释）。 */
  retriever?: Retriever;
  /** 可注入时钟：失效/归档/自检/读时衰减的"现在"走它；缺省真实系统时间。 */
  clock?: Clock;
}

/**
 * 从 StoreBundle 装配受控管理 API（引用检查/清链要直查关联表，故吃整个 bundle 而非单个 store）。
 * @param cfg 可注入配置；省略时使用全局单例，merge 与缺省 subject 均按该配置执行。
 * @param deps 可选依赖：retriever 供 resetSubject 清向量索引；不传则出厂跳过清索引。
 */
export function createMemoryManagementAPI(
  bundle: StoreBundle,
  cfg: MemoWeftConfig = config,
  deps: MemoryManagementDeps = {},
): MemoryManagementAPI {
  const {
    db,
    evidenceStore,
    eventStore,
    cognitionStore,
    interactionContextStore,
    semanticResolutionStore,
    managementLog,
    transaction,
  } = bundle;
  const retriever = deps.retriever;
  const clock = deps.clock ?? systemClock; // 可注入时钟：失效/归档/自检/读时衰减的"现在"
  const subjectOf = (explicit?: string) => explicit ?? cfg.identity.subjectId;

  /** 查某证据被谁引用（事件链 + 认知链）。 */
  function blockersOf(evidenceId: string): RemovalBlocker[] {
    const out: RemovalBlocker[] = [];
    const evRows = db
      .prepare('SELECT event_id FROM event_evidence WHERE evidence_id = ?')
      .all(evidenceId) as unknown as Array<{ event_id: string }>;
    for (const r of evRows) out.push({ kind: 'event', id: r.event_id });
    const cogRows = db
      .prepare('SELECT cognition_id, relation FROM cognition_evidence WHERE evidence_id = ?')
      .all(evidenceId) as unknown as Array<{ cognition_id: string; relation: string }>;
    for (const r of cogRows)
      out.push({ kind: 'cognition', id: r.cognition_id, relation: r.relation });
    return out;
  }

  /**
   * 重算期的 hedged 判据（含糊自述封顶；与 consolidate 的 `resolveHedged` 同一语义）。
   *
   * **为什么这三个重算点都得算**：hedged **不落库**，每次都从支持证据链重新派生。一条含糊自述
   * 封顶在 280，只要这里漏算，用户在记忆管理页删掉一条无关证据 / 在确认式 UI 上点一下「对」/
   * 合并两条认知，把握度就静默反弹回 600+——一个肯定动作抹掉了「这话说得含糊」这个事实。
   *
   * **为什么不与 consolidate 共用一份实现**：那边的重算点执行时，本轮 LLM 产的解析还没落库
   * （落库循环在写循环之后），必须做「库优先 + 内存兜底」的混合读；这里的证据全是【既有落库证据】，
   * 解析早在表里，纯查表即可。另外这里的 `semanticResolutionStore` 来自 StoreBundle（**非可选**），
   * 不存在 consolidate 那边的缺席退化——两侧能力本就不对等，硬合并只会把弱侧的退化传染给强侧。
   *
   * ⚠ 不变式：传进来的 `links` 必须与算 `supportCount` 用的是同一份链快照（改完链之后取的那一份）。
   */
  function hedgedOf(cog: Cognition, links: readonly EvidenceLink[]): boolean {
    // 非 stated 恒 false（`isHedgedStated` 同判）——提前挡掉，省一次查表。
    if (cog.formedBy !== 'stated') return false;
    const supportIds = links.filter((l) => l.relation === 'support').map((l) => l.evidenceId);
    if (supportIds.length === 0) return false;
    // 一次批量查（走 ix_semres_evidence），不逐条 ofEvidence；查不到的按「解析不出不臆造惩罚」当 null。
    const byId = new Map(
      semanticResolutionStore.forEvidenceIds(supportIds).map((r) => [r.evidenceId, r] as const),
    );
    return isHedgedStated(
      cog.formedBy,
      supportIds.map((id) => byId.get(id) ?? null),
    );
  }

  return {
    invalidateCognition({ cognitionId, reason }) {
      return transaction(() => {
        const updated = cognitionStore.update(cognitionId, { invalidAt: clock().toISOString() });
        if (!updated) return null;
        managementLog.append({
          op: 'invalidate',
          targetKind: 'cognition',
          targetId: cognitionId,
          reason,
          detail: null,
        });
        return updated;
      });
    },

    updateEvidenceAuthorization({ evidenceId, allowCloudRead, allowInference, reason }) {
      return transaction(() => {
        const before = evidenceStore.get(evidenceId);
        if (!before) return null;
        // 零变更（没传位、或传的值与现值相同）→ 原样返回、不写库不审计（口径：只记真实发生的变更）。
        const nextCloud = allowCloudRead ?? before.allowCloudRead;
        const nextInfer = allowInference ?? before.allowInference;
        if (nextCloud === before.allowCloudRead && nextInfer === before.allowInference)
          return before;
        const updated = evidenceStore.update(evidenceId, { allowCloudRead, allowInference });
        managementLog.append({
          op: 'update_authorization',
          targetKind: 'evidence',
          targetId: evidenceId,
          reason,
          // before/after 快照：授权位变更是隐私敏感操作，改前改后都留痕。
          detail: {
            before: {
              allowCloudRead: before.allowCloudRead,
              allowInference: before.allowInference,
            },
            after: {
              allowCloudRead: updated!.allowCloudRead,
              allowInference: updated!.allowInference,
            },
          },
        });
        return updated;
      });
    },

    removeEvidenceSafely({ evidenceId, reason, force }) {
      return transaction(() => {
        if (!evidenceStore.get(evidenceId)) return { removed: false, blockers: [] }; // 不存在（拒绝只发生在有引用时）
        const blockers = blockersOf(evidenceId);
        if (blockers.length > 0 && !force) return { removed: false, blockers }; // 拒绝：影响面摆给调用方，没改就不审计
        // force（或无引用）：清关联链 → 删证据 → 审计，同一事务全成或全滚。
        db.prepare('DELETE FROM event_evidence WHERE evidence_id = ?').run(evidenceId);
        db.prepare('DELETE FROM cognition_evidence WHERE evidence_id = ?').run(evidenceId);
        // 这条证据的语义解析（用户原话的解开改写）跟着删——必须在删证据之前，删完就找不着关联了。
        //   漏掉它等于：用户点「删除这条」，界面说删了，而那句话的改写版永久留库、再无入口可删。
        semanticResolutionStore.removeByEvidenceIds([evidenceId]);
        evidenceStore.remove(evidenceId);
        // 受影响认知按【剩余】链重算——与 mergeCognition 同口径（那里的注释写着「不留旧值撒谎」）。
        //   不重算的话：一条靠 5 条证据攒到 stable 的认知，用户删掉其中 4 条后仍然是 stable，
        //   系统对他的把握度停留在已经被撤回的证据上。credStatus 随之重导出，不留旧值。
        //   blockers 按 (认知, relation) 逐条列出，同一认知可能出现多次 → 去重后各算一次。
        for (const cogId of new Set(
          blockers.filter((b) => b.kind === 'cognition').map((b) => b.id),
        )) {
          const cog = cognitionStore.get(cogId);
          if (!cog || cog.invalidAt) continue; // 已失效的不动：它的置信度是历史快照，重算反而抹掉当时的判断
          const links = cognitionStore.sourcesOf(cogId);
          const supportCount = links.filter((l) => l.relation === 'support').length;
          const contradictCount = links.filter((l) => l.relation === 'contradict').length;
          // 接线点 6/8 · removeEvidenceSafely。hedged 按【剩余】链重派生：与置信度重算同口径。
          //   **必须接**：用户只是删掉一条无关证据，若 hedged 蒸发，这条含糊自述会从 280
          //   反弹回 600——删证据反而让系统更笃定。
          //   顺序依赖（勿调换）：上面的 `semanticResolutionStore.removeByEvidenceIds` 在本循环【之前】
          //   执行，所以被删证据的解析已同步消失，此处回查看到的正是删后视图。
          let confidence = computeConfidence(
            {
              contentType: cog.contentType,
              formedBy: cog.formedBy,
              supportCount,
              contradictCount,
              hedged: hedgedOf(cog, links),
            },
            cfg,
          );
          if (cog.contentType === 'hypothesis')
            confidence = Math.min(confidence, cfg.attribution.hypothesisCap); // 同 merge：假设类不因重算被抬成结论
          cognitionStore.update(cogId, {
            confidence,
            credStatus: deriveCredStatus(
              confidence,
              contradictCount,
              cog.contentType,
              cfg,
              supportCount,
            ),
          });
        }
        managementLog.append({
          op: 'remove_evidence',
          targetKind: 'evidence',
          targetId: evidenceId,
          reason,
          detail: { force: !!force, blockers }, // blockers 快照：删的时候都断了谁的链，事后可追
        });
        return { removed: true, blockers };
      });
    },

    reinforceCognition({ cognitionId, evidenceId, relation = 'support', reason }) {
      // 这是【第五条】会改动溯源链的路径（前四条：consolidate 的 reinforce/conflict、
      //   mergeCognition、removeEvidenceSafely）。前面两条曾经只改状态不重算，
      //   contradictPenalty 因此长期不生效——所以这条从第一天就把重算写死在同一个事务里。
      const lang = resolveLang(cfg);
      const cog = cognitionStore.get(cognitionId);
      if (!cog) {
        throw new Error(
          lang === 'zh'
            ? `补证据失败：认知不存在（${cognitionId}）`
            : `Reinforce failed: cognition not found (${cognitionId})`,
        );
      }
      if (cog.invalidAt) {
        throw new Error(
          lang === 'zh'
            ? `补证据失败：认知已失效（${cognitionId}）——失效认知的置信度是历史快照，重算会抹掉当时的判断`
            : `Reinforce failed: cognition is invalidated (${cognitionId}); its confidence is a historical snapshot`,
        );
      }
      if (cog.archivedAt) {
        throw new Error(
          lang === 'zh'
            ? `补证据失败：认知已归档（${cognitionId}），先恢复再补`
            : `Reinforce failed: cognition is archived (${cognitionId}); restore it first`,
        );
      }
      const ev = evidenceStore.get(evidenceId);
      if (!ev) {
        throw new Error(
          lang === 'zh'
            ? `补证据失败：证据不存在（${evidenceId}）`
            : `Reinforce failed: evidence not found (${evidenceId})`,
        );
      }
      if (ev.subjectId !== cog.subjectId) {
        throw new Error(
          lang === 'zh'
            ? `补证据失败：证据与认知不属于同一 subject（${ev.subjectId} ≠ ${cog.subjectId}）`
            : `Reinforce failed: evidence and cognition belong to different subjects (${ev.subjectId} != ${cog.subjectId})`,
        );
      }

      return transaction(() => {
        // 幂等：同 (证据, 关系) 已在链上就原样返回。不重算是刻意的——重算本身幂等，
        //   但"点两下把握度涨两次"才是确认式 UI 最容易出的 bug，这里从源头挡掉。
        if (
          cognitionStore
            .sourcesOf(cognitionId)
            .some((l) => l.evidenceId === evidenceId && l.relation === relation)
        ) {
          return { reinforced: false, duplicate: true, cognition: cog };
        }
        cognitionStore.addEvidence(cognitionId, [{ evidenceId, relation }]);

        // 按新链重算——与 consolidate 强化路径、mergeCognition、removeEvidenceSafely 同一口径。
        const links = cognitionStore.sourcesOf(cognitionId);
        const supportCount = links.filter((l) => l.relation === 'support').length;
        const contradictCount = links.filter((l) => l.relation === 'contradict').length;
        // 接线点 7/8 · reinforceCognition。**必须接**：本路径是确认式 UI 的主入口——
        //   用户点一下「对」补一条证据，若 hedged 蒸发，认知立刻从 280 涨到 640。
        //   「他点头确认了」不等于「他当初说得不含糊」：补证据只该加支持分，
        //   不该把含糊自述的封顶一起取消。
        //   links 取自 addEvidence 之后（含本次新挂的那条），与 supportCount 同一份快照。
        let confidence = computeConfidence(
          {
            contentType: cog.contentType,
            formedBy: cog.formedBy,
            supportCount,
            contradictCount,
            hedged: hedgedOf(cog, links),
          },
          cfg,
        );
        if (cog.contentType === 'hypothesis')
          confidence = Math.min(confidence, cfg.attribution.hypothesisCap); // 同 merge：补证据不能把推断抬成结论
        const updated = cognitionStore.update(cognitionId, {
          confidence,
          credStatus: deriveCredStatus(
            confidence,
            contradictCount,
            cog.contentType,
            cfg,
            supportCount,
          ),
        })!;
        managementLog.append({
          op: 'reinforce',
          targetKind: 'cognition',
          targetId: cognitionId,
          reason,
          detail: { evidenceId, relation, supportCount, contradictCount, confidence },
        });
        return { reinforced: true, duplicate: false, cognition: updated };
      });
    },

    removeCognitionSafely({ cognitionId, reason }) {
      return transaction(() => {
        const cur = cognitionStore.get(cognitionId);
        if (!cur) return { removed: false, removedLinks: [] };
        const removedLinks = cognitionStore.sourcesOf(cognitionId); // 影响面：将被一并断掉的溯源链
        const removed = cognitionStore.remove(cognitionId); // 现有 remove 连链删
        managementLog.append({
          op: 'remove_cognition',
          targetKind: 'cognition',
          targetId: cognitionId,
          reason,
          // 不存内容原文：删除审计只留元数据——被删的是什么类型/哪来的/什么状态/断了几条链。
          detail: {
            contentType: cur.contentType,
            formedBy: cur.formedBy,
            credStatus: cur.credStatus,
            linkCount: removedLinks.length,
          },
        });
        return { removed, removedLinks };
      });
    },

    mergeCognition({ sourceId, targetId, reason }) {
      const lang = resolveLang(cfg);
      const source = cognitionStore.get(sourceId);
      const target = cognitionStore.get(targetId);
      if (!source || !target) {
        throw new Error(
          lang === 'zh'
            ? `合并失败：认知不存在（source=${sourceId}, target=${targetId}）`
            : `Merge failed: cognition not found (source=${sourceId}, target=${targetId})`,
        );
      }
      if (sourceId === targetId) {
        throw new Error(
          lang === 'zh'
            ? '合并失败：source 与 target 是同一条认知'
            : 'Merge failed: source and target are the same cognition',
        );
      }
      // 跨 subject 拒绝：不同人的认知合并会串线，违反隐私边界。
      if (source.subjectId !== target.subjectId) {
        throw new Error(
          lang === 'zh'
            ? `合并失败：跨 subject 不允许（source=${source.subjectId}, target=${target.subjectId}）`
            : `Merge failed: cross-subject merge not allowed (source=${source.subjectId}, target=${target.subjectId})`,
        );
      }
      // 死目标拒绝：活链搬进已失效或已归档的 target 后会从召回中静默消失。
      if (target.invalidAt) {
        throw new Error(
          lang === 'zh'
            ? `合并失败：target 已失效（${targetId}），先恢复再合并`
            : `Merge failed: target is invalidated (${targetId}); restore it before merging`,
        );
      }
      if (target.archivedAt) {
        throw new Error(
          lang === 'zh'
            ? `合并失败：target 已归档（${targetId}），先恢复再合并`
            : `Merge failed: target is archived (${targetId}); restore it before merging`,
        );
      }
      return transaction(() => {
        // 1) 链搬家：source 的 cognition_evidence 搬到 target，按 (evidenceId, relation) 去重。
        const targetKeys = new Set(
          cognitionStore.sourcesOf(targetId).map((l) => `${l.evidenceId}|${l.relation}`),
        );
        const sourceLinks = cognitionStore.sourcesOf(sourceId);
        const toMove: EvidenceLink[] = [];
        let duplicateLinks = 0;
        for (const l of sourceLinks) {
          const key = `${l.evidenceId}|${l.relation}`;
          if (targetKeys.has(key)) {
            duplicateLinks++;
            continue;
          } // target 已有同款 → 去重丢弃
          targetKeys.add(key); // source 自身链里也可能有重复，一并去重
          toMove.push(l);
        }
        db.prepare('DELETE FROM cognition_evidence WHERE cognition_id = ?').run(sourceId);
        if (toMove.length > 0) cognitionStore.addEvidence(targetId, toMove);

        // 2) target 置信度重算（选了"重算"路：computeConfidence 只需 contentType/formedBy/支持/反对计数，
        //    合并后都齐——与 consolidate 强化路径同款口径；credStatus 随之重导出，不留旧值撒谎）。
        //    假设类受 hypothesisCap 限制，合并不能将非结论性推断提升为确定结论。
        const links = cognitionStore.sourcesOf(targetId);
        const supportCount = links.filter((l) => l.relation === 'support').length;
        const contradictCount = links.filter((l) => l.relation === 'contradict').length;
        // 接线点 8/8 · mergeCognition。hedged 按【合并后的完整 support 集】重新派生
        //   （links 已是搬链之后的视图，与 supportCount 同源）。
        //   **必须接**，且判定天然双向：两条含糊认知合并 → 仍无 explicit → 继续封顶 280；
        //   source 带来一条 explicit 的主动陈述 → 「没有 explicit」不再成立 → 封顶自动解除。
        //   合并只是把证据搬到一起，判据永远重新从证据算，不继承任何一侧的旧结论。
        let confidence = computeConfidence(
          {
            contentType: target.contentType,
            formedBy: target.formedBy,
            supportCount,
            contradictCount,
            hedged: hedgedOf(target, links),
          },
          cfg,
        );
        if (target.contentType === 'hypothesis')
          confidence = Math.min(confidence, cfg.attribution.hypothesisCap);
        const credStatus = deriveCredStatus(
          confidence,
          contradictCount,
          target.contentType,
          cfg,
          supportCount,
        );
        const updatedTarget = cognitionStore.update(targetId, { confidence, credStatus })!; // content 不动

        // 3) source 标失效不硬删（保留可追溯；链已搬走，来龙去脉靠本审计行的 detail）。
        const updatedSource = cognitionStore.update(sourceId, {
          invalidAt: clock().toISOString(),
        })!;

        managementLog.append({
          op: 'merge', // 合并操作的 op 名
          targetKind: 'cognition',
          targetId: sourceId, // 审计挂在被合并方（状态被改变的那条）；detail 里两端都有
          reason,
          detail: {
            sourceId,
            targetId,
            movedLinks: toMove.length,
            duplicateLinks,
            confidenceRecomputed: true,
            confidenceBefore: target.confidence,
            confidenceAfter: confidence,
          },
        });
        return {
          merged: true,
          movedLinks: toMove.length,
          duplicateLinks,
          target: updatedTarget,
          source: updatedSource,
        };
      });
    },

    archiveCognition({ cognitionId, reason }) {
      return transaction(() => {
        const updated = cognitionStore.update(cognitionId, { archivedAt: clock().toISOString() });
        if (!updated) return null;
        managementLog.append({
          op: 'archive',
          targetKind: 'cognition',
          targetId: cognitionId,
          reason,
          detail: null,
        });
        return updated;
      });
    },

    muteCognition({ cognitionId, muted, reason }) {
      return transaction(() => {
        // 仅更新 mutedAt（召回门控），不改变 confidence/credStatus；muted:false 清标恢复召回。
        const updated = cognitionStore.update(cognitionId, {
          mutedAt: muted ? clock().toISOString() : null,
        });
        if (!updated) return null;
        managementLog.append({
          op: muted ? 'mute' : 'unmute',
          targetKind: 'cognition',
          targetId: cognitionId,
          reason,
          detail: null,
        });
        return updated;
      });
    },

    checkIntegrity() {
      const issues: IntegrityIssue[] = [];
      // 孤儿 event_evidence：event 端或 evidence 端指向不存在的行。
      const eeMissingEvent = db
        .prepare(
          'SELECT ee.event_id, ee.evidence_id FROM event_evidence ee LEFT JOIN event e ON e.id = ee.event_id WHERE e.id IS NULL',
        )
        .all() as unknown as Array<{ event_id: string; evidence_id: string }>;
      for (const r of eeMissingEvent) {
        issues.push({
          kind: 'orphan_event_evidence',
          eventId: r.event_id,
          evidenceId: r.evidence_id,
          missing: 'event',
        });
      }
      const eeMissingEvidence = db
        .prepare(
          'SELECT ee.event_id, ee.evidence_id FROM event_evidence ee LEFT JOIN evidence ev ON ev.id = ee.evidence_id WHERE ev.id IS NULL',
        )
        .all() as unknown as Array<{ event_id: string; evidence_id: string }>;
      for (const r of eeMissingEvidence) {
        issues.push({
          kind: 'orphan_event_evidence',
          eventId: r.event_id,
          evidenceId: r.evidence_id,
          missing: 'evidence',
        });
      }
      // 孤儿 cognition_evidence：cognition 端或 evidence 端指向不存在的行。
      const ceMissingCog = db
        .prepare(
          'SELECT ce.cognition_id, ce.evidence_id FROM cognition_evidence ce LEFT JOIN cognition c ON c.id = ce.cognition_id WHERE c.id IS NULL',
        )
        .all() as unknown as Array<{ cognition_id: string; evidence_id: string }>;
      for (const r of ceMissingCog) {
        issues.push({
          kind: 'orphan_cognition_evidence',
          cognitionId: r.cognition_id,
          evidenceId: r.evidence_id,
          missing: 'cognition',
        });
      }
      const ceMissingEvidence = db
        .prepare(
          'SELECT ce.cognition_id, ce.evidence_id FROM cognition_evidence ce LEFT JOIN evidence ev ON ev.id = ce.evidence_id WHERE ev.id IS NULL',
        )
        .all() as unknown as Array<{ cognition_id: string; evidence_id: string }>;
      for (const r of ceMissingEvidence) {
        issues.push({
          kind: 'orphan_cognition_evidence',
          cognitionId: r.cognition_id,
          evidenceId: r.evidence_id,
          missing: 'evidence',
        });
      }
      return { ok: issues.length === 0, issues, checkedAt: clock().toISOString() };
    },

    // ── 只读列取：读 + 组装收进 facade，不新写记忆逻辑（all/sourcesOf/evidenceOf/effectiveConfidence 都已存在）──

    listEvidence(input = {}) {
      // EvidenceStore.all() 无 subject 过滤（它跨 subject 存），这里按 subjectId 过滤（v1 单 subject 时等于全量）。
      const subjectId = subjectOf(input.subjectId);
      return evidenceStore.all().filter((e) => e.subjectId === subjectId);
    },

    listCognitions(input = {}) {
      const now = clock();
      // cognitionStore.all(subjectId) 已按 subject 过滤；每条配溯源链 + 读时算有效置信（衰减读时算、不持久化）。
      return cognitionStore.all(subjectOf(input.subjectId)).map((c) => ({
        ...c,
        sources: cognitionStore.sourcesOf(c.id),
        effectiveConfidence: effectiveConfidence(c, now, cfg),
      }));
    },

    listEvents(input = {}) {
      // eventStore.all(subjectId) 已按 subject 过滤；每条配它覆盖的证据 id 列表。
      return eventStore.all(subjectOf(input.subjectId)).map((e) => ({
        ...e,
        evidenceIds: eventStore.evidenceOf(e.id),
      }));
    },

    // ── 恢复出厂（破坏性操作）──

    resetSubject(input) {
      const subjectId = subjectOf(input.subjectId);
      // 库内四张表（三层 + 审计）包进一个事务：全成或全滚。indexAll([]) 是外部异步索引、放事务外。
      const counts = transaction(() => {
        // 证据层无 removeBySubject → all() 按 subject 逐条 remove（单 subject 全清；跨 subject 只清本 subject）。
        // ⚠ 先把 id 收集下来再删：semantic_resolution 只认 evidence_id（表里没有 subject_id），
        //   删完证据就没法再问「哪些解析属于本 subject」了。
        // 出厂 = 真抹除（含墓碑）：按 subject 物理删全部证据。软删除（remove）留下的墓碑也一并清掉——
        //   「清空全部记忆」是本库最强的删除承诺，不能给隐私留残留。（普通 remove 是软删；这里必须 purge。）
        const evidenceRemoved = evidenceStore.purgeBySubject(subjectId);
        // 语义解析（含用户原话的解开改写，如「不是」→「用户否认是素食者」）：证据已 purge → 其解析成孤儿，
        //   由下面的孤儿清扫一并收（无需先收集 id：purge 已物理删，NOT IN 子查询看到的就是删后视图）。
        //   ⚠ 必须排在删证据之后。跨 subject：v1 单人单宿主下可接受，与同事务里 managementLog.clear()／
        //   indexAll([]) 的整表口径一致；将来多 subject 化时，这句要改成只清本 subject，孤儿交给 checkIntegrity 报告。
        db.prepare(
          'DELETE FROM semantic_resolution WHERE evidence_id NOT IN (SELECT id FROM evidence)',
        ).run();
        const eventRemoved = eventStore.removeBySubject(subjectId); // 连带清 event_evidence
        const cognitionRemoved = cognitionStore.removeBySubject(subjectId); // 连带清 cognition_evidence
        // 交互上下文快照（含【用户原话】+ AI 上一轮原话，明文 JSON）：出厂必须一起清。
        //   它没有 evidence 关联列，removeEvidenceSafely 按 evidenceId 定位不到它——这里漏掉，
        //   就等于这份用户原话副本【任何入口都删不掉】，而「清空全部记忆」是本库最强的删除承诺。
        //   计数不进返回值：ResetSubjectResult 是公开 API 形状，加字段属破坏性变更，另议。
        interactionContextStore.removeBySubject(subjectId);
        // 出厂=无历史：整表清审计。本次操作不额外 append——留了也被这句冲掉。
        const auditRemoved = managementLog.clear();
        return { evidenceRemoved, eventRemoved, cognitionRemoved, auditRemoved };
      });
      // 清向量索引：有 retriever 才清（VectorRetriever 会 DELETE FROM vectors；NullRetriever no-op）。
      // 无 retriever 则跳过——由 core facade 建 memory 时把自己的 retriever 传进来接上（见 createCore.ts）。
      // 注：本方法同步返回，indexAll 是 fire-and-forget（不 await）——与 transaction 的"库内原子"分属两层。
      // ⚠ 粒度限制（v1 单人单宿主无碍）：indexAll([]) 清的是【整张 vectors 表】（所有 subject 的向量），
      //   不是只清本 subject。多 subject 化时这里要换成 subject 粒度清索引（届时 Retriever 需提供按 subject/id 清）。
      //   该限制属于当前单宿主存储契约；多 subject 部署必须提供按 subject/id 清理的 Retriever 实现。
      if (retriever) {
        retriever
          .indexAll([])
          .catch((e) =>
            console.error(
              resolveLang(cfg) === 'zh'
                ? 'resetSubject 清向量索引失败：'
                : 'resetSubject failed to clear the vector index:',
              e,
            ),
          );
      }
      return counts;
    },
  };
}
