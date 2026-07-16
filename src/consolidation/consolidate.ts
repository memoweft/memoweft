/**
 * 画像生成 · 增量更新（写路径，地图 cell 4 / 5 阶段 2）。落地 v3 写路径"增量 + 反证"。
 *
 * 处理【未消化的新事件】，连同【现有画像】给 LLM，判断新事件对画像意味着什么：
 *   - new      新事件里有、画像没有 → 新增认知。
 *   - reinforce 新事件印证现有认知 → 补证据、置信升。
 *   - correct  用户在新事件里【明确纠正】现有认知 → 旧的标失效保留、采纳新的（M6 纠正闭环）。
 *   - conflict 矛盾但非明确纠正（如行为 vs 旧偏好）→ 标 conflicted，两条都留，暴露不消解（cell 8 规则 5）。
 *
 * 纪律：MemoWeft 自算把握度（不采信 LLM 自报）；推测低置信；旧判断失效保留可溯源（cell 6）。
 */
import type { EventStore } from '../event/store.ts';
import type { EvidenceStore } from '../evidence/store.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { Cognition, ContentType, FormedBy } from '../cognition/model.ts';
import type { Evidence, SourceKind } from '../evidence/model.ts';
import type { SemanticResolutionStore } from '../interaction/semanticResolutionStore.ts';
import type { ResponseAct, PromptAct, PropositionOrigin, AssertionStrength } from '../interaction/model.ts';
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import { computeConfidence, deriveCredStatus } from './confidence.ts';
import { deriveFormedBy } from './deriveFormedBy.ts';
import { filterReadableByTier } from '../evidence/privacy.ts';
import { sourceLabel, aiContextSuffix } from '../evidence/sourceLabel.ts';
import { parseJsonObjectWithRepair } from '../llm/jsonRepair.ts';
import { noopTransaction, type Transaction } from '../store/transaction.ts';
import { resolveLang, type Lang, type MemoWeftConfig } from '../config.ts';
import { systemClock, type Clock } from '../clock.ts';
import { CONSOLIDATE_PROMPT } from './prompts.ts';

export interface ConsolidateDeps {
  eventStore: EventStore;
  /** 证据层：证据级溯源要读原话文本喂给 LLM（地基债 · 证据级引用）。 */
  evidenceStore: EvidenceStore;
  cognitionStore: CognitionStore;
  /** 语义解析 store（v0.6 Phase 2·D-0034，**可选**）：接了则把每条证据的语义解析落 semantic_resolution 表。
   *  **v0.6 Phase 3 起：不接它也不影响 formedBy 派生** —— `deriveFormedBy` 吃的是本轮 LLM 产的解析
   *  （内存里的 `resolutionOf`）、**不读表**，所以本依赖始终可选，接了只是多一份可追溯的落库。
   *  （这也是为什么 Phase 3 没把它改成必需：唯一要读历史解析的场景本是 reinforce 的升级路，
   *   而 D-0035 拍板③ 已把升级路取消、改为并存新认知。） */
  semanticResolutionStore?: SemanticResolutionStore;
  llm: LLMClient;
  /** 事务器（可选）：接了共享连接就传它，把下方多步写（new/correct/conflict/reinforce + markConsolidated）
   *  原子化——崩在中间整段回滚，不留半拉画像。不传（如各开各连接的测试）= 直接跑，行为同旧。见 store/openStores.ts。 */
  transaction?: Transaction;
  /** 可注入配置（P2-5 config 去单例）：不传 = 用全局单例，行为同旧；传了则把握度阈值等按这份算。 */
  config?: MemoWeftConfig;
  /** 可注入时钟（Phase 4）：correct/conflict 分支的显式时间戳走它；缺省真实系统时间。 */
  clock?: Clock;
}

export interface ConsolidateResult {
  created: Cognition[];
  reinforced: number;
  corrected: number;
  conflicted: number;
  processedEvents: number;
  llmCalls: number;
  /** 写路径仪表（决策 D4 · 只观测不动刀）：本轮注入 prompt 的 active 认知条数（= 现有画像大小）。
   *  0 = 本轮未执行整理（无新事件早退）。给 11-A 膨胀债画 dogfood 曲线用。 */
  profileSize: number;
  /** 写路径仪表（决策 D4 · 只观测不动刀）：本轮 buildMessages 产物全部 content 的字符数之和（prompt 多大）。
   *  0 = 本轮未执行整理（无新事件早退）。给 11-A 膨胀债画 dogfood 曲线用。 */
  promptChars: number;
}

/** 候选认知的原始形状（字段名容错：content / new_content / cognition 都接）。 */
interface RawCog {
  content?: string;
  new_content?: string;
  cognition?: string;
  content_type?: string;
  formed_by?: string;
  /** 支撑这条认知的【具体原话证据 id】（证据级，非事件级）；容错也接 evidence_ids。 */
  support_evidence_ids?: string[];
  evidence_ids?: string[];
}
interface RawRef {
  cognition_id?: string;
  support_evidence_ids?: string[];
  evidence_ids?: string[];
  /** 强化的来源强度分类（D-0033 Phase 1b）:LLM 把这次 reinforce 判为哪种来源;仅 'stated' 用于把
   *  confirmed 认知升级破顶(见 reinforce 分支)。Phase 2 提示词才教它标;Phase 1b 缺省 undefined = 不升级。 */
  formed_by?: string;
}
/** 一条候选语义解析（v0.6 Phase 2·D-0034）：LLM 对某条原话的解析（这句在回应谁提出的什么、是肯定/否定/选择/含糊）。
 *  字段全 string 容错；落库前经 VALID_* 收敛（非法值落 null）。**是解释、不是证据**，永不进 support 白名单（3a/3d）。 */
interface RawResolution {
  evidence_id?: string;
  resolved_content?: string;
  response_act?: string;
  prompt_act?: string;
  proposition_origin?: string;
  assertion_strength?: string;
  required_context?: string;
}
interface LLMOut {
  new?: RawCog[];
  reinforce?: RawRef[];
  correct?: Array<{ cognition_id?: string } & RawCog>;
  conflict?: RawRef[];
  resolutions?: RawResolution[];
}

/** 从候选里取它引的原话 id（容错字段名）。 */
function citedIds(c: RawCog | RawRef): string[] {
  return c.support_evidence_ids ?? c.evidence_ids ?? [];
}

const VALID_TYPES = ['fact', 'preference', 'goal', 'project', 'state', 'trait'];
const VALID_FORMED = ['stated', 'observed', 'ruled', 'confirmed', 'inferred'];
// 语义解析枚举（v0.6 Phase 2·D-0034）：落库前收敛，非法值落 null（与 model.ts 的 `... | null` 一致）。
const VALID_RESPONSE_ACT = ['affirm', 'negate', 'select', 'elaborate', 'ask', 'none', 'other'];
const VALID_PROMPT_ACT = ['propose', 'ask', 'state', 'none', 'other'];
const VALID_ORIGIN = ['user_stated', 'assistant_proposed'];
const VALID_STRENGTH = ['explicit', 'weak', 'none'];

/**
 * 从原始候选里抽出认知（容错字段名 + 缺类型给保守默认 fact）。无内容返回 null。
 *
 * **不再算 formedBy**（v0.6 Phase 3·D-0035 拍板①）：载体维（stated/confirmed/observed = 这条信息是
 * 谁的话）由 `deriveFormedBy` 从支持证据算、**模型说了不算**；模型只保留「这条是不是我推断出来的」
 * 这一维（推断距离），即 `modelSaysInferred` —— 那是往低了报、无骗人动机的一维，且只有它知道。
 */
function pickCognition(c: RawCog): { content: string; contentType: ContentType; modelSaysInferred: boolean } | null {
  const content = (c.content ?? c.new_content ?? c.cognition ?? '').trim();
  if (!content) return null;
  const contentType = (VALID_TYPES.includes(c.content_type ?? '') ? c.content_type : 'fact') as ContentType;
  // 模型只负责「这条是不是我推断出来的」这一维（拍板①）。**缺 / 非法值 → 保守当推断**：
  //   模型【漏标】时旧世界兜底成 inferred(200)，若改成「缺 = 不是推断 → 走载体维」，一条 spoken 证据
  //   就会把漏标的推断型认知抬到 stated(600) —— 那是真的退步。EVAL-T05「缺 formed_by → 保守当 inferred，
  //   不默认高置信亲述」这条既有断言正是钉这个，Phase 3 刻意保留其语义。
  //   ⇒ 只有模型【明确填了合法值且不是 inferred】，才认为它主张「这条是从原话直接得到的」，进而走载体维。
  const declared = VALID_FORMED.includes(c.formed_by ?? '') ? c.formed_by : null;
  return { content, contentType, modelSaysInferred: declared === null || declared === 'inferred' };
}

/** 喂给 LLM 的事件视图：事件摘要 + 其下逐条原话（带证据 id 供引用）。 */
interface EventView {
  summary: string;
  occurredAt: string;
  utterances: Array<{ id: string; text: string }>;
}

function buildMessages(existing: Cognition[], events: EventView[], lang: Lang): ChatMessage[] {
  const zh = lang === 'zh';
  const profile = existing.length
    ? existing.map((c) => `- [${c.id}] (${c.contentType}) ${c.content}`).join('\n')
    : zh ? '（空）' : '(none)';
  const material = events
    .map((e) => {
      const head = `· ${zh ? '事件' : 'Event'} (${e.occurredAt.slice(0, 16)}) ${e.summary}`;
      const lines = e.utterances.map((u) => `    - [${u.id}] ${u.text}`).join('\n');
      return lines ? `${head}\n${lines}` : head;
    })
    .join('\n');
  const body = zh
    ? `【现有画像】：\n${profile}\n\n【新材料】：\n${material}`
    : `[Existing profile]:\n${profile}\n\n[New material]:\n${material}`;
  return [
    { role: 'system', content: CONSOLIDATE_PROMPT.text[lang] },
    { role: 'user', content: body },
  ];
}

export async function consolidate(subjectId: string, deps: ConsolidateDeps): Promise<ConsolidateResult> {
  const newEvents = deps.eventStore.unconsolidated(subjectId);
  const empty: ConsolidateResult = { created: [], reinforced: 0, corrected: 0, conflicted: 0, processedEvents: 0, llmCalls: 0, profileSize: 0, promptChars: 0 };
  if (newEvents.length === 0) return empty;

  // 现有画像 = active()（未失效且未归档）：归档全面雪藏（批次3 用户拍板）——已归档认知不进
  // 「现有画像」prompt、不参与强化/纠正/冲突比对（数据保留、可经恢复归档重新生效）。
  const existing = deps.cognitionStore.active(subjectId);

  // 事件视图 + 合法原话集合：把每个新事件覆盖的原话（带 id+原文）摊开给 LLM 引用；
  // 只有这些 id 是合法支撑（防 LLM 编造/自证，证据级溯源）。
  const validEvidence = new Set<string>();
  /** 合法原话里【来源=spoken】的子集（v0.6 Phase 2·D-0034）：resolutions 只对【用户真说出口的话】落解析。
   *  [行为观察]/[工具返回] 不是用户在说话——「这句在回应谁提出的什么、是肯定还是否定」对一条行为观察
   *  本就无意义，落进表里就是脏数据（而 Phase 3 的 deriveFormedBy 要读这张表：垃圾进→垃圾出）。
   *  提示词 v4 也教了同样的收窄，但**这里是结构保证**：模型不听话也进不了表（同 validEvidence 守 3d 的思路——
   *  纪律靠结构、不靠提示词自觉）。 */
  const spokenEvidence = new Set<string>();
  /** 过了隐私门的证据 id → 派生载体维要的两样（v0.6 Phase 3·D-0035）：来源 + 它的 AI 上一句。
   *  在下面构建 events 时顺手填（那里本就查了这两样），避免为每条支撑再查一次 store。 */
  const carrierOf = new Map<string, { sourceKind: SourceKind; precedingAiContext: string | null }>();
  // 隐私关（按当前写模型 tier）+ 推理门：事件覆盖的原话里，只把【当前模型可读】且【可推画像】的喂给 LLM、
  //   也只让它们当合法支撑——被挡的既不进 prompt、也进不了 validEvidence（不成为所生认知的依据）。
  //   tier=cloud 筛 allowCloudRead / tier=local 筛 allowLocalRead；inference=false 不进画像（distill/attribute 三处一致）。
  const tier = deps.llm.tier ?? 'cloud';
  const lang = resolveLang(deps.config);
  const events: EventView[] = newEvents.map((ev) => {
    const evidences = deps.eventStore
      .evidenceOf(ev.id)
      .map((id) => deps.evidenceStore.get(id))
      .filter((e): e is Evidence => e !== null);
    const utterances = filterReadableByTier(evidences, tier)
      .filter((e) => e.allowInference)
      .map((e) => {
        validEvidence.add(e.id);
        if (e.sourceKind === 'spoken') spokenEvidence.add(e.id); // resolutions 的落库白名单（见 spokenEvidence 声明）
        const aiCtx = deps.evidenceStore.precedingAiContextOf(e.id);
        carrierOf.set(e.id, { sourceKind: e.sourceKind, precedingAiContext: aiCtx }); // 见 carrierOf 声明
        // 来源感知（D-0018）:原话带来源前缀,让 LLM 定 formedBy 时知道哪些不是用户亲口。
        // 附和/AI 上下文（D-0033 Phase 1b）:把 preceding_ai_context【追加进本原话的 text 后缀】
        //   (经专用只读 precedingAiContextOf,只对已过隐私门的证据取)——让 LLM 看懂"是的"这类孤儿回应
        //   在附和 AI 提出的什么命题,据此判 formed_by=confirmed。**关键(3a/3d):AI 上文只是 text 后缀、
        //   共用这条【真证据】的 id,绝不铸独立 {id,text} 条目 → validEvidence 只含真证据 id、AI 上文永无 id
        //   → pickSupport 结构性引不到 AI 上文 → 助手输出永不成为可溯源证据**。缺省无 → 后缀空 = no-op。
        return {
          id: e.id,
          text: sourceLabel(e.sourceKind, lang) + e.rawContent + aiContextSuffix(aiCtx, lang),
        };
      });
    return { summary: ev.summary, occurredAt: ev.occurredAt, utterances };
  });
  /** 取候选引的原话 id，只留合法的、去重（无合法引用 → 空，调用方按"跳过"处理）。 */
  const pickSupport = (ids: string[]): string[] => [...new Set(ids.filter((id) => validEvidence.has(id)))];

  // 结构化输出加固（jsonRepair）：去代码块围栏 → 解析对象；失败落日志 + 最多重试一次（提示"只输出 JSON"）。
  // 仍失败 → null（按"本轮无产出"处理，等价旧的返回空对象；下方各 `?? []` 兜住）。重试会多调一次模型，故计数取前后差。
  // 写路径仪表（D4 只观测）：先把 messages 存下来量 prompt 字符数，再原样喂给解析器——行为零变化，只加计量。
  const messages = buildMessages(existing, events, lang);
  const promptChars = messages.reduce((n, m) => n + m.content.length, 0);
  const before = deps.llm.callCount;
  const out = (await parseJsonObjectWithRepair<LLMOut>({
    llm: deps.llm,
    messages,
    lang,
  })) ?? {};
  const llmCalls = deps.llm.callCount - before;

  /**
   * 本轮 LLM 产的语义解析，规整 + 收窄后按 evidenceId 索引（v0.6 Phase 3·D-0035）。
   * **落库（下方）与派生（deriveFormedBy）共用这一份**——两处各自校验必然漂移。
   * 收窄同落库白名单：只收【用户真说的】真证据（`spokenEvidence` ⊆ `validEvidence` → 3d 一并守住）；
   * `resolved_content` 空则整条丢；非法枚举收敛 null（不写脏数据）；同证据多条解析【先到先得】（同幂等语义）。
   * **为什么必须在写循环之前规整**：new 路【当场】就要算 formedBy，而落库在四个写循环之后。
   */
  const resolutionOf = new Map<
    string,
    {
      resolvedContent: string;
      responseAct: ResponseAct | null;
      promptAct: PromptAct | null;
      propositionOrigin: PropositionOrigin | null;
      assertionStrength: AssertionStrength | null;
      requiredContext: string | null;
    }
  >();
  for (const r of out.resolutions ?? []) {
    const eid = r.evidence_id;
    if (!eid || !spokenEvidence.has(eid)) continue; // 3d + 来源收窄（见 spokenEvidence 声明）
    const resolved = (r.resolved_content ?? '').trim();
    if (!resolved) continue; // resolved_content 非空
    if (resolutionOf.has(eid)) continue; // 同证据先到先得
    resolutionOf.set(eid, {
      resolvedContent: resolved,
      responseAct: VALID_RESPONSE_ACT.includes(r.response_act ?? '') ? (r.response_act as ResponseAct) : null,
      promptAct: VALID_PROMPT_ACT.includes(r.prompt_act ?? '') ? (r.prompt_act as PromptAct) : null,
      propositionOrigin: VALID_ORIGIN.includes(r.proposition_origin ?? '') ? (r.proposition_origin as PropositionOrigin) : null,
      assertionStrength: VALID_STRENGTH.includes(r.assertion_strength ?? '') ? (r.assertion_strength as AssertionStrength) : null,
      requiredContext: (r.required_context ?? '').trim() || null,
    });
  }

  /**
   * 算一条认知的 formedBy（v0.6 Phase 3·D-0035）。
   * 模型报「我推断的」→ `inferred`（拍板①：推断距离这一维仍归模型，代码只接管载体维）；
   * 否则由 `deriveFormedBy` 从支持证据的【载体维】算（取最弱，含「spoken 无解析」的结构性兜底）。
   * 派生不出（支持集空）→ `inferred`（最保守）。
   */
  const resolveFormedBy = (modelSaysInferred: boolean, supportIds: readonly string[]): FormedBy => {
    if (modelSaysInferred) return 'inferred';
    const inputs = supportIds.map((id) => {
      const c = carrierOf.get(id);
      return {
        // 取不到理论上不该发生（supportIds 已过 validEvidence 白名单）→ 保守当作「不是用户亲口」
        sourceKind: c?.sourceKind ?? ('observed' as const),
        precedingAiContext: c?.precedingAiContext ?? null,
        resolution: resolutionOf.get(id) ?? null,
      };
    });
    return deriveFormedBy(inputs) ?? 'inferred';
  };

  // 事务化（写路径一致性，地图 cell 4）：下面这些多步写（new/correct/conflict/reinforce + markConsolidated）
  // 要么全成、要么全滚——崩在中间不留半拉画像、也不出现"认知写了但事件没标已消化 → 下轮重复处理"。
  // 只包这段【同步】写：LLM 已在上面 await 完，此闭包内不含任何 await（见 store/openStores.ts 的告诫）。
  // 接了共享连接才真开事务；没接（各开各连接的测试）走 noopTransaction = 直接跑，行为同旧。
  const runTx = deps.transaction ?? noopTransaction;
  const mutation = runTx(() => {
    const now = (deps.clock ?? systemClock)().toISOString();
    const created: Cognition[] = [];
    let reinforced = 0;
    let corrected = 0;
    let conflicted = 0;

    // new
    for (const c of out.new ?? []) {
      const p = pickCognition(c);
      if (!p) continue;
      const support = pickSupport(citedIds(c));
      if (support.length === 0) continue; // 无可溯源原话 → 跳过（不落无溯源认知，地基债 fork 决策）
      const formedBy = resolveFormedBy(p.modelSaysInferred, support); // v0.6 Phase 3·D-0035：载体维由代码从证据算，不听模型
      const confidence = computeConfidence({ contentType: p.contentType, formedBy, supportCount: support.length, contradictCount: 0 }, deps.config);
      created.push(
        deps.cognitionStore.put({
          subjectId,
          content: p.content,
          contentType: p.contentType,
          formedBy,
          confidence,
          credStatus: deriveCredStatus(confidence, 0, p.contentType, deps.config),
          evidence: support.map((id) => ({ evidenceId: id, relation: 'support' as const })),
        }),
      );
    }

    // reinforce
    for (const c of out.reinforce ?? []) {
      const cog = c.cognition_id ? deps.cognitionStore.get(c.cognition_id) : null;
      if (!cog || cog.invalidAt) continue;
      const cited = pickSupport(citedIds(c));
      if (cited.length === 0) continue; // 没引到有效原话 → 无操作（地基债 fork 决策）
      const already = new Set(deps.cognitionStore.sourcesOf(cog.id).map((s) => s.evidenceId));
      const add = cited.filter((id) => !already.has(id));
      if (add.length) deps.cognitionStore.addEvidence(cog.id, add.map((id) => ({ evidenceId: id, relation: 'support' as const })));
      const links = deps.cognitionStore.sourcesOf(cog.id);
      const supportCount = links.filter((l) => l.relation === 'support').length;
      const contradictCount = links.filter((l) => l.relation === 'contradict').length;
      // confirmed→stated 升级（D-0033）:附和产的 confirmed 认知,只有被【用户主动亲口】印证才破 480 封顶
      //   ——"AI 带你确认的永远低档,你自己捅出来的才够比较确定"。触发判据(双重、缺一不可):
      //   ① LLM 把这次 reinforce 判为 formed_by='stated'(与 new 路同款语义分类);
      //   ② 引用的支撑原话里【有 spoken 来源】(结构护栏:observed/tool/inferred 永不能升 stated)。
      //   纯附和(LLM 判 confirmed / 未标 stated)永不触发 → 诱导提问风暴 + 连答"是的"洗不上去。
      //   分数仍由规则算(铁律 3b:formedBy 是分类、confidence 由 computeConfidence 算)。
      let formedBy = cog.formedBy;
      if (
        formedBy === 'confirmed' &&
        c.formed_by === 'stated' &&
        cited.some((id) => deps.evidenceStore.get(id)?.sourceKind === 'spoken')
      ) {
        formedBy = 'stated';
      }
      const confidence = computeConfidence({ contentType: cog.contentType, formedBy, supportCount, contradictCount }, deps.config);
      deps.cognitionStore.update(cog.id, {
        confidence,
        credStatus: deriveCredStatus(confidence, contradictCount, cog.contentType, deps.config),
        ...(formedBy !== cog.formedBy ? { formedBy } : {}),
      });
      reinforced++;
    }

    // correct：旧失效保留，采纳新的
    for (const c of out.correct ?? []) {
      const old = c.cognition_id ? deps.cognitionStore.get(c.cognition_id) : null;
      const p = pickCognition(c);
      if (!old || old.invalidAt || !p) continue;
      const support = pickSupport(citedIds(c));
      if (support.length === 0) continue; // 纠正后的新认知也要可溯源，否则跳过（不动旧的）
      deps.cognitionStore.update(old.id, { invalidAt: now }); // 标失效、保留可溯源
      const formedBy = resolveFormedBy(p.modelSaysInferred, support); // v0.6 Phase 3·D-0035：同 new 路，载体维由代码算
      const confidence = computeConfidence({ contentType: p.contentType, formedBy, supportCount: support.length, contradictCount: 0 }, deps.config);
      created.push(
        deps.cognitionStore.put({
          subjectId,
          content: p.content,
          contentType: p.contentType,
          formedBy,
          confidence,
          credStatus: deriveCredStatus(confidence, 0, p.contentType, deps.config),
          evidence: support.map((id) => ({ evidenceId: id, relation: 'support' as const })),
        }),
      );
      corrected++;
    }

    // conflict：标记暴露，不消解
    for (const c of out.conflict ?? []) {
      const cog = c.cognition_id ? deps.cognitionStore.get(c.cognition_id) : null;
      if (!cog || cog.invalidAt) continue;
      const contra = pickSupport(citedIds(c));
      if (contra.length === 0) continue; // 没引到冲突原话 → 不凭空标冲突（无操作，地基债 fork 决策）
      const already = new Set(deps.cognitionStore.sourcesOf(cog.id).map((s) => s.evidenceId));
      const add = contra.filter((id) => !already.has(id));
      if (add.length) deps.cognitionStore.addEvidence(cog.id, add.map((id) => ({ evidenceId: id, relation: 'contradict' as const })));
      deps.cognitionStore.update(cog.id, { credStatus: 'conflicted' });
      conflicted++;
    }

    // 语义解析落库（v0.6 Phase 2·D-0034）：对每条【用户真说的】证据落一份 resolution。
    //   规整 / 收窄 / 枚举收敛已在上方 `resolutionOf` 做完（3d + 来源收窄见那里）——**落库与派生共用那一份**，
    //   两处各自校验必然漂移。这里只负责 store：幂等（同证据已有解析则跳过）+ resolverVersion 绑 prompt 版本可追溯。
    //   resolved_content 是【解释结果、不是证据】，永不进 consolidate 的 support 白名单（3a）。
    //   **v0.6 Phase 3 起**：同一份解析还喂 `resolveFormedBy` 算认知的载体维（见上方声明）——
    //   即「先 produce → 当场派生 → 最后 store」，落库晚于派生是刻意的（派生不能等写完才算）。
    for (const [eid, r] of resolutionOf) {
      if (deps.semanticResolutionStore?.ofEvidence(eid)) continue; // 幂等：同证据不重复落
      deps.semanticResolutionStore?.put({
        evidenceId: eid,
        resolvedContent: r.resolvedContent,
        responseAct: r.responseAct,
        promptAct: r.promptAct,
        propositionOrigin: r.propositionOrigin,
        assertionStrength: r.assertionStrength,
        requiredContext: r.requiredContext,
        resolverVersion: `consolidate@${CONSOLIDATE_PROMPT.version}`,
      });
    }
    deps.eventStore.markConsolidated(newEvents.map((e) => e.id));
    return { created, reinforced, corrected, conflicted };
  });

  // 写路径仪表：profileSize = 本轮注入 prompt 的 active 认知条数（existing 就是拼进 prompt 的那份画像）。
  return { ...mutation, processedEvents: newEvents.length, llmCalls, profileSize: existing.length, promptChars };
}
