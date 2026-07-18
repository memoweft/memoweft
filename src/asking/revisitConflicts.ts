/**
 * 冲突复看：把"冲突中"的认知拿出来，带正反证据主动问。
 *
 * consolidate 遇到模糊矛盾时标 conflicted，不自动选择一方，保留冲突供后续复核。
 * 但不能永远挂着：周期把 conflicted 认知拿出来，【并排亮支撑/反对两面证据】问用户到底哪样，
 *   用户回答 → 走 correct/conflict 闭环消解。复用 AskProposal 形态。
 *
 * 纪律：MemoWeft 只产"该问什么 + 两面证据"，是否开口、怎么措辞归宿主（public contract）；
 *   提问不进入证据库，只有用户回答才是证据；已提问项（askedAt）不再重复询问。
 */
import { config, resolveLang, type Lang, type MemoWeftConfig } from '../config.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { EvidenceStore } from '../evidence/store.ts';
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import type { Evidence } from '../evidence/model.ts';
import { filterReadableByTier } from '../evidence/privacy.ts';
import type { AskProposal } from './proposeAsk.ts';
import { REVISIT_CONFLICTS_PROMPT } from './prompts.ts';
import { systemClock, type Clock } from '../clock.ts';

export interface RevisitDeps {
  cognitionStore: CognitionStore;
  evidenceStore: EvidenceStore;
  llm?: LLMClient;
  /** 可注入配置（config 去单例）：不传 = 用全局单例（作为 opts.maxAsks 缺项的兜底）。 */
  config?: MemoWeftConfig;
  /** 可注入时钟：askedAt 时间戳走它；缺省 systemClock（真实系统时间）。 */
  clock?: Clock;
}

export interface RevisitResult {
  proposals: AskProposal[];
  llmCalls: number;
}

/** 取回一批证据（保留完整对象，供隐私过滤用）。 */
function evidenceByIds(ids: string[], ev: EvidenceStore): Evidence[] {
  return ids.map((id) => ev.get(id)).filter((e): e is Evidence => e !== null);
}
/** 证据 → 展示用简讯（id + 摘要）。 */
const brief = (e: Evidence): { id: string; summary: string } => ({
  id: e.id,
  summary: e.summary || e.rawContent,
});

/** 模板兜底：同时呈现正反证据，并使用不预设结论的问法。 */
function templateQuestion(
  content: string,
  support: { summary: string }[],
  contradict: { summary: string }[],
  lang: Lang,
): string {
  if (lang === 'zh') {
    const s = support.map((e) => `「${e.summary}」`).join('、');
    const c = contradict.map((e) => `「${e.summary}」`).join('、');
    if (s && c) return `关于"${content}"——一方面${s}，另一方面又${c}。现在到底是哪样呢？`;
    return `关于"${content}"，我这边的信息有点对不上，能帮我确认下现在是怎样吗？`;
  }
  const s = support.map((e) => `"${e.summary}"`).join(', ');
  const c = contradict.map((e) => `"${e.summary}"`).join(', ');
  if (s && c)
    return `About "${content}" — on one hand ${s}, but on the other hand ${c}. Which is it actually now?`;
  return `About "${content}", the signals on my end don't quite line up. Could you help me confirm how it stands now?`;
}

async function phraseQuestion(
  content: string,
  support: { summary: string }[],
  contradict: { summary: string }[],
  llm: LLMClient,
  lang: Lang,
): Promise<string> {
  const s = support.map((e) => `- ${e.summary}`).join('\n');
  const c = contradict.map((e) => `- ${e.summary}`).join('\n');
  const user =
    lang === 'zh'
      ? `【认知】${content}\n【支撑证据】\n${s}\n【反对证据】\n${c}`
      : `[Cognition] ${content}\n[Supporting evidence]\n${s}\n[Opposing evidence]\n${c}`;
  const messages: ChatMessage[] = [
    { role: 'system', content: REVISIT_CONFLICTS_PROMPT.text[lang] },
    { role: 'user', content: user },
  ];
  const text = (await llm.chat(messages)).trim();
  return text || templateQuestion(content, support, contradict, lang);
}

export async function revisitConflicts(
  subjectId: string,
  deps: RevisitDeps,
  opts: { maxAsks?: number; markAsked?: boolean } = {},
): Promise<RevisitResult> {
  const cfg = deps.config ?? config;
  const lang = resolveLang(cfg);
  const maxAsks = opts.maxAsks ?? cfg.asking.maxAsks;
  const markAsked = opts.markAsked ?? true;

  // 候选 = active 的冲突认知里、没复看问过的。
  // active() 仅返回未失效且未归档项，因此归档的冲突认知不会触发复看询问。
  const candidates = deps.cognitionStore
    .active(subjectId)
    .filter((c) => c.credStatus === 'conflicted')
    .filter((c) => c.askedAt == null)
    .slice(0, maxAsks);

  const before = deps.llm?.callCount ?? 0;
  const proposals: AskProposal[] = [];

  for (const cog of candidates) {
    const links = deps.cognitionStore.sourcesOf(cog.id);
    const supportEv = evidenceByIds(
      links.filter((l) => l.relation === 'support').map((l) => l.evidenceId),
      deps.evidenceStore,
    );
    const contradictEv = evidenceByIds(
      links.filter((l) => l.relation === 'contradict').map((l) => l.evidenceId),
      deps.evidenceStore,
    );
    const support = supportEv.map(brief);
    const contradict = contradictEv.map(brief);
    // 隐私护栏（按当前措辞模型 tier）：只将该 tier 可读的两面证据提供给措辞模型；宿主展示的两面证据保持完整。
    const tier = deps.llm?.tier ?? 'cloud';
    const question = deps.llm
      ? await phraseQuestion(
          cog.content,
          filterReadableByTier(supportEv, tier).map(brief),
          filterReadableByTier(contradictEv, tier).map(brief),
          deps.llm,
          lang,
        )
      : templateQuestion(cog.content, support, contradict, lang);

    proposals.push({
      cognitionId: cog.id,
      kind: 'conflict',
      hypothesis: cog.content,
      question,
      evidence: support,
      contradictEvidence: contradict,
      confidence: cog.confidence,
      credStatus: cog.credStatus,
    });

    if (markAsked)
      deps.cognitionStore.update(cog.id, { askedAt: (deps.clock ?? systemClock)().toISOString() });
  }

  return { proposals, llmCalls: (deps.llm?.callCount ?? 0) - before };
}
