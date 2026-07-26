/**
 * 矛盾判别与落库的【单一真源】：A5 护栏（consolidate 内 guardHits）与全画像 reconcile
 *   （reconcileContradictions）共用同一段极性判 + 挂反证口径，杜绝两处漂移。
 *
 * 从 consolidate.ts 抽出（逐字保持行为）：consolidate 保留薄包装委托到这里、绑定自己的 deps；
 *   reconcile 直接调用。改一处 = 两条路径同步变（避免 #19 / A5 口径分叉）。
 */
import type { LLMClient } from '../llm/client.ts';
import type { Lang, MemoWeftConfig } from '../config.ts';
import type { FormedBy } from '../cognition/model.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { SemanticResolutionStore } from '../interaction/semanticResolutionStore.ts';
import type { PropositionOrigin, AssertionStrength } from '../interaction/model.ts';
import { computeConfidence, deriveCredStatus, isHedgedStated } from './confidence.ts';
import { parseJsonObjectWithRepair } from '../llm/jsonRepair.ts';

/** shortlist 余弦阈值缺省（只对 vector 余弦有语义）。护栏与 reconcile 同用。 */
export const GUARD_DEFAULT_SIMILARITY = 0.5;
/** 每条候选最多做几次极性判（按相似度降序取前 N）缺省。 */
export const GUARD_DEFAULT_TOPK = 3;

/** 余弦相似度；任一为零向量或维度不齐返回 0（与 vectorRetriever 内部同口径）。 */
export function cosine(a: readonly number[], b: readonly number[]): number {
  if (a.length !== b.length || a.length === 0) return 0;
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i]! * b[i]!;
    na += a[i]! * a[i]!;
    nb += b[i]! * b[i]!;
  }
  if (na === 0 || nb === 0) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

/**
 * 极性判断（护栏第二半，非确定性）：关于【同一个人】的两条陈述，作为其【当前】状态是否【冲突】（不能同时为真）。
 * 只判「相反」，不判「相关」——相似度已把候选压到同主题，这里专问极性。
 * ⚠ 提示词经护栏量具调优（真实管线措辞的配对）：旧版"保守·拿不准判否"在啰嗦/带辩解从句的真实认知上
 *   召回仅 ~32%；改成明确纳入 立场/偏好/目标/事实反转、并【看穿】辩解从句与演化语气后，召回 93.5%、误判 0%
 *   （见 _workflow-docs/reviews/guard-metrics-2026-07-25.md）。仍排除 细化/强化/不同侧面/程度差以防误伤。
 *   Python 侧 _judge_contradiction 与本段【逐字对齐】；改一处必须同改。
 */
export async function judgeContradiction(
  llm: LLMClient,
  lang: Lang,
  existingContent: string,
  candidateContent: string,
): Promise<boolean> {
  const zh = lang === 'zh';
  const sys = zh
    ? [
        '你比较关于【同一个人】的两条陈述，各自都当作 TA【当前】的状态，判断它们是否【冲突】——即不可能同时为真。',
        '算冲突(true)：',
        '· 偏好/态度反转：「讨厌跑步」vs「爱上跑步、是一天最爱」；「从小不吃香菜」vs「爱上香菜」。',
        '· 目标放弃/改向：「想当管理者」vs「决定继续做个人贡献者」；「要考研」vs「放弃考研去找工作」。',
        '· 事实状态改变、不能同为当前：「每周练六天」vs「已减到每周四天」；「住北京」vs「搬去上海了」。',
        '· 自我特质的重新评估。',
        '要【看穿】辩解从句、原因、时间/演化措辞（「以前」「如今」「但因为便宜才选」「渐渐变得」）——只看两条【当前】立场/事实是否互斥。',
        '不算冲突(false)：',
        '· 细化/子偏好：「爱喝咖啡」vs「尤其爱手冲」。',
        '· 强化，或不同侧面/方式：「讨厌跑步机」vs「爱户外越野跑」；「戒了含糖饮料」vs「照喝无糖黑咖啡」。',
        '· 仅程度差别，或两者本可同时为真。',
        '只输出 JSON：{"contradicts": true|false}。',
      ].join('\n')
    : [
        'Compare two statements about the SAME person, each taken as their CURRENT state, and decide if they CONFLICT — cannot both be true of the person right now.',
        'COUNT AS CONFLICT (true):',
        '· Reversed preference/attitude: "dislikes running" vs "has come to love running, favorite part of the day"; "unable to eat cilantro" vs "loves cilantro".',
        '· Abandoned/changed goal: "aiming to become a manager" vs "chose to stay an individual contributor"; "planning grad school" vs "gave up grad school for a job".',
        '· A factual state that changed and cannot both be current: "trains six days a week" vs "reduced to four days a week"; "lives in Beijing" vs "moved to Shanghai".',
        '· A re-evaluated self-trait.',
        'Look PAST justifying clauses, reasons, and time/evolution wording ("used to", "now", "but chose it because…", "has developed…"); judge only whether the two CURRENT stances/facts are incompatible.',
        'DO NOT count as conflict (false):',
        '· Refinement/sub-preference: "loves coffee" vs "especially loves pour-over".',
        '· Reinforcement, or a different facet/modality: "hates the treadmill" vs "loves outdoor trail runs"; "gave up sugary drinks" vs "still drinks black coffee".',
        '· Mere degree differences, or two things that can both be true at once.',
        'Output only JSON: {"contradicts": true|false}.',
      ].join('\n');
  const user = zh
    ? `陈述甲：${existingContent}\n陈述乙：${candidateContent}\n作为同一个人的当前状态，它们冲突吗？`
    : `Statement A: ${existingContent}\nStatement B: ${candidateContent}\nAs current states of the same person, do they conflict?`;
  const parsed = await parseJsonObjectWithRepair<{ contradicts?: boolean }>({
    llm,
    messages: [
      { role: 'system', content: sys },
      { role: 'user', content: user },
    ],
    lang,
  });
  return parsed?.contradicts === true;
}

/** hedged 重算期读表所需的最小视图（isHedgedStated 只看这两维）。 */
export interface HedgeResolutionView {
  propositionOrigin: PropositionOrigin | null;
  assertionStrength: AssertionStrength | null;
}

/** resolveHedged / attachContradiction 的共享依赖：hedged 重算期查解析（库优先、内存兜底）。 */
export interface HedgeDeps {
  /** 语义解析 store（可选）：重算期查历史证据的解析（走 forEvidenceIds 批量）。 */
  semanticResolutionStore?: SemanticResolutionStore;
  /** 本轮内存解析（consolidate 有；reconcile 事后扫描无 → 传空 Map 或省略）。 */
  resolutionOf?: ReadonlyMap<string, HedgeResolutionView>;
}

/**
 * 含糊自述（hedged）判定：stated + 有支持集时，按解析视图判是否含糊。
 * ⚠ **不变式**：`supportIds` 恒等于同一调用点传给 `supportCount` 的那一个集合（见 consolidate 各调用点）。
 * 库优先、内存兜底（落库幂等 ⇒ 库里那份才是活下来的那份）。
 */
export function resolveHedged(
  formedBy: FormedBy,
  supportIds: readonly string[],
  deps: HedgeDeps,
): boolean {
  // 非 stated / 空支持集恒 false（isHedgedStated 同判）——提前挡掉，省下重算期那次查表。
  if (formedBy !== 'stated' || supportIds.length === 0) return false;
  const stored = new Map<string, HedgeResolutionView>();
  for (const r of deps.semanticResolutionStore?.forEvidenceIds([...supportIds]) ?? [])
    stored.set(r.evidenceId, r);
  const view = supportIds.map((id) => stored.get(id) ?? deps.resolutionOf?.get(id) ?? null);
  return isHedgedStated(formedBy, view);
}

/** attachContradiction 的依赖：改认知库 + 配置 + hedged 重算期查解析。 */
export interface AttachDeps extends HedgeDeps {
  cognitionStore: CognitionStore;
  config?: MemoWeftConfig;
}

/**
 * 把「反证原话」挂到旧认知上并按【全链】重算把握度（conflict 分支、A5 护栏、reconcile 共用同口径，
 *  避免 #19 的「只翻 credStatus 不重算 → contradictPenalty 空转」回归）。返回是否真挂上。
 *  formedBy 继承旧认知、不重派载体维（重算期约束）；credStatus 交 deriveCredStatus 在
 *  contradictCount>0 下判 conflicted/contested（保住「冲突只暴露、不消解」不变量）。
 */
export function attachContradiction(
  cogId: string,
  contraIds: readonly string[],
  deps: AttachDeps,
): boolean {
  const cog = deps.cognitionStore.get(cogId);
  if (!cog || cog.invalidAt) return false;
  if (contraIds.length === 0) return false; // 没引到冲突原话 → 不凭空标冲突（证据完整性规则）
  const already = new Set(deps.cognitionStore.sourcesOf(cog.id).map((s) => s.evidenceId));
  const add = contraIds.filter((id) => !already.has(id));
  if (add.length)
    deps.cognitionStore.addEvidence(
      cog.id,
      add.map((id) => ({ evidenceId: id, relation: 'contradict' as const })),
    );
  const links = deps.cognitionStore.sourcesOf(cog.id);
  const supportIds = links.filter((l) => l.relation === 'support').map((l) => l.evidenceId);
  const supportCount = supportIds.length;
  const contradictCount = links.filter((l) => l.relation === 'contradict').length;
  const confidence = computeConfidence(
    {
      contentType: cog.contentType,
      formedBy: cog.formedBy,
      supportCount,
      contradictCount,
      hedged: resolveHedged(cog.formedBy, supportIds, deps),
    },
    deps.config,
  );
  deps.cognitionStore.update(cog.id, {
    confidence,
    credStatus: deriveCredStatus(
      confidence,
      contradictCount,
      cog.contentType,
      deps.config,
      supportCount,
    ),
  });
  return true;
}
