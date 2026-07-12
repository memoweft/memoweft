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
import type { Evidence } from '../evidence/model.ts';
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import { computeConfidence, deriveCredStatus } from './confidence.ts';
import { filterReadableByTier } from '../evidence/privacy.ts';
import { sourceLabel } from '../evidence/sourceLabel.ts';
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
}
interface LLMOut {
  new?: RawCog[];
  reinforce?: RawRef[];
  correct?: Array<{ cognition_id?: string } & RawCog>;
  conflict?: RawRef[];
}

/** 从候选里取它引的原话 id（容错字段名）。 */
function citedIds(c: RawCog | RawRef): string[] {
  return c.support_evidence_ids ?? c.evidence_ids ?? [];
}

const VALID_TYPES = ['fact', 'preference', 'goal', 'project', 'state', 'trait'];
const VALID_FORMED = ['stated', 'observed', 'ruled', 'inferred'];

/** 从原始候选里抽出认知（容错字段名 + 缺类型给保守默认：fact/inferred）。无内容返回 null。 */
function pickCognition(c: RawCog): { content: string; contentType: ContentType; formedBy: FormedBy } | null {
  const content = (c.content ?? c.new_content ?? c.cognition ?? '').trim();
  if (!content) return null;
  const contentType = (VALID_TYPES.includes(c.content_type ?? '') ? c.content_type : 'fact') as ContentType;
  const formedBy = (VALID_FORMED.includes(c.formed_by ?? '') ? c.formed_by : 'inferred') as FormedBy;
  return { content, contentType, formedBy };
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
        // 来源感知（D-0018）:原话带来源前缀,让 LLM 定 formedBy 时知道哪些不是用户亲口。
        return { id: e.id, text: sourceLabel(e.sourceKind, lang) + e.rawContent };
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
      const confidence = computeConfidence({ contentType: p.contentType, formedBy: p.formedBy, supportCount: support.length, contradictCount: 0 }, deps.config);
      created.push(
        deps.cognitionStore.put({
          subjectId,
          content: p.content,
          contentType: p.contentType,
          formedBy: p.formedBy,
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
      const confidence = computeConfidence({ contentType: cog.contentType, formedBy: cog.formedBy, supportCount, contradictCount }, deps.config);
      deps.cognitionStore.update(cog.id, { confidence, credStatus: deriveCredStatus(confidence, contradictCount, cog.contentType, deps.config) });
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
      const confidence = computeConfidence({ contentType: p.contentType, formedBy: p.formedBy, supportCount: support.length, contradictCount: 0 }, deps.config);
      created.push(
        deps.cognitionStore.put({
          subjectId,
          content: p.content,
          contentType: p.contentType,
          formedBy: p.formedBy,
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

    deps.eventStore.markConsolidated(newEvents.map((e) => e.id));
    return { created, reinforced, corrected, conflicted };
  });

  // 写路径仪表：profileSize = 本轮注入 prompt 的 active 认知条数（existing 就是拼进 prompt 的那份画像）。
  return { ...mutation, processedEvents: newEvents.length, llmCalls, profileSize: existing.length, promptChars };
}
