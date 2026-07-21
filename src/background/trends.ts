/**
 * 跨会话趋势：反复出现的瞬时状态 → 聚成持续模式认知。
 *
 * 例："烦/累/没睡好/提不起劲"这一两周断续出现 → 不只各记一条会衰减的瞬时情绪，
 *   而是聚出一条「用户最近持续情绪低落」。
 *
 * 趋势比特质推断更可信：趋势基于【客观重复频率】由规则筛选——
 *   先规则保证"窗口内同类状态真的出现够多次"，再让 LLM 给这个模式命名（formed_by=ruled）。
 *   频率是客观的、LLM 只负责归纳，不是凭空猜性格。
 *
 * 纪律：挂聚合的真实证据可溯源；趋势也会随好转衰减/过期（config.background）；
 *   趋势本身不再被聚成趋势；同一批证据聚过了不重复（dedup）。
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
import { TRENDS_PROMPT } from './prompts.ts';

export interface AggregateTrendsDeps {
  evidenceStore: EvidenceStore;
  cognitionStore: CognitionStore;
  llm: LLMClient;
  /** 可注入配置（config 去单例）：不传 = 用全局单例。 */
  config?: MemoWeftConfig;
}

export interface TrendResult {
  trends: Cognition[];
  /** 窗口内考察到的状态证据条数（规则筛的输入量）。 */
  consideredCount: number;
  llmCalls: number;
}

interface RawTrend {
  content?: string;
  trend?: string;
  based_on_evidence_ids?: string[];
}

function buildMessages(
  items: Array<{ id: string; state: string; text: string; at: string }>,
  lang: Lang,
): { messages: ChatMessage[]; tagToId: Map<string, string> } {
  const zh = lang === 'zh';
  // 发短标号 [e1] 而非 36 字符 UUID：模型会模仿示例的 id 形态、间歇性把长 UUID 截断回写
  //   → windowEvidence 精确匹配落空 → 趋势被静默丢弃。发标号 = 示例与真实形态一致，模型写不错。
  const tagToId = new Map<string, string>();
  const list = items
    .map((i, k) => {
      const tag = `e${k + 1}`;
      tagToId.set(tag, i.id);
      return zh
        ? `- [${tag}] (${i.at.slice(0, 10)}) 状态「${i.state}」← 原话：${i.text}`
        : `- [${tag}] (${i.at.slice(0, 10)}) state "${i.state}" ← utterance: ${i.text}`;
    })
    .join('\n');
  const body = zh ? `【近期反复出现的状态】：\n${list}` : `[Recent recurring states]:\n${list}`;
  return {
    messages: [
      { role: 'system', content: TRENDS_PROMPT.text[lang] },
      { role: 'user', content: body },
    ],
    tagToId,
  };
}

interface LLMOut {
  trends?: RawTrend[];
}

export async function aggregateTrends(
  subjectId: string,
  deps: AggregateTrendsDeps,
  now: Date = new Date(),
): Promise<TrendResult> {
  const fullCfg = deps.config ?? config; // 可注入配置（缺省=单例）
  const lang = resolveLang(fullCfg);
  const cfg = fullCfg.background;
  const windowStart = new Date(now.getTime() - cfg.trendWindowDays * 86_400_000).toISOString();

  // 收集窗口内的"状态证据"：state 类认知（含已失效，趋势看的是"曾反复出现"）的支撑证据里、发生时间在窗内的。
  // 保持 all()：趋势聚合是【历史口径】——看"曾反复出现"，本就计入已失效，
  // 已归档项同样计入历史；趋势聚合不使用 active() 的当前状态口径。
  // 排除 confirmed（附和）：防"AI 诱导性提问风暴 + 用户连答是的"被规则数成一条更可信的 ruled 趋势，
  //   防止通过重复附和绕过 confirmed 的低置信上限，这是结构性不变量。
  const states = deps.cognitionStore
    .all(subjectId)
    .filter((c) => c.contentType === 'state' && c.formedBy !== 'confirmed');
  const items: Array<{ id: string; state: string; text: string; at: string }> = [];
  const windowEvidence = new Set<string>();
  // 隐私关（按当前模型 tier）：tier=cloud 筛 allowCloudRead / tier=local 筛 allowLocalRead。缺省 'cloud'。
  const tier = deps.llm.tier ?? 'cloud';
  for (const s of states) {
    for (const link of deps.cognitionStore.sourcesOf(s.id)) {
      if (link.relation !== 'support') continue;
      const e = deps.evidenceStore.get(link.evidenceId);
      // 隐私护栏：当前模型 tier 无权读取的证据不进入趋势模型输入，也不能成为趋势支撑（与 distill/consolidate/attribute 一致）。
      if (
        e &&
        filterReadableByTier([e], tier).length > 0 &&
        e.occurredAt >= windowStart &&
        !windowEvidence.has(e.id)
      ) {
        windowEvidence.add(e.id);
        items.push({
          id: e.id,
          state: s.content,
          text: e.summary || e.rawContent,
          at: e.occurredAt,
        });
      }
    }
  }
  const empty: TrendResult = { trends: [], consideredCount: items.length, llmCalls: 0 };
  if (items.length < cfg.trendMinCount) return empty; // 规则筛：不够频，不是趋势

  // dedup：排除已被 active 趋势覆盖的证据；当前批次无新证据时跳过聚合。
  const covered = new Set<string>();
  for (const t of deps.cognitionStore.active(subjectId).filter((c) => c.contentType === 'trend')) {
    for (const l of deps.cognitionStore.sourcesOf(t.id)) covered.add(l.evidenceId);
  }
  if ([...windowEvidence].every((id) => covered.has(id))) return empty;

  // 结构化输出加固（jsonRepair）：去围栏 → 解析对象；失败落日志 + 最多重试一次（提示"只输出 JSON"）。
  // 仍失败 → null（按"本轮无趋势产出"处理，等价旧的返回空对象）。重试复用同一批已过滤 messages
  // （写路径边界 C：只复用已过滤上下文 + 追加提示，不引入新证据文本）。重试会多调一次模型，故计数取前后差。
  const before = deps.llm.callCount;
  const { messages, tagToId } = buildMessages(items, lang);
  const out =
    (await parseJsonObjectWithRepair<LLMOut>({
      llm: deps.llm,
      messages,
      lang,
    })) ?? {};
  const llmCalls = deps.llm.callCount - before;

  const trends: Cognition[] = [];
  for (const raw of out.trends ?? []) {
    const content = (raw.content ?? raw.trend ?? '').trim();
    if (!content) continue;
    // 经 resolveEchoedId 归一：标号 e1 / 精确 UUID / 截断前缀都解回真 id；捏造 / 歧义仍丢。
    const cited = [
      ...new Set(
        (raw.based_on_evidence_ids ?? [])
          .map((id) => resolveEchoedId(id, windowEvidence, tagToId))
          .filter((id): id is string => id !== null),
      ),
    ];
    if (cited.length === 0) continue; // 没引到真实状态证据 → 不硬编
    const confidence = computeConfidence(
      { contentType: 'trend', formedBy: 'ruled', supportCount: cited.length, contradictCount: 0 },
      fullCfg,
    );
    trends.push(
      deps.cognitionStore.put({
        subjectId,
        content,
        contentType: 'trend',
        formedBy: 'ruled', // 规则聚出（基于客观频率），比 inferred 可信
        confidence,
        credStatus: deriveCredStatus(confidence, 0, 'trend', fullCfg),
        evidence: cited.map((id) => ({ evidenceId: id, relation: 'support' as const })),
      }),
    );
  }
  return { trends, consideredCount: items.length, llmCalls };
}
