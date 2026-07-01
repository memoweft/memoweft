/**
 * 跨会话趋势（地图 cell 12 开放问题 · 阶段 4-B）：反复出现的瞬时状态 → 聚成持续模式认知。
 *
 * 例："烦/累/没睡好/提不起劲"这一两周断续出现 → 不只各记一条会衰减的瞬时情绪，
 *   而是聚出一条「用户最近持续情绪低落」。
 *
 * 为什么比"特质"可信（呼应难点 1）：趋势基于【客观重复频率】用规则筛出来——
 *   先规则保证"窗口内同类状态真的出现够多次"，再让 LLM 给这个模式命名（formed_by=ruled）。
 *   频率是客观的、LLM 只负责归纳，不是凭空猜性格。
 *
 * 纪律：挂聚合的真实证据可溯源；趋势也会随好转衰减/过期（config.background）；
 *   趋势本身不再被聚成趋势；同一批证据聚过了不重复（dedup）。
 */
import { config } from '../config.ts';
import type { EvidenceStore } from '../evidence/store.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { Cognition } from '../cognition/model.ts';
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import { computeConfidence, deriveCredStatus } from '../consolidation/confidence.ts';

export interface AggregateTrendsDeps {
  evidenceStore: EvidenceStore;
  cognitionStore: CognitionStore;
  llm: LLMClient;
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

const SYSTEM = [
  '给你用户近期【反复出现的状态片段】（每条带证据 id）。判断它们有没有汇成某种【持续趋势】。',
  '铁律：',
  '- 只有当多条状态确实指向同一个持续模式时才给（如多次烦/累/没睡好 → "最近持续情绪低落/压力大"）。',
  '- 一句话描述这个趋势；注明依据了哪些证据 id；凑不出明确趋势就给空数组。',
  '- 别把"一次性的情绪"说成趋势；趋势是【一段时间反复】。',
  '严格按示例字段名输出一个 JSON 对象，不要解释：',
  '{"trends":[{"content":"用户最近这段时间持续情绪低落","based_on_evidence_ids":["ev-1","ev-2","ev-3"]}]}',
].join('\n');

function buildMessages(items: Array<{ id: string; state: string; text: string; at: string }>): ChatMessage[] {
  const list = items.map((i) => `- [${i.id}] (${i.at.slice(0, 10)}) 状态「${i.state}」← 原话：${i.text}`).join('\n');
  return [
    { role: 'system', content: SYSTEM },
    { role: 'user', content: `【近期反复出现的状态】：\n${list}` },
  ];
}

function parseOut(raw: string): { trends?: RawTrend[] } {
  const start = raw.indexOf('{');
  const end = raw.lastIndexOf('}');
  if (start === -1 || end === -1 || end < start) return {};
  try {
    return JSON.parse(raw.slice(start, end + 1)) as { trends?: RawTrend[] };
  } catch {
    return {};
  }
}

export async function aggregateTrends(
  subjectId: string,
  deps: AggregateTrendsDeps,
  now: Date = new Date(),
): Promise<TrendResult> {
  const cfg = config.background;
  const windowStart = new Date(now.getTime() - cfg.trendWindowDays * 86_400_000).toISOString();

  // 收集窗口内的"状态证据"：state 类认知（含已失效，趋势看的是"曾反复出现"）的支撑证据里、发生时间在窗内的。
  const states = deps.cognitionStore.all(subjectId).filter((c) => c.contentType === 'state');
  const items: Array<{ id: string; state: string; text: string; at: string }> = [];
  const windowEvidence = new Set<string>();
  for (const s of states) {
    for (const link of deps.cognitionStore.sourcesOf(s.id)) {
      if (link.relation !== 'support') continue;
      const e = deps.evidenceStore.get(link.evidenceId);
      if (e && e.occurredAt >= windowStart && !windowEvidence.has(e.id)) {
        windowEvidence.add(e.id);
        items.push({ id: e.id, state: s.content, text: e.summary || e.rawContent, at: e.occurredAt });
      }
    }
  }
  const empty: TrendResult = { trends: [], consideredCount: items.length, llmCalls: 0 };
  if (items.length < cfg.trendMinCount) return empty; // 规则筛：不够频，不是趋势

  // dedup：已有 active 趋势覆盖过的证据——这批要是全被盖过，就别重复聚。
  const covered = new Set<string>();
  for (const t of deps.cognitionStore.active(subjectId).filter((c) => c.contentType === 'trend')) {
    for (const l of deps.cognitionStore.sourcesOf(t.id)) covered.add(l.evidenceId);
  }
  if ([...windowEvidence].every((id) => covered.has(id))) return empty;

  const before = deps.llm.callCount;
  const out = parseOut(await deps.llm.chat(buildMessages(items)));
  const llmCalls = deps.llm.callCount - before;

  const trends: Cognition[] = [];
  for (const raw of out.trends ?? []) {
    const content = (raw.content ?? raw.trend ?? '').trim();
    if (!content) continue;
    const cited = [...new Set((raw.based_on_evidence_ids ?? []).filter((id) => windowEvidence.has(id)))];
    if (cited.length === 0) continue; // 没引到真实状态证据 → 不硬编
    const confidence = computeConfidence({ contentType: 'trend', formedBy: 'ruled', supportCount: cited.length, contradictCount: 0 });
    trends.push(
      deps.cognitionStore.put({
        subjectId,
        content,
        contentType: 'trend',
        formedBy: 'ruled', // 规则聚出（基于客观频率），比 inferred 可信
        confidence,
        credStatus: deriveCredStatus(confidence, 0, 'trend'),
        evidence: cited.map((id) => ({ evidenceId: id, relation: 'support' as const })),
      }),
    );
  }
  return { trends, consideredCount: items.length, llmCalls };
}
