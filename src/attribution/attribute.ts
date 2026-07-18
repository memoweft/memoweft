/**
 * 归因 / 可解释假设（public contract；动机与特质只能作为低置信假设）。
 *
 * 从【现象】（一条 state 认知，如"昨晚没睡好"）出发，拉【时间窗内的证据】（含 observed
 * "游戏到3:30"），让 LLM 推"为什么"——产出**可解释假设**：低初始置信、挂证据、可被推翻。
 *
 * 纪律：
 *   - 假设只当假设：formed_by=inferred、低置信封顶（public contract：动机与特质做不到准）。
 *   - 禁止系统自证：LLM 只读取【证据】（用户话 / 观察），不接收 MemoWeft 自己的旧输出；
 *     假设的 support 只挂证据，不挂 MemoWeft 的话。否定一条假设要靠【用户回答】（走 闭环）。
 *   - 时间窗粗筛（runtime contract）：byTimeRange 拉候选，便宜、天然贴合"昨晚"这类归因。
 *
 * 形态：独立写路径步骤，跟在 consolidate 之后（consolidate 先把"没睡好"沉淀成 state 认知）。
 */
import { config, resolveLang, type Lang, type MemoWeftConfig } from '../config.ts';
import type { EvidenceStore } from '../evidence/store.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { Cognition } from '../cognition/model.ts';
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import { computeConfidence, deriveCredStatus } from '../consolidation/confidence.ts';
import { filterReadableByTier } from '../evidence/privacy.ts';
import { parseJsonObjectWithRepair } from '../llm/jsonRepair.ts';
import { resolveEchoedId } from '../llm/echoedId.ts';
import { systemClock, type Clock } from '../clock.ts';
import { ATTRIBUTE_PROMPT } from './prompts.ts';

export interface AttributeDeps {
  evidenceStore: EvidenceStore;
  cognitionStore: CognitionStore;
  llm: LLMClient;
  /** 可注入配置（config 去单例）：不传 = 用全局单例。 */
  config?: MemoWeftConfig;
  /** 可注入时钟：归因窗口上界"此刻"走它；缺省真实系统时间。 */
  clock?: Clock;
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

function buildMessages(
  phenomenon: string,
  evidences: Array<{ id: string; sourceKind: string; occurredAt: string; text: string }>,
  lang: Lang,
): { messages: ChatMessage[]; tagToId: Map<string, string> } {
  // 发短标号 [e1] 而非 36 字符 UUID：模型会模仿提示词示例的 id 形态、间歇性把长 UUID
  //   截成前缀写回 → candidateIds 精确匹配落空 → 假设被静默丢弃。发标号 = 示例与真实形态一致，
  //   模型结构上写不错（与 consolidate 相同的手法）。tagToId 落库前把标号翻译回真证据 id。
  const tagToId = new Map<string, string>();
  const list = evidences
    .map((e, i) => {
      const tag = `e${i + 1}`;
      tagToId.set(tag, e.id);
      return `- [${tag}] (${e.sourceKind} ${e.occurredAt.slice(0, 16)}) ${e.text}`;
    })
    .join('\n');
  const body =
    lang === 'zh'
      ? `【现象】：${phenomenon}\n\n【可能相关的行为/观察证据（只能从这里选原因）】：\n${list}`
      : `[Phenomenon]: ${phenomenon}\n\n[Possibly relevant behavior/observation evidence (causes may only be chosen from here)]:\n${list}`;
  return {
    messages: [
      { role: 'system', content: ATTRIBUTE_PROMPT.text[lang] },
      { role: 'user', content: body },
    ],
    tagToId,
  };
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
  const lang = resolveLang(fullCfg);
  const cfg = fullCfg.attribution;
  // active() 仅返回未失效且未归档项，因此归档的现象与假设不参与归因。
  const active = deps.cognitionStore.active(subjectId);
  const states = active.filter((c) => c.contentType === 'state');
  const hypos = active.filter((c) => c.contentType === 'hypothesis');

  const supportOf = (cogId: string): string[] =>
    deps.cognitionStore
      .sourcesOf(cogId)
      .filter((l) => l.relation === 'support')
      .map((l) => l.evidenceId);

  // state 现象自身的证据：只能当"现象 side"，【不能当原因】——禁"用一个抱怨解释另一个抱怨"。
  const stateEvidence = new Set<string>();
  for (const s of states) for (const id of supportOf(s.id)) stateEvidence.add(id);
  // 已有假设引用过的证据 → 判某现象【是否已归因】（按现象去重，修旧的"按证据去重"bug：
  //   state 证据只会出现在现象 side，故"现象证据被某假设引用" ⇔ 该现象已归因，可靠）。
  const hypoRefEvidence = new Set<string>();
  for (const h of hypos) for (const id of supportOf(h.id)) hypoRefEvidence.add(id);
  const isAttributed = (phenomId: string): boolean =>
    supportOf(phenomId).some((id) => hypoRefEvidence.has(id));

  // 只归因【最近活跃、还没归因过】的现象，一次最多 maxPhenomenaPerRun 个（避免一次扫全部 state 爆炸）。
  // 按 updatedAt 降序：用户刚（重新）抱怨的那条会被 consolidate 触碰、updatedAt 最新，正是"当下要解释的"。
  // 归因支撑门槛：现象至少有 minPhenomenonSupport 条支撑证据才允许推导原因，
  // 别每句"好累"就推一串因果。偶发一次的情绪先攒着，反复出现（多条支撑）再解释。N 可配、integration testing 后调。
  const phenomena = states
    .filter((c) => !isAttributed(c.id))
    .filter((c) => supportOf(c.id).length >= cfg.minPhenomenonSupport)
    .sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))
    .slice(0, cfg.maxPhenomenaPerRun);

  const before = deps.llm.callCount;
  const hypotheses: AttributedHypothesis[] = [];
  let considered = 0;
  const upperBound = (deps.clock ?? systemClock)().toISOString(); // 窗口上界放到"此刻"（可注入 clock），吸收"抱怨后才注入观察"的录入时差

  for (const phenom of phenomena) {
    const phenomEvidences = supportOf(phenom.id)
      .map((id) => deps.evidenceStore.get(id))
      .filter((e): e is NonNullable<typeof e> => e !== null)
      .sort((a, b) => (a.occurredAt < b.occurredAt ? 1 : -1)); // 最晚在前
    // 现象锚 = 最晚一条现象证据（抱怨时刻）。只挂【这一条】当假设的现象 side 支撑，
    // 不把现象积累的一堆（可能被污染的）证据全挂上——避免支撑爆炸（integration testing 暴露）。
    const anchorEvidence = phenomEvidences[0] ?? null;
    const anchor = anchorEvidence?.occurredAt ?? phenom.createdAt;

    // 候选【原因】：[anchor - windowHours, 此刻] 内、可推断（allowInference）、且【不支撑任何 state 现象】的证据（禁 state→state）。
    // 隐私关（按当前写模型 tier）：候选原因中仅将【当前模型可读】的内容提供给 LLM（tier=cloud 筛 allowCloudRead / local 筛 allowLocalRead）。
    // 推理门 allowInference 已在下方 .filter 里（本步与 distill/consolidate 三处一致）。
    const causes = filterReadableByTier(
      deps.evidenceStore
        .byTimeRange(minusHours(anchor, cfg.windowHours), upperBound)
        .filter((e) => e.allowInference)
        .filter((e) => !stateEvidence.has(e.id)),
      deps.llm.tier ?? 'cloud',
    );
    if (causes.length === 0) continue; // 没有行为/观察类原因可依据 → 不硬编

    considered++;
    // 只将【候选原因】提供给 LLM（现象本身已写在 prompt 的【现象】中，无需重复加入现象证据）。
    const candidates = causes.map((e) => ({
      id: e.id,
      sourceKind: e.sourceKind,
      occurredAt: e.occurredAt,
      text: e.summary || e.rawContent,
    }));
    const candidateIds = new Set(candidates.map((c) => c.id));

    // 结构化输出加固（jsonRepair）：去围栏 → 解析对象；失败落日志 + 最多重试一次（提示"只输出 JSON"）。
    // 仍失败 → null（按"本轮此现象无产出"处理，等价旧的返回空对象）。重试复用同一批已过滤 messages
    // （写路径边界 C：只复用已过滤上下文 + 追加提示，不引入新证据文本）。
    const { messages, tagToId } = buildMessages(phenom.content, candidates, lang);
    const out =
      (await parseJsonObjectWithRepair<LLMOut>({
        llm: deps.llm,
        messages,
        lang,
      })) ?? {};
    for (const raw of out.hypotheses ?? []) {
      const content = (raw.content ?? raw.hypothesis ?? '').trim();
      if (!content) continue;
      // 只采纳引用了真实候选原因的依据（防 LLM 编造 id / 自证），并【硬封顶】条数（防过度归因）。
      // 经 resolveEchoedId 归一：标号 e1 / 精确 UUID / 截断前缀都解回真 id；捏造 / 歧义仍丢。
      const citedCauses = [
        ...new Set(
          (raw.based_on_evidence_ids ?? [])
            .map((id) => resolveEchoedId(id, candidateIds, tagToId))
            .filter((id): id is string => id !== null),
        ),
      ].slice(0, cfg.maxCausesPerHypothesis);
      if (citedCauses.length === 0) continue; // 没引到真实原因 → 不硬编
      // 支撑 = ≤N 条原因证据 + 1 个现象锚点（现象锚同时让"已归因"判定有据，dedup 可靠）。
      const basedOn = [
        ...new Set([...citedCauses, ...(anchorEvidence ? [anchorEvidence.id] : [])]),
      ];

      const rawConf = computeConfidence(
        {
          contentType: 'hypothesis',
          formedBy: 'inferred',
          supportCount: basedOn.length,
          contradictCount: 0,
        },
        fullCfg,
      );
      const confidence = Math.min(rawConf, cfg.hypothesisCap); // 假设级置信上限，防止推断被提升为定论
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
