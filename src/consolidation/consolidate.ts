/**
 * 画像生成 · 增量更新（写路径）。实现增量更新与反证处理。
 *
 * 处理【未消化的新事件】，连同【现有画像】给 LLM，判断新事件对画像意味着什么：
 *   - new      新事件里有、画像没有 → 新增认知。
 *   - reinforce 新事件印证现有认知 → 补证据、置信升。
 *   - correct  用户在新事件里【明确纠正】现有认知 → 旧的标失效保留、采纳新的。
 *   - conflict 矛盾但非明确纠正（如行为 vs 旧偏好）→ 标 conflicted，两条都留，暴露不消解（public contract）。
 *
 * 纪律：MemoWeft 自算把握度（不采信 LLM 自报）；推测低置信；旧判断失效保留可溯源（public contract）。
 */
import type { EventStore } from '../event/store.ts';
import type { EvidenceStore } from '../evidence/store.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { Cognition, ContentType, FormedBy } from '../cognition/model.ts';
import type { Evidence, SourceKind } from '../evidence/model.ts';
import type { SemanticResolutionStore } from '../interaction/semanticResolutionStore.ts';
import type {
  ResponseAct,
  PromptAct,
  PropositionOrigin,
  AssertionStrength,
} from '../interaction/model.ts';
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import type { Embedder } from '../retrieval/embedder.ts';
import { computeConfidence, deriveCredStatus, isHedgedStated } from './confidence.ts';
import { deriveFormedBy } from './deriveFormedBy.ts';
import { filterReadableByTier } from '../evidence/privacy.ts';
import { sourceLabel, aiContextSuffix } from '../evidence/sourceLabel.ts';
import { parseJsonObjectWithRepair } from '../llm/jsonRepair.ts';
import { resolveEchoedId } from '../llm/echoedId.ts';
import { noopTransaction, type Transaction } from '../store/transaction.ts';
import { resolveLang, type Lang, type MemoWeftConfig } from '../config.ts';
import { systemClock, type Clock } from '../clock.ts';
import { CONSOLIDATE_PROMPT } from './prompts.ts';

export interface ConsolidateDeps {
  eventStore: EventStore;
  /** 证据层：证据级溯源需要将原话文本提供给 LLM。 */
  evidenceStore: EvidenceStore;
  cognitionStore: CognitionStore;
  /** 语义解析 store（**可选**）：接了则把每条证据的语义解析落 semantic_resolution 表。
   *  **载体维派生仍不读表** —— `deriveFormedBy` 用的是本次 LLM 产的解析（内存里的 `resolutionOf`）。
   *  但 **hedged（含糊自述封顶）的重算期派生要读表**：reinforce/conflict 按【全链】重算把握度，
   *  链上多是既往轮次的历史证据，它们的解析只存在于表里（见下方 `resolveHedged`）。
   *  不接它 ⇒ 历史证据那部分查不出解析，按「解析不出就不臆造惩罚」退化成不封顶：
   *  方向保守（可能少封顶，绝不会凭空封顶），但重算期的封顶会静默失真——**生产接线务必接上**。
   *  （原注释以「唯一要读历史解析的场景是 reinforce 的升级路、而升级路已取消」论证本依赖恒可选；
   *   该理由自 hedged 接入起不再成立，故一并订正，不留骗后人的契约说明。） */
  semanticResolutionStore?: SemanticResolutionStore;
  llm: LLMClient;
  /** 事务器（可选）：接了共享连接就传它，把下方多步写（new/correct/conflict/reinforce + markConsolidated）
   *  原子化——中途失败时整段回滚，不留下部分画像。不传（如各开各连接的测试）= 直接执行。见 store/openStores.ts。 */
  transaction?: Transaction;
  /** 可注入配置（配置去单例）：不传 = 用全局单例，行为同旧；传了则把握度阈值等按这份算。 */
  config?: MemoWeftConfig;
  /** 可注入时钟：correct/conflict 分支的显式时间戳走它；缺省真实系统时间。 */
  clock?: Clock;
  /** A5「矛盾画像可并存」护栏（**可选**·混合方案）：接了 `embedder` 才启用。
   *  new 分支入库【前】，对内存里的现有画像（existing）算嵌入余弦筛出【同主题】候选，
   *  再对少量候选用 `llm` 做一次聚焦【极性判断】；命中「相似且极性相反」的候选，
   *  【不新建那条相反行】，改走 conflict 语义（把这条反转证据挂 contradict 到旧认知、
   *  按全链重算 confidence、由 deriveCredStatus 判 conflicted/contested）——把本会静默并存的
   *  矛盾拉回「冲突保持可见」。**不接 = 空命中 = 行为完全同旧**（护住 tests/eval 与既有 call site）。
   *  纯确定性做不到：相似度可确定性化，但「极性相反」余弦判不出（对否定不敏感），必然要一次 llm 判。
   *  依赖详见 `_workflow-docs/design/a5-guardrail-impact-2026-07-25.md`。 */
  contradictionGuard?: {
    embedder: Embedder;
    /** shortlist 余弦阈值（只对 vector 余弦有语义）；缺省 GUARD_DEFAULT_SIMILARITY。 */
    minSimilarity?: number;
    /** 每条候选最多做几次极性判（按相似度降序取前 N）；缺省 GUARD_DEFAULT_TOPK。 */
    topK?: number;
  };
}

export interface ConsolidateResult {
  created: Cognition[];
  reinforced: number;
  corrected: number;
  conflicted: number;
  processedEvents: number;
  llmCalls: number;
  /** 写路径仪表（计量契约 · 只观测，不改变行为）：本轮注入 prompt 的 active 认知条数（= 现有画像大小）。
   *  0 = consolidate 未执行（无新事件早退）。供集成测试记录写路径规模。 */
  profileSize: number;
  /** 写路径仪表（计量契约 · 只观测，不改变行为）：本轮 buildMessages 产物全部 content 的字符数之和（prompt 多大）。
   *  0 = consolidate 未执行（无新事件早退）。供集成测试记录 prompt 规模。 */
  promptChars: number;
  /** 写路径仪表（计量契约 · 只观测，不改变行为）：`content_type` 落 `fact` 兜底的次数，按触发因分。
   *
   *  为什么要分因：三者严重度不同，混计就判断不了该不该动语义。
   *  - `missing`    模型没给该字段；
   *  - `invalid`    给了但不在 `VALID_TYPES` 六值内（拼错 / 幻觉值）；
   *  - `outOfScope` 给了 `hypothesis` / `trend` —— 这两值在 `ContentType`（cognition/model.ts）里
   *    **合法**，只是 consolidate 不收。这是三者里唯一的【语义降级】：本应受
   *    `attribution.hypothesisCap`(250) 与 2 天半衰期约束、且应进 proposeAsk 求证队列的推测，
   *    被兜成 `fact`（永不衰减、永不自动失效、不受 transientCap 封顶）并永久退出该队列。
   *
   *  兜底行为本身**未改动**，本字段只是让它不再无声。全 0 = 本轮模型给的类型都合法（或未执行）。 */
  contentTypeFallback: { missing: number; invalid: number; outOfScope: number };
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
  // 注：原有 `formed_by?` 的 confirmed→stated 升级路径已移除，
  //   升级路已**删除**——它已无消费者。破顶改由 reinforce 分支里的【并存新认知】兑现。
}
/** 一条候选语义解析（current public contract）：LLM 对某条原话的解析（这句在回应谁提出的什么、是肯定/否定/选择/含糊）。
 *  字段全 string 容错；落库前经 VALID_* 收敛（非法值落 null）。**解析结果是解释、不是证据**，永不进入 support 白名单。 */
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

/** 前缀容错的最短长度：短于此一律不猜。`ev-1`（提示词示例的字面占位）绝不能撞上真证据。 */
const MIN_ID_PREFIX = 8;

/**
 * 把模型写回的 evidence id 解回【白名单内的真 id】；解不出返回 null。
 *
 * 精确匹配优先。失败时才做【唯一前缀】容错：部分模型会把 36 字符 UUID 截成前 8 位，
 * 或输出 `ev-<前8位>`。这些值仍可在不放宽白名单的前提下安全解析。
 *
 * 安全边界：只可能解到白名单【内】的 id，且必须**唯一命中**——
 * 捏造 id（非任何真 id 前缀）、歧义前缀（命中多条）、过短前缀一律 null。宁可不记，不可记错。
 * 见 tests/evidenceIdTruncation.test.ts（前半钉「真 id 别被误杀」、后半钉「护栏不许松」），
 * 与 confirmedLaundering.test.ts 的助手输出排除用例互补。
 */
function resolveEvidenceId(
  raw: string | undefined,
  whitelist: Set<string>,
  tagToEvidenceId: Map<string, string>,
): string | null {
  if (!raw) return null;
  // ① 标号：prompt 里发的就是 `e1`/`e2`，模型照抄即可。
  //    仍过 whitelist —— 标号只在本次 prompt 内有效，且 resolutions 走更窄的 spokenEvidence。
  const byTag = tagToEvidenceId.get(raw.trim());
  if (byTag && whitelist.has(byTag)) return byTag;
  // ② 精确 UUID（向后兼容 + 模型偶尔照抄了别处的真 id）。
  if (whitelist.has(raw)) return raw;
  // ③ 前缀兜底：兼容模型截断 UUID 的情况，作为防御性解析路径。
  const bare = raw.replace(/^ev-/i, ''); // 剥掉照抄旧示例的 `ev-` 前缀
  if (bare.length < MIN_ID_PREFIX) return null;
  let hit: string | null = null;
  for (const id of whitelist) {
    if (!id.startsWith(bare)) continue;
    if (hit !== null) return null; // 歧义 → 不猜
    hit = id;
  }
  return hit;
}

const VALID_TYPES = ['fact', 'preference', 'goal', 'project', 'state', 'trait'];
/** `ContentType` 里合法、但 consolidate 不收的两值：只能由 attribute / trends 内部产生。
 *  模型仍可能吐出它们（现有画像以 `- [id] (contentType) content` 回灌 prompt，标签它看得见），
 *  故单列一因与拼写错误区分。 */
const OUT_OF_SCOPE_TYPES = ['hypothesis', 'trend'];
type TypeFallbackReason = 'missing' | 'invalid' | 'outOfScope';

/** 判定本条 content_type 走没走兜底、因为什么。纯函数，不改变任何行为。 */
function classifyTypeFallback(raw: string | undefined): TypeFallbackReason | null {
  if (raw === undefined || raw === null || raw === '') return 'missing';
  if (VALID_TYPES.includes(raw)) return null;
  return OUT_OF_SCOPE_TYPES.includes(raw) ? 'outOfScope' : 'invalid';
}
const VALID_FORMED = ['stated', 'observed', 'ruled', 'confirmed', 'inferred'];
// 语义解析枚举（current public contract）：落库前收敛，非法值落 null（与 model.ts 的 `... | null` 一致）。
const VALID_RESPONSE_ACT = ['affirm', 'negate', 'select', 'elaborate', 'ask', 'none', 'other'];
const VALID_PROMPT_ACT = ['propose', 'ask', 'state', 'none', 'other'];
const VALID_ORIGIN = ['user_stated', 'assistant_proposed'];
const VALID_STRENGTH = ['explicit', 'weak', 'none'];

/**
 * 从原始候选里抽出认知（容错字段名 + 缺类型给保守默认 fact）。无内容返回 null。
 *
 * **不再算 formedBy**：载体维（stated/confirmed/observed = 这条信息是
 * 谁的话）由 `deriveFormedBy` 从支持证据算、**模型说了不算**；模型只保留「这条是不是我推断出来的」
 * 这一维（推断距离），即 `modelSaysInferred` —— 那是往低了报、无骗人动机的一维，且只有它知道。
 */
function pickCognition(c: RawCog): {
  content: string;
  contentType: ContentType;
  modelSaysInferred: boolean;
  /** 计量用（不改变行为）：本条走没走 content_type 兜底、因为什么。null = 类型合法。见 ConsolidateResult.contentTypeFallback。 */
  typeFallback: TypeFallbackReason | null;
} | null {
  const content = (c.content ?? c.new_content ?? c.cognition ?? '').trim();
  if (!content) return null;
  const typeFallback = classifyTypeFallback(c.content_type);
  const contentType = (
    VALID_TYPES.includes(c.content_type ?? '') ? c.content_type : 'fact'
  ) as ContentType;
  // 模型只负责「这条是不是我推断出来的」这一维。**缺 / 非法值 → 保守当推断**：
  //   模型漏标时，旧实现兜底成 inferred(200)。若改成「缺失 = 不是推断 → 走载体维」，一条 spoken 证据
  //   就会把漏标的推断型认知抬到 stated(600)。缺 formed_by 时保守当 inferred，
  //   不默认高置信亲述，保持该约束的语义。
  //   ⇒ 只有模型【明确填了合法值且不是 inferred】，才认为它主张「这条是从原话直接得到的」，进而走载体维。
  const declared = VALID_FORMED.includes(c.formed_by ?? '') ? c.formed_by : null;
  return {
    content,
    contentType,
    modelSaysInferred: declared === null || declared === 'inferred',
    typeFallback,
  };
}

/** 提供给 LLM 的事件视图：事件摘要 + 其下逐条原话（带证据 id 供引用）。 */
interface EventView {
  summary: string;
  occurredAt: string;
  utterances: Array<{ id: string; text: string }>;
}

/**
 * 拼 prompt，并返回【原话标号 → 真 evidence id】的映射。
 *
 * **原话不再发 36 字符 UUID，改发短标号 `e1`/`e2`…**（`tagToEvidenceId` 记住对应关系，
 * 落库前翻译回真 id）。短标号让输入与示例形态一致，避免模型改写或截断 UUID；
 * 唯一前缀解析继续作为兼容性兜底。
 *
 * 认知 id 仍保持 UUID 原样；两种 id 形态不同，也能降低混淆风险。
 */
function buildMessages(
  existing: Cognition[],
  events: EventView[],
  lang: Lang,
): { messages: ChatMessage[]; tagToEvidenceId: Map<string, string> } {
  const zh = lang === 'zh';
  const profile = existing.length
    ? existing.map((c) => `- [${c.id}] (${c.contentType}) ${c.content}`).join('\n')
    : zh
      ? '（空）'
      : '(none)';
  const tagToEvidenceId = new Map<string, string>();
  let n = 0;
  const material = events
    .map((e) => {
      const head = `· ${zh ? '事件' : 'Event'} (${e.occurredAt.slice(0, 16)}) ${e.summary}`;
      const lines = e.utterances
        .map((u) => {
          const tag = `e${++n}`; // 跨事件连续编号：模型看到的是一份扁平的原话清单
          tagToEvidenceId.set(tag, u.id);
          return `    - [${tag}] ${u.text}`;
        })
        .join('\n');
      return lines ? `${head}\n${lines}` : head;
    })
    .join('\n');
  const body = zh
    ? `【现有画像】：\n${profile}\n\n【新材料】：\n${material}`
    : `[Existing profile]:\n${profile}\n\n[New material]:\n${material}`;
  return {
    messages: [
      { role: 'system', content: CONSOLIDATE_PROMPT.text[lang] },
      { role: 'user', content: body },
    ],
    tagToEvidenceId,
  };
}

// ── A5 矛盾并存护栏辅助（模块级纯函数）──────────────────────────────
/** shortlist 余弦阈值缺省：bge-m3 CLS 池化下同主题句常 ≥0.6；只对 vector 余弦有语义。 */
const GUARD_DEFAULT_SIMILARITY = 0.6;
/** 每条候选最多做几次极性判（相似度降序前 N）：把 llm 调用量压到很小。 */
const GUARD_DEFAULT_TOPK = 3;

/** 余弦相似度；任一为零向量或维度不齐返回 0（与 vectorRetriever 内部同口径）。 */
function cosine(a: readonly number[], b: readonly number[]): number {
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
 * 极性判断（护栏第二半，非确定性）：候选新认知是否【直接反驳/反转】某条现有认知（同一主体）。
 * 只判「相反」，不判「相关」——相似度已把候选压到同主题，这里专问极性。保守：拿不准判 false（不拦，避免误伤）。
 */
async function judgeContradiction(
  llm: LLMClient,
  lang: Lang,
  existingContent: string,
  candidateContent: string,
): Promise<boolean> {
  const zh = lang === 'zh';
  const sys = zh
    ? '你判断关于【同一个人】的两条陈述是否【直接矛盾】——即一条断言了另一条的相反面（如"爱喝咖啡"vs"不再喝咖啡"、"想考研"vs"放弃考研"）。仅当逻辑互斥时才算矛盾；只是不同话题、程度差别、或随时间自然递进（同方向）都【不算】。只输出 JSON：{"contradicts": true|false}。拿不准输出 false。'
    : 'Decide whether two statements about the SAME person DIRECTLY contradict each other — i.e. one asserts the opposite of the other (e.g. "loves coffee" vs "no longer drinks coffee"). Only logically incompatible = contradiction; different topics, degree differences, or same-direction evolution over time do NOT count. Output only JSON: {"contradicts": true|false}. If unsure, output false.';
  const user = zh
    ? `现有认知：${existingContent}\n新认知：${candidateContent}\n它们矛盾吗？`
    : `Existing: ${existingContent}\nNew: ${candidateContent}\nDo they contradict?`;
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

export async function consolidate(
  subjectId: string,
  deps: ConsolidateDeps,
): Promise<ConsolidateResult> {
  const newEvents = deps.eventStore.unconsolidated(subjectId);
  const empty: ConsolidateResult = {
    created: [],
    reinforced: 0,
    corrected: 0,
    conflicted: 0,
    processedEvents: 0,
    llmCalls: 0,
    profileSize: 0,
    promptChars: 0,
    contentTypeFallback: { missing: 0, invalid: 0, outOfScope: 0 },
  };
  if (newEvents.length === 0) return empty;

  // 现有画像 = active()（未失效且未归档）：已归档认知不进入
  // 「现有画像」prompt、不参与强化/纠正/冲突比对（数据保留、可经恢复归档重新生效）。
  const existing = deps.cognitionStore.active(subjectId);
  // reinforce/correct/conflict 引用【已有认知】的 cognition_id 白名单：
  //   这三条路径使用精确匹配，并保留唯一前缀兜底；无法解析的 id 会触发告警。
  const existingIds = new Set(existing.map((c) => c.id));
  const resolveCogId = (rawId: string | undefined, op: string): string | null => {
    const id = resolveEchoedId(rawId, existingIds);
    if (rawId && !id) {
      console.warn(
        `[memoweft/consolidate] ${op} 引用的 cognition_id 认不出（模型写的：${JSON.stringify(rawId)}）——多半是它把 id 截断了`,
      );
    }
    return id;
  };

  // 事件视图 + 合法原话集合：把每个新事件覆盖的原话（带 id+原文）摊开给 LLM 引用；
  // 只有这些 id 是合法支撑（防 LLM 编造/自证，证据级溯源）。
  const validEvidence = new Set<string>();
  /** 合法原话里【来源=spoken】的子集（public contract）：resolutions 只对【用户真说出口的话】落解析。
   *  [行为观察]/[工具返回] 不是用户在说话——「这句在回应谁提出的什么、是肯定还是否定」对一条行为观察
   *  本就无意义；若持久化会污染 deriveFormedBy 读取的语义解析数据。
   *  提示词也教了同样的收窄，但**这里是结构保证**：模型不听话也进不了表（同 validEvidence 的思路——
   *  纪律靠结构、不靠提示词自觉）。 */
  const spokenEvidence = new Set<string>();
  /** 过了隐私门的证据 id → 派生载体维要的两样（privacy default）：来源 + 它的 AI 上一句。
   *  在下面构建 events 时顺手填（那里本就查了这两样），避免为每条支撑再查一次 store。 */
  const carrierOf = new Map<
    string,
    { sourceKind: SourceKind; precedingAiContext: string | null }
  >();
  // 隐私关（按当前写模型 tier）+ 推理门：事件覆盖的原话里，只将【当前模型可读】且【可推画像】的内容提供给 LLM，
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
        // 来源感知：原话带来源前缀，让 LLM 定 formedBy 时知道哪些不是用户亲口。
        // 附和/AI 上下文:把 preceding_ai_context【追加进本原话的 text 后缀】
        //   (经专用只读 precedingAiContextOf,只对已过隐私门的证据取)——让 LLM 看懂"是的"这类孤儿回应
        //   在附和 AI 提出的什么命题，据此判 formed_by=confirmed。**关键：AI 上文只是 text 后缀、
        //   共用这条【真证据】的 id,绝不铸独立 {id,text} 条目 → validEvidence 只含真证据 id、AI 上文永无 id
        //   → pickSupport 结构性引不到 AI 上文 → 助手输出永不成为可溯源证据**。缺省无 → 后缀空 = no-op。
        return {
          id: e.id,
          text: sourceLabel(e.sourceKind, lang) + e.rawContent + aiContextSuffix(aiCtx, lang),
        };
      });
    return { summary: ev.summary, occurredAt: ev.occurredAt, utterances };
  });
  /** 取候选引的原话 id，只留合法的、去重（无合法引用 → 空，调用方按"跳过"处理）。
   *  经 `resolveEvidenceId` 归一：精确匹配优先，模型截断的短 id 按【唯一前缀】解回真 id（见其文档）。 */
  const pickSupport = (ids: string[]): string[] => [
    ...new Set(
      ids
        .map((id) => resolveEvidenceId(id, validEvidence, tagToEvidenceId))
        .filter((id): id is string => id !== null),
    ),
  ];

  // 结构化输出加固（jsonRepair）：去代码块围栏 → 解析对象；失败落日志 + 最多重试一次（提示"只输出 JSON"）。
  // 仍失败 → null（按"本轮无产出"处理，等价旧的返回空对象；下方各 `?? []` 兜住）。重试会多调一次模型，故计数取前后差。
  // 写路径仪表（runtime metric）：先保存 messages 并计算 prompt 字符数，再原样传给解析器；仅增加计量，不改变行为。
  const { messages, tagToEvidenceId } = buildMessages(existing, events, lang);
  const promptChars = messages.reduce((n, m) => n + m.content.length, 0);
  const before = deps.llm.callCount;
  const out =
    (await parseJsonObjectWithRepair<LLMOut>({
      llm: deps.llm,
      messages,
      lang,
    })) ?? {};
  const llmCalls = deps.llm.callCount - before;

  /**
   * 本轮 LLM 产的语义解析，规整 + 收窄后按 evidenceId 索引（public contract）。
   * **落库（下方）与派生（deriveFormedBy）共用这一份**——两处各自校验必然漂移。
   * 收窄同落库白名单：只收【用户真说的】真证据（`spokenEvidence` ⊆ `validEvidence`），同时保证来源范围有效；
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
    // 归一到真 id（同 pickSupport）：白名单仍是 spokenEvidence（来源收窄，见其声明）——
    // 容错只把模型截断的短 id 解回【集合内】的真 id，解不出 / 歧义一律丢，白名单一寸不放宽。
    // **必须用解出的真 id 做 key**：下方落库直接拿 key 写 evidence_id，短 id 进表就是脏数据。
    const eid = resolveEvidenceId(r.evidence_id, spokenEvidence, tagToEvidenceId);
    if (!eid) continue;
    const resolved = (r.resolved_content ?? '').trim();
    if (!resolved) continue; // resolved_content 非空
    if (resolutionOf.has(eid)) continue; // 同证据先到先得
    resolutionOf.set(eid, {
      resolvedContent: resolved,
      responseAct: VALID_RESPONSE_ACT.includes(r.response_act ?? '')
        ? (r.response_act as ResponseAct)
        : null,
      promptAct: VALID_PROMPT_ACT.includes(r.prompt_act ?? '') ? (r.prompt_act as PromptAct) : null,
      propositionOrigin: VALID_ORIGIN.includes(r.proposition_origin ?? '')
        ? (r.proposition_origin as PropositionOrigin)
        : null,
      assertionStrength: VALID_STRENGTH.includes(r.assertion_strength ?? '')
        ? (r.assertion_strength as AssertionStrength)
        : null,
      requiredContext: (r.required_context ?? '').trim() || null,
    });
  }

  /**
   * 覆盖率仪表：模型产了解析、却一条都没落地，通常表示它写回的 evidence_id 无法解析。
   * JSON 合法与调用成功不足以发现这类问题，因此保留一条不含用户原文的诊断信号。
   *
   * **刻意只抓「产了但全没落地」**：「模型压根没产 resolutions」不告警——那是模型能力/材料问题，
   * 也是简化测试 stub 的常见形态（见 writePathMetrics.test.ts），为此告警会产生大量无效噪声。
   * 「部分没落地」同样不告警：来源收窄 / resolved_content 空 / 同证据先到先得都会合法地挡掉一些。
   *
   * **只观测、不改行为**：仍按原样标 consolidated。要改成「没覆盖就不标、留待下次」得给 event 加
   * 重试计数（schema 变更 + 防死循环）属于独立的兼容性变更，不在此处处理。
   * 隐私：只记 id 与计数，**不记原话**（同 jsonRepair.ts:67-69 的取向；id 不是用户内容）。
   */
  const rawResolutionCount = (out.resolutions ?? []).length;
  if (rawResolutionCount > 0 && resolutionOf.size === 0 && spokenEvidence.size > 0) {
    const sample = (out.resolutions ?? [])
      .slice(0, 3)
      .map((r) => JSON.stringify(r.evidence_id))
      .join(' ');
    console.warn(
      `[memoweft/consolidate] 模型产了 ${rawResolutionCount} 条解析、却一条都没落地` +
        `（spoken 证据 ${spokenEvidence.size} 条、llmCalls=${llmCalls}）——` +
        `多半是它写回的 evidence_id 认不出。模型写的前几个：${sample}`,
    );
  }

  /**
   * 算一条认知的 formedBy（public contract）。
   * 模型报「我推断的」→ `inferred`（推断距离这一维仍归模型，代码只接管载体维）；
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

  /**
   * 算一条认知的 hedged（含糊自述封顶的判据；public contract）。
   *
   * 与载体维【正交】：载体维答「谁的话」（`resolveFormedBy`，本函数完全不参与它的计算），
   * hedged 答「这话说得含不含糊」。用户主动说的含糊断言（「我可能不太会做饭」）连命题边界
   * 都没定，此前与「我是素食者」同拿 stated 底分 600；而点头认 AI 猜测的含糊只有 confirmed 280
   * ——保守性倒置。封顶动作本身在 `computeConfidence` 里（min(hedgeCap)），这里只出判据。
   *
   * **为什么是「库优先、内存兜底」而不是反过来**：文末的解析落库循环对「库里已有解析」的证据
   * **跳过写入**（幂等），所以**库里那份才是会活下来的那份**。若让内存赢，就会出现：本轮按内存
   * 那份（马上要被丢弃的）算出 600，而此后每一次重算读到库里那份旧的算出 280 —— 同一条认知在
   * 600/280 之间**永久分叉**。可达路径：同一条证据挂在两个事件上而其中一个尚未消化；
   * importBundle 还原未消化事件时库里已有该证据的解析。故：库里有就以库为准，内存只补库里还没有的
   * （= 本轮新证据，它们的解析此刻确实还没落库——落库循环在四个写循环之后）。
   *
   * **store 缺席时的退化边界**（`semanticResolutionStore` 是可选依赖）：历史证据查不出解析，
   * 按「解析不出不臆造惩罚」当作没有该维 ⇒ 可能少封顶，绝不会凭空封顶。本轮证据的 hedged
   * 仍然准确（走内存那半边），静默失效面被压到只剩历史证据部分。
   *
   * ⚠ **不变式（漏了必错）**：`supportIds` 恒等于同一调用点传给 `supportCount` 的那一个集合。
   * reinforce 重算传【全链】，并存新认知传【本轮 add】——两处 support 集合定义本就不同（见并存路
   * 的论证）。若在并存路误用全链，链上旧的附和证据会让「没有 explicit」不成立，封顶永不触发。
   */
  const resolveHedged = (formedBy: FormedBy, supportIds: readonly string[]): boolean => {
    // 非 stated / 空支持集恒 false（`isHedgedStated` 同判）——提前挡掉，省下重算期那次查表。
    if (formedBy !== 'stated' || supportIds.length === 0) return false;
    /** 回查只需这两维：`isHedgedStated` 不看 sourceKind/precedingAiContext（那是载体维才要的），
     *  所以重算期不必碰 evidenceStore/carrierOf——这正是本方案能落地的结构原因。 */
    const stored = new Map<
      string,
      { propositionOrigin: PropositionOrigin | null; assertionStrength: AssertionStrength | null }
    >();
    // 一次批量查（走 ix_semres_evidence），不逐条 ofEvidence。
    for (const r of deps.semanticResolutionStore?.forEvidenceIds([...supportIds]) ?? [])
      stored.set(r.evidenceId, r);
    // 库优先、内存兜底（理由见上：落库幂等 ⇒ 库里那份才是活下来的那份）。
    const view = supportIds.map((id) => stored.get(id) ?? resolutionOf.get(id) ?? null);
    return isHedgedStated(formedBy, view);
  };

  // ── A5 矛盾并存护栏：进事务【前】(async) 预算「哪些 new 候选其实是对某条现有认知的极性反转」。
  //   相似度筛(嵌入余弦) → 对少量同主题候选做一次极性判(llm)。命中 → 记 {new 候选下标 → 被反转的旧认知 id}。
  //   下方 new 循环据此把命中候选改走 conflict（挂 contradict）、不新建相反行。
  //   不接 embedder / 无现有画像 / 无候选 → 空 Map = 行为完全同旧。嵌入与极性判都是 async，
  //   故【必须】在 runTx 同步闭包之前算完（闭包内不含 await 的写约束）。
  const guardHits = await (async (): Promise<Map<number, string>> => {
    const hits = new Map<number, string>();
    const guard = deps.contradictionGuard;
    const candidates = out.new ?? [];
    if (!guard || existing.length === 0 || candidates.length === 0) return hits;
    const threshold = guard.minSimilarity ?? GUARD_DEFAULT_SIMILARITY;
    const topK = guard.topK ?? GUARD_DEFAULT_TOPK;
    // 只对【会真落库】的候选（有内容 + 有可溯源支撑）算，下标与下方 new 循环严格对齐。
    const cands: Array<{ idx: number; content: string }> = [];
    candidates.forEach((c, idx) => {
      const p = pickCognition(c);
      if (!p) return;
      if (pickSupport(citedIds(c)).length === 0) return;
      cands.push({ idx, content: p.content });
    });
    if (cands.length === 0) return hits;
    let candVecs: number[][];
    let existVecs: number[][];
    try {
      [candVecs, existVecs] = await Promise.all([
        guard.embedder.embed(cands.map((x) => x.content)),
        guard.embedder.embed(existing.map((e) => e.content)),
      ]);
    } catch (e) {
      // 嵌入端点挂了不该拖垮固化：显式记日志、退化成「本轮不拦」（不静默改数据）。
      console.warn(
        `[memoweft/consolidate] A5 护栏嵌入失败，本轮跳过护栏：${e instanceof Error ? e.message : String(e)}`,
      );
      return hits;
    }
    for (let k = 0; k < cands.length; k++) {
      const shortlist = existing
        .map((e, j) => ({ e, score: cosine(candVecs[k] ?? [], existVecs[j] ?? []) }))
        .filter((x) => x.score >= threshold)
        .sort((a, b) => b.score - a.score)
        .slice(0, topK);
      for (const { e } of shortlist) {
        if (await judgeContradiction(deps.llm, lang, e.content, cands[k]!.content)) {
          hits.set(cands[k]!.idx, e.id); // 命中一条即够：这条候选改挂到该旧认知
          break;
        }
      }
    }
    return hits;
  })();

  // 事务化保证写路径一致性：new/correct/conflict/reinforce 与 markConsolidated
  // 要么全部提交、要么全部回滚，避免部分画像写入或事件被重复处理。
  // 只包这段【同步】写：LLM 已在上面 await 完，此闭包内不含任何 await（见 store/openStores.ts 的告诫）。
  // 接了共享连接才真开事务；没接（各开各连接的测试）走 noopTransaction = 直接跑，行为同旧。
  const runTx = deps.transaction ?? noopTransaction;
  const mutation = runTx(() => {
    const now = (deps.clock ?? systemClock)().toISOString();
    const created: Cognition[] = [];
    let reinforced = 0;
    let corrected = 0;
    let conflicted = 0;
    /** 计量（不改变行为）：只统计【实际落库】的兜底 —— 没落库的候选不构成危害，
     *  这样该计数可直接读作「库里有多少条认知的类型是靠兜底定的」。 */
    const typeFallback = { missing: 0, invalid: 0, outOfScope: 0 };

    /** 把「反证原话」挂到旧认知上并按【全链】重算把握度（conflict 分支与 A5 护栏共用同口径，
     *  避免 #19 的「只翻 credStatus 不重算 → contradictPenalty 空转」回归）。返回是否真挂上。
     *  formedBy 继承旧认知、不重派载体维（重算期约束）；credStatus 交 deriveCredStatus 在
     *  contradictCount>0 下判 conflicted/contested（保住「冲突只暴露、不消解」不变量）。 */
    const attachContradiction = (cogId: string, contraIds: readonly string[]): boolean => {
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
          hedged: resolveHedged(cog.formedBy, supportIds),
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
    };

    // new
    for (const [idx, c] of (out.new ?? []).entries()) {
      const p = pickCognition(c);
      if (!p) continue;
      const support = pickSupport(citedIds(c));
      if (support.length === 0) continue; // 无可溯源原话 → 跳过（不落无溯源认知，证据完整性规则）
      // A5 护栏命中：这条「new」其实是对某条现有认知的极性反转 → 不新建那条相反行，
      //   改把它的原话当反证挂到旧认知上（走 conflict 语义、把矛盾拉回「冲突可见」）。
      const guardOldId = guardHits.get(idx);
      if (guardOldId && attachContradiction(guardOldId, support)) {
        conflicted++;
        continue; // 不落新行、不计 typeFallback（未落库）
      }
      // 未命中护栏（或挂失败，如旧认知已失效）→ 走正常新建，别把这条丢了。
      if (p.typeFallback) typeFallback[p.typeFallback]++;
      const formedBy = resolveFormedBy(p.modelSaysInferred, support); // 载体维由代码从证据算，不听模型
      // 接线点 1/8 · new（形成期）：`resolutionOf` 就地可用，零查表成本。
      //   **这里必须接**：新建的含糊自述若不封顶，缺口就原封不动地留在最主要的入库路径上。
      const confidence = computeConfidence(
        {
          contentType: p.contentType,
          formedBy,
          supportCount: support.length,
          contradictCount: 0,
          hedged: resolveHedged(formedBy, support), // 集合恒同 supportCount 用的那一个（support）
        },
        deps.config,
      );
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
      const cogId = resolveCogId(c.cognition_id, 'reinforce');
      const cog = cogId ? deps.cognitionStore.get(cogId) : null;
      if (!cog || cog.invalidAt) continue;
      const cited = pickSupport(citedIds(c));
      if (cited.length === 0) continue; // 没引到有效原话 → 无操作（证据完整性规则）
      const already = new Set(deps.cognitionStore.sourcesOf(cog.id).map((s) => s.evidenceId));
      const add = cited.filter((id) => !already.has(id));
      if (add.length)
        deps.cognitionStore.addEvidence(
          cog.id,
          add.map((id) => ({ evidenceId: id, relation: 'support' as const })),
        );
      const links = deps.cognitionStore.sourcesOf(cog.id);
      // supportIds 与 supportCount 同源导出：hedged 的 support 集合必须与算 supportCount 的那一个
      //   完全相同（见 resolveHedged 的不变式），从结构上杜绝两者漂移。
      const supportIds = links.filter((l) => l.relation === 'support').map((l) => l.evidenceId);
      const supportCount = supportIds.length;
      const contradictCount = links.filter((l) => l.relation === 'contradict').length;
      // v0.6：**取消 confirmed→stated 的就地升级**。
      //   旧实现会在这里改写来源标签，触发条件是 ① LLM 自报 formed_by='stated'，且 ② 引用原话中有 spoken。
      //   取消载体维指令后，就地升级已不再有可靠输入；继续隐式改写会破坏原有来源语义。
      //   此外，就地改写来源标签与「冲突只暴露、不裁决」的契约相悖。
      //   因此 formedBy 恒继承；主动陈述通过下面的并存新认知表达。
      const formedBy = cog.formedBy;
      // 接线点 2/8 · reinforce（重算期）：hedged **不落库**，每次都从支持证据链重新派生。
      //   这里 formedBy 继承存量，一条已封顶 280 的含糊自述，只要用户再说一句相关的话触发 reinforce，
      //   不接就静默反弹回 600+支持加分——即「改证据后 hedged 蒸发」。
      //   全链里的历史证据靠 resolveHedged 回表补齐。
      const confidence = computeConfidence(
        {
          contentType: cog.contentType,
          formedBy,
          supportCount,
          contradictCount,
          hedged: resolveHedged(formedBy, supportIds),
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
      reinforced++;

      // 规则的另一半：旧认知是【附和来的】(confirmed)，而这次【新增】的证据是【用户主动说的】
      //   （载体维派生成 stated）→ 形成一条**并存的新 stated 认知**；旧的 confirmed 原样留档、标签不动。
      //   两条各自溯源清楚，读的人自己判断——而不是把一条认知的来源就地改写掉。
      //   **用 `add`（本次新增）而非 `cited` 派生**：cited 可能含旧的附和证据，取最弱会让它恒算 confirmed、
      //   并存永不触发。add 为空（幂等重跑 / 无新证据）自然不触发。
      //   ⚠ 已知代价：新认知的 content 只能**复制旧命题**——reinforce 的输入 `RawRef` 不带
      //   content（模型报 reinforce 时不重述内容）。于是同一命题会并存两条（旧 confirmed + 新 stated）。
      //   因此保留两条独立且可追溯的认知，不在强化路径内隐式合并。
      if (
        cog.formedBy === 'confirmed' &&
        add.length > 0 &&
        resolveFormedBy(false, add) === 'stated'
      ) {
        const upConfidence = computeConfidence(
          {
            contentType: cog.contentType,
            formedBy: 'stated',
            supportCount: add.length,
            contradictCount: 0,
            // 接线点 3/8 · 并存新 stated（形成期）。**必须接，且必须用 `add`**：
            //   本路径是保守性倒置最刺眼的一条——用户点头认了 AI 的猜测（旧 confirmed 280）之后
            //   自己含糊复述一句，并存的新认知就拿满 600。
            //   support 集合与上面的 supportCount 同为 `add`（不是全链）：全链会混进旧的附和证据，
            //   让「没有 explicit」这一条不成立，并存路的封顶永不触发（见 resolveHedged 的不变式）。
            hedged: resolveHedged('stated', add),
          },
          deps.config,
        );
        created.push(
          deps.cognitionStore.put({
            subjectId,
            content: cog.content,
            contentType: cog.contentType,
            formedBy: 'stated',
            confidence: upConfidence,
            credStatus: deriveCredStatus(upConfidence, 0, cog.contentType, deps.config),
            evidence: add.map((id) => ({ evidenceId: id, relation: 'support' as const })),
          }),
        );
      }
    }

    // correct：旧失效保留，采纳新的
    for (const c of out.correct ?? []) {
      const oldId = resolveCogId(c.cognition_id, 'correct');
      const old = oldId ? deps.cognitionStore.get(oldId) : null;
      const p = pickCognition(c);
      if (!old || old.invalidAt || !p) continue;
      const support = pickSupport(citedIds(c));
      if (support.length === 0) continue; // 纠正后的新认知也要可溯源，否则跳过（不动旧的）
      if (p.typeFallback) typeFallback[p.typeFallback]++; // 同 new 路：correct 也会落一条新认知
      deps.cognitionStore.update(old.id, { invalidAt: now }); // 标失效、保留可溯源
      const formedBy = resolveFormedBy(p.modelSaysInferred, support); // 同 new 路，载体维由代码算
      // 接线点 4/8 · correct（形成期，同 new 路）。**必须接**：否则用户用一句含糊的话纠正旧认知时，
      //   新生认知拿 600，而被标失效的旧认知可能只有 280——「纠正」反而抬高了系统对他的把握度。
      const confidence = computeConfidence(
        {
          contentType: p.contentType,
          formedBy,
          supportCount: support.length,
          contradictCount: 0,
          hedged: resolveHedged(formedBy, support), // 集合恒同 supportCount 用的那一个（support）
        },
        deps.config,
      );
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
      // 模型显式点名的 conflict：把反证挂到该旧认知、按全链重算（复用 attachContradiction，
      //   与 A5 护栏走同一段口径）。#19 的教训（只翻 credStatus→contradictPenalty 空转→被反驳的
      //   认知仍以原强度通过 recall 注入）与「hedged 重算期回表退化」边界都收在 attachContradiction 里。
      const cogId = resolveCogId(c.cognition_id, 'conflict');
      if (!cogId) continue;
      if (attachContradiction(cogId, pickSupport(citedIds(c)))) conflicted++;
    }

    // 语义解析落库（public contract）：对每条【用户真说的】证据落一份 resolution。
    //   规整 / 收窄 / 枚举收敛已在上方 `resolutionOf` 做完（来源收窄见那里）——**落库与派生共用那一份**，
    //   两处各自校验必然漂移。这里只负责 store：幂等（同证据已有解析则跳过）+ resolverVersion 绑 prompt 版本可追溯。
    //   resolved_content 是【解释结果、不是证据】，永不进入 consolidate 的 support 白名单。
    //   同一份解析还喂 `resolveFormedBy` 算认知的载体维（见上方声明）——
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
    return { created, reinforced, corrected, conflicted, typeFallback };
  });

  // 写路径仪表：profileSize = 本轮注入 prompt 的 active 认知条数（existing 就是拼进 prompt 的那份画像）。
  const { typeFallback, ...counts } = mutation;
  return {
    ...counts,
    processedEvents: newEvents.length,
    llmCalls,
    profileSize: existing.length,
    promptChars,
    contentTypeFallback: typeFallback,
  };
}
