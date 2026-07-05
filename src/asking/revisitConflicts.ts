/**
 * 冲突复看（地图 cell 8 规则 5 · 阶段 4-B）：把"冲突中"的认知拿出来，带正反证据主动问。
 *
 * consolidate 遇到模糊矛盾时标 conflicted、不自动选边（规则 5）——这是 MemoWeft 的分水岭。
 * 但不能永远挂着：周期把 conflicted 认知拿出来，【并排亮支撑/反对两面证据】问用户到底哪样，
 *   用户回答 → 走阶段 2 的 correct/conflict 闭环消解。复用 M5 的 AskProposal 形态。
 *
 * 纪律：MemoWeft 只产"该问什么 + 两面证据"，是否开口、怎么措辞归宿主（cell 9）；
 *   提问不入证据库、用户回答才是证据（规则 4）；问过的（askedAt）不再问。
 */
import { config, resolveLang, type Lang, type MemoWeftConfig } from '../config.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { EvidenceStore } from '../evidence/store.ts';
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import type { Evidence } from '../evidence/model.ts';
import { filterCloudReadable } from '../evidence/privacy.ts';
import type { AskProposal } from './proposeAsk.ts';

export interface RevisitDeps {
  cognitionStore: CognitionStore;
  evidenceStore: EvidenceStore;
  llm?: LLMClient;
  /** 可注入配置（P2-5 config 去单例）：不传 = 用全局单例（作为 opts.maxAsks 缺项的兜底）。 */
  config?: MemoWeftConfig;
}

export interface RevisitResult {
  proposals: AskProposal[];
  llmCalls: number;
}

const PHRASE_SYSTEM: Record<Lang, string> = {
  zh: [
    '下面是一条关于用户的认知，它【同时有支撑和反对的证据】，处于矛盾状态。',
    '请生成一句简短、真诚、不预设立场的提问，把两边都点一下、请用户澄清到底是哪样。',
    '只输出这一句问话本身，不要解释、不要引号。',
  ].join('\n'),
  en: [
    'Below is a cognition about the user that [has both supporting and opposing evidence] and is in a conflicted state.',
    'Generate one short, sincere, non-presumptive question that touches on both sides and asks the user to clarify which is actually the case.',
    'Output only this one question itself, with no explanation and no quotation marks.',
  ].join('\n'),
};

/** 取回一批证据（保留完整对象，供隐私过滤用）。 */
function evidenceByIds(ids: string[], ev: EvidenceStore): Evidence[] {
  return ids
    .map((id) => ev.get(id))
    .filter((e): e is Evidence => e !== null);
}
/** 证据 → 展示用简讯（id + 摘要）。 */
const brief = (e: Evidence): { id: string; summary: string } => ({ id: e.id, summary: e.summary || e.rawContent });

/** 模板兜底：并排亮两面、留余地的朴素问法。 */
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
  if (s && c) return `About "${content}" — on one hand ${s}, but on the other hand ${c}. Which is it actually now?`;
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
    { role: 'system', content: PHRASE_SYSTEM[lang] },
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
  // active()（未失效且未归档）：归档全面雪藏（批次3 用户拍板）——已归档的冲突认知不再复看追问。
  const candidates = deps.cognitionStore
    .active(subjectId)
    .filter((c) => c.credStatus === 'conflicted')
    .filter((c) => c.askedAt == null)
    .slice(0, maxAsks);

  const before = deps.llm?.callCount ?? 0;
  const proposals: AskProposal[] = [];

  for (const cog of candidates) {
    const links = deps.cognitionStore.sourcesOf(cog.id);
    const supportEv = evidenceByIds(links.filter((l) => l.relation === 'support').map((l) => l.evidenceId), deps.evidenceStore);
    const contradictEv = evidenceByIds(links.filter((l) => l.relation === 'contradict').map((l) => l.evidenceId), deps.evidenceStore);
    const support = supportEv.map(brief);
    const contradict = contradictEv.map(brief);
    // 隐私护栏：只把允许上云的两面证据喂给（云端）措辞模型；宿主展示的两面证据保持完整（展示归宿主）。
    const question = deps.llm
      ? await phraseQuestion(cog.content, filterCloudReadable(supportEv).map(brief), filterCloudReadable(contradictEv).map(brief), deps.llm, lang)
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

    if (markAsked) deps.cognitionStore.update(cog.id, { askedAt: new Date().toISOString() });
  }

  return { proposals, llmCalls: (deps.llm?.callCount ?? 0) - before };
}
