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
import { config } from '../config.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { EvidenceStore } from '../evidence/store.ts';
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import type { AskProposal } from './proposeAsk.ts';

export interface RevisitDeps {
  cognitionStore: CognitionStore;
  evidenceStore: EvidenceStore;
  llm?: LLMClient;
}

export interface RevisitResult {
  proposals: AskProposal[];
  llmCalls: number;
}

const PHRASE_SYSTEM = [
  '下面是一条关于用户的认知，它【同时有支撑和反对的证据】，处于矛盾状态。',
  '请生成一句简短、真诚、不预设立场的提问，把两边都点一下、请用户澄清到底是哪样。',
  '只输出这一句问话本身，不要解释、不要引号。',
].join('\n');

function summarize(
  ids: string[],
  ev: EvidenceStore,
): { id: string; summary: string }[] {
  return ids
    .map((id) => ev.get(id))
    .filter((e): e is NonNullable<typeof e> => e !== null)
    .map((e) => ({ id: e.id, summary: e.summary || e.rawContent }));
}

/** 模板兜底：并排亮两面、留余地的朴素问法。 */
function templateQuestion(
  content: string,
  support: { summary: string }[],
  contradict: { summary: string }[],
): string {
  const s = support.map((e) => `「${e.summary}」`).join('、');
  const c = contradict.map((e) => `「${e.summary}」`).join('、');
  if (s && c) return `关于"${content}"——一方面${s}，另一方面又${c}。现在到底是哪样呢？`;
  return `关于"${content}"，我这边的信息有点对不上，能帮我确认下现在是怎样吗？`;
}

async function phraseQuestion(
  content: string,
  support: { summary: string }[],
  contradict: { summary: string }[],
  llm: LLMClient,
): Promise<string> {
  const messages: ChatMessage[] = [
    { role: 'system', content: PHRASE_SYSTEM },
    {
      role: 'user',
      content:
        `【认知】${content}\n【支撑证据】\n` +
        support.map((e) => `- ${e.summary}`).join('\n') +
        `\n【反对证据】\n` +
        contradict.map((e) => `- ${e.summary}`).join('\n'),
    },
  ];
  const text = (await llm.chat(messages)).trim();
  return text || templateQuestion(content, support, contradict);
}

export async function revisitConflicts(
  subjectId: string,
  deps: RevisitDeps,
  opts: { maxAsks?: number; markAsked?: boolean } = {},
): Promise<RevisitResult> {
  const maxAsks = opts.maxAsks ?? config.asking.maxAsks;
  const markAsked = opts.markAsked ?? true;

  // 候选 = active 的冲突认知里、没复看问过的。
  const candidates = deps.cognitionStore
    .active(subjectId)
    .filter((c) => c.credStatus === 'conflicted')
    .filter((c) => c.askedAt == null)
    .slice(0, maxAsks);

  const before = deps.llm?.callCount ?? 0;
  const proposals: AskProposal[] = [];

  for (const cog of candidates) {
    const links = deps.cognitionStore.sourcesOf(cog.id);
    const support = summarize(links.filter((l) => l.relation === 'support').map((l) => l.evidenceId), deps.evidenceStore);
    const contradict = summarize(links.filter((l) => l.relation === 'contradict').map((l) => l.evidenceId), deps.evidenceStore);
    const question = deps.llm
      ? await phraseQuestion(cog.content, support, contradict, deps.llm)
      : templateQuestion(cog.content, support, contradict);

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
