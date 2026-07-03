/**
 * M4 归因 / 可解释假设（地图 cell 5 阶段 3 · cell 8 规则 6 · 难点 1）。
 *
 * 从【现象】（一条 state 认知，如"昨晚没睡好"）出发，拉【时间窗内的证据】（含 observed
 * "游戏到3:30"），让 LLM 推"为什么"——产出**可解释假设**：低初始置信、挂证据、可被推翻。
 *
 * 纪律：
 *   - 假设只当假设：formed_by=inferred、低置信封顶（cell 8 规则 6 / 难点 1：动机/特质做不到准）。
 *   - 禁止系统自证（规则 4）：LLM 只看【证据】（用户话 / 观察），不喂 MemoWeft 自己的旧输出；
 *     假设的 support 只挂证据，不挂 MemoWeft 的话。否定一条假设要靠【用户回答】（走阶段 2 闭环）。
 *   - 时间窗粗筛（cell 7）：byTimeRange 拉候选，便宜、天然贴合"昨晚"这类归因。
 *
 * 形态：独立写路径步骤，跟在 consolidate 之后（consolidate 先把"没睡好"沉淀成 state 认知）。
 */
import { config, type MemoWeftConfig } from '../config.ts';
import type { EvidenceStore } from '../evidence/store.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { Cognition } from '../cognition/model.ts';
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import { computeConfidence, deriveCredStatus } from '../consolidation/confidence.ts';
import { filterCloudReadable } from '../evidence/privacy.ts';

export interface AttributeDeps {
  evidenceStore: EvidenceStore;
  cognitionStore: CognitionStore;
  llm: LLMClient;
  /** 可注入配置（P2-5 config 去单例）：不传 = 用全局单例。 */
  config?: MemoWeftConfig;
}

/** 一条落库的可解释假设 + 它解释的现象 + 依据。 */
export interface AttributedHypothesis {
  cognition: Cognition;
  phenomenon: string;
  basedOnEvidenceIds: string[];
}

export interface AttributeResult {
  hypotheses: AttributedHypothesis[];
  /** 本次考察了几个现象（state 认知）。 */
  consideredPhenomena: number;
  llmCalls: number;
}

/** LLM 原始输出（字段名容错）。 */
interface RawHypo {
  content?: string;
  hypothesis?: string;
  based_on_evidence_ids?: string[];
}
interface LLMOut {
  hypotheses?: RawHypo[];
}

const SYSTEM = [
  '你在为一个【现象】寻找【可能的原因】，产出"可解释假设"。',
  '铁律：',
  '- 只给【可能的】原因，绝不下定论；宁可一条不给，也不要硬编、不要凑数。',
  '- 原因必须是【行为或客观观察】（例如"游戏开到凌晨3:30"），不要用【另一种主观感受/情绪】去解释现象',
  '  （不要写"因为烦所以渴""因为没睡好所以烦"这种把一个抱怨接到另一个抱怨上）。',
  '- 每条假设必须基于下面列出的【证据】，注明依据的证据 id；只引最相关的 1~2 条，没有合适的就不要给。',
  '- 一句话写清因果方向，例如"可能因为玩游戏太晚，导致没睡好"。',
  '- 至多给 1 条最站得住的假设；宁缺毋滥。',
  '严格按示例字段名输出一个 JSON 对象，没有就给空数组，不要解释：',
  '{"hypotheses":[{"content":"可能因为玩游戏太晚导致没睡好","based_on_evidence_ids":["ev-1"]}]}',
].join('\n');

function buildMessages(
  phenomenon: string,
  evidences: Array<{ id: string; sourceKind: string; occurredAt: string; text: string }>,
): ChatMessage[] {
  const list = evidences
    .map((e) => `- [${e.id}] (${e.sourceKind} ${e.occurredAt.slice(0, 16)}) ${e.text}`)
    .join('\n');
  return [
    { role: 'system', content: SYSTEM },
    { role: 'user', content: `【现象】：${phenomenon}\n\n【可能相关的行为/观察证据（只能从这里选原因）】：\n${list}` },
  ];
}

function parseOut(raw: string): LLMOut {
  const start = raw.indexOf('{');
  const end = raw.lastIndexOf('}');
  if (start === -1 || end === -1 || end < start) return {};
  try {
    return JSON.parse(raw.slice(start, end + 1)) as LLMOut;
  } catch {
    return {};
  }
}

/** 减去 windowHours 小时，返回 ISO（用于时间窗下界）。 */
function minusHours(iso: string, hours: number): string {
  return new Date(new Date(iso).getTime() - hours * 3600_000).toISOString();
}

/**
 * 对未归因的 state 现象做一次归因。
 * v1 简化：现象 = 当前 active 的 state 认知里【还没有假设解释过】的那些。
 */
export async function attribute(subjectId: string, deps: AttributeDeps): Promise<AttributeResult> {
  const fullCfg = deps.config ?? config; // 可注入配置（缺省=单例）
  const cfg = fullCfg.attribution;
  const active = deps.cognitionStore.active(subjectId);
  const states = active.filter((c) => c.contentType === 'state');
  const hypos = active.filter((c) => c.contentType === 'hypothesis');

  const supportOf = (cogId: string): string[] =>
    deps.cognitionStore.sourcesOf(cogId).filter((l) => l.relation === 'support').map((l) => l.evidenceId);

  // state 现象自身的证据：只能当"现象 side"，【不能当原因】——禁"用一个抱怨解释另一个抱怨"（用户拍板）。
  const stateEvidence = new Set<string>();
  for (const s of states) for (const id of supportOf(s.id)) stateEvidence.add(id);
  // 已有假设引用过的证据 → 判某现象【是否已归因】（按现象去重，修旧的"按证据去重"bug：
  //   state 证据只会出现在现象 side，故"现象证据被某假设引用" ⇔ 该现象已归因，可靠）。
  const hypoRefEvidence = new Set<string>();
  for (const h of hypos) for (const id of supportOf(h.id)) hypoRefEvidence.add(id);
  const isAttributed = (phenomId: string): boolean => supportOf(phenomId).some((id) => hypoRefEvidence.has(id));

  // 只归因【最近活跃、还没归因过】的现象，一次最多 maxPhenomenaPerRun 个（避免一次扫全部 state 爆炸）。
  // 按 updatedAt 降序：用户刚（重新）抱怨的那条会被 consolidate 触碰、updatedAt 最新，正是"当下要解释的"。
  // ④治脑补（2026-07-01）：现象要【攒够 / 反复出现】≥ minPhenomenonSupport 条支撑证据才归因——
  // 别每句"好累"就推一串因果。偶发一次的情绪先攒着，反复出现（多条支撑）再解释。N 可配、dogfood 后调。
  const phenomena = states
    .filter((c) => !isAttributed(c.id))
    .filter((c) => supportOf(c.id).length >= cfg.minPhenomenonSupport)
    .sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))
    .slice(0, cfg.maxPhenomenaPerRun);

  const before = deps.llm.callCount;
  const hypotheses: AttributedHypothesis[] = [];
  let considered = 0;
  const upperBound = new Date().toISOString(); // 窗口上界放到"此刻"，吸收"抱怨后才注入观察"的录入时差

  for (const phenom of phenomena) {
    const phenomEvidences = supportOf(phenom.id)
      .map((id) => deps.evidenceStore.get(id))
      .filter((e): e is NonNullable<typeof e> => e !== null)
      .sort((a, b) => (a.occurredAt < b.occurredAt ? 1 : -1)); // 最晚在前
    // 现象锚 = 最晚一条现象证据（抱怨时刻）。只挂【这一条】当假设的现象 side 支撑，
    // 不把现象积累的一堆（可能被污染的）证据全挂上——避免支撑爆炸（dogfood 暴露）。
    const anchorEvidence = phenomEvidences[0] ?? null;
    const anchor = anchorEvidence?.occurredAt ?? phenom.createdAt;

    // 候选【原因】：[anchor - windowHours, 此刻] 内、可推断、且【不支撑任何 state 现象】的证据（禁 state→state）。
    // 隐私关：候选原因里只留"允许上云"的喂给（云端）LLM。
    // deps.llm 假设是云端模型——接本地模型时需改（见 evidence/privacy.ts 前提注释）。
    const causes = filterCloudReadable(
      deps.evidenceStore
        .byTimeRange(minusHours(anchor, cfg.windowHours), upperBound)
        .filter((e) => e.allowInference)
        .filter((e) => !stateEvidence.has(e.id)),
    );
    if (causes.length === 0) continue; // 没有行为/观察类原因可依据 → 不硬编

    considered++;
    // 只把【候选原因】喂给 LLM（现象本身已写在 prompt 的【现象】里，不必再塞一堆现象证据当噪声）。
    const candidates = causes.map((e) => ({
      id: e.id,
      sourceKind: e.sourceKind,
      occurredAt: e.occurredAt,
      text: e.summary || e.rawContent,
    }));
    const candidateIds = new Set(candidates.map((c) => c.id));

    const out = parseOut(await deps.llm.chat(buildMessages(phenom.content, candidates)));
    for (const raw of out.hypotheses ?? []) {
      const content = (raw.content ?? raw.hypothesis ?? '').trim();
      if (!content) continue;
      // 只采纳引用了真实候选原因的依据（防 LLM 编造 id / 自证），并【硬封顶】条数（防过度归因）。
      const citedCauses = (raw.based_on_evidence_ids ?? [])
        .filter((id) => candidateIds.has(id))
        .slice(0, cfg.maxCausesPerHypothesis);
      if (citedCauses.length === 0) continue; // 没引到真实原因 → 不硬编
      // 支撑 = ≤N 条原因证据 + 1 个现象锚点（现象锚同时让"已归因"判定有据，dedup 可靠）。
      const basedOn = [...new Set([...citedCauses, ...(anchorEvidence ? [anchorEvidence.id] : [])])];

      const rawConf = computeConfidence({
        contentType: 'hypothesis',
        formedBy: 'inferred',
        supportCount: basedOn.length,
        contradictCount: 0,
      }, fullCfg);
      const confidence = Math.min(rawConf, cfg.hypothesisCap); // 假设级封顶：低声说（规则 6）
      const cognition = deps.cognitionStore.put({
        subjectId,
        content,
        contentType: 'hypothesis',
        formedBy: 'inferred',
        confidence,
        credStatus: deriveCredStatus(confidence, 0, 'hypothesis', fullCfg),
        evidence: basedOn.map((id) => ({ evidenceId: id, relation: 'support' as const })),
      });
      hypotheses.push({ cognition, phenomenon: phenom.content, basedOnEvidenceIds: basedOn });
    }
  }

  return { hypotheses, consideredPhenomena: considered, llmCalls: deps.llm.callCount - before };
}
