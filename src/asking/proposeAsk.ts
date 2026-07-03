/**
 * M5 带证据主动询问（地图 cell 5 阶段 3 · cell 8 规则 6/7 · cell 9 · cell 12 开放问题）。
 *
 * MemoWeft 对低置信【假设】拿不准 → 产出"该不该问 / 问什么 / 附什么证据"的结构化建议（AskProposal）。
 * 它【不】替宿主开口：是否开口、最终措辞归宿主（cell 9）；测试台替宿主做最朴素的提问。
 *
 * 纪律：
 *   - 只问【低置信假设】（规则 6）：credStatus 在白名单、把握度落在"将信将疑"带（cell 12 时机）。
 *   - 把握度透明给宿主（规则 7）：AskProposal 带 confidence / credStatus。
 *   - 带证据问（可证伪）：附上支撑该假设的证据，让用户能据此否定。
 *   - 禁止系统自证（规则 4）：提问本身不入证据库；只有用户的【回答】才是证据（走阶段 2 闭环）。
 *   - 不烦用户：问过的（askedAt 已写）不再问；一轮最多 maxAsks 个。
 */
import { config, type MemoWeftConfig } from '../config.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { EvidenceStore } from '../evidence/store.ts';
import type { CredStatus } from '../cognition/model.ts';
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import { filterCloudReadable } from '../evidence/privacy.ts';

/** 一条主动询问建议：针对哪条认知、建议怎么问、附了什么证据、有多大把握。 */
export interface AskProposal {
  cognitionId: string;
  /** 来源：求证一个低置信假设（hypothesis）/ 复看一条冲突中的认知（conflict）。 */
  kind: 'hypothesis' | 'conflict';
  /** 被求证的认知原文（假设的因果句，或冲突认知的内容）。 */
  hypothesis: string;
  /** 建议问法（朴素，宿主可改写）。 */
  question: string;
  /** 亮出来的证据（支撑侧；让用户能据此否定 / 证实）。 */
  evidence: { id: string; summary: string }[];
  /** 反对侧证据（仅冲突复看：跟支撑侧并排亮出来，让用户判到底哪样）。 */
  contradictEvidence?: { id: string; summary: string }[];
  /** 把握度（透明给宿主，规则 7）。 */
  confidence: number;
  credStatus: CredStatus;
}

export interface ProposeAskDeps {
  cognitionStore: CognitionStore;
  evidenceStore: EvidenceStore;
  /** 可选：用于润色问法；不给则用模板拼。 */
  llm?: LLMClient;
  /** 可注入配置（P2-5 config 去单例）：不传 = 用全局单例（作为 opts.policy 缺项的兜底）。 */
  config?: MemoWeftConfig;
}

export interface AskPolicy {
  maxAsks: number;
  confidenceBand: { min: number; max: number };
  askableStatuses: string[];
}

export interface ProposeAskOptions {
  policy?: Partial<AskPolicy>;
  /** 是否把选中的假设标记为"已问过"（写 askedAt，用于去重）。默认 true。 */
  markAsked?: boolean;
}

export interface ProposeAskResult {
  proposals: AskProposal[];
  llmCalls: number;
}

const PHRASE_SYSTEM = [
  '你在帮助理解用户。下面是一条关于用户的【低置信假设】和支撑它的【证据】。',
  '请生成一句简短、真诚、不武断的提问，亮出证据、向用户求证这条假设是否成立。',
  '只输出这一句问话本身，不要解释、不要引号。',
].join('\n');

/** 模板兜底：带证据、留余地的朴素问法。 */
function templateQuestion(hypothesis: string, evidence: { summary: string }[]): string {
  const shown = evidence.map((e) => `「${e.summary}」`).join('、');
  if (!shown) return `我有个不太确定的猜测：${hypothesis}。是这样吗？`;
  return `我看到${shown}，所以在想：${hypothesis}。是这样吗？`;
}

async function phraseQuestion(
  hypothesis: string,
  evidence: { summary: string }[],
  llm: LLMClient,
): Promise<string> {
  const shown = evidence.map((e) => `- ${e.summary}`).join('\n');
  const messages: ChatMessage[] = [
    { role: 'system', content: PHRASE_SYSTEM },
    { role: 'user', content: `【假设】${hypothesis}\n【证据】\n${shown}` },
  ];
  const text = (await llm.chat(messages)).trim();
  return text || templateQuestion(hypothesis, evidence);
}

export async function proposeAsk(
  subjectId: string,
  deps: ProposeAskDeps,
  opts: ProposeAskOptions = {},
): Promise<ProposeAskResult> {
  const cfg = deps.config ?? config; // 可注入配置（缺省=单例）
  const policy: AskPolicy = {
    maxAsks: opts.policy?.maxAsks ?? cfg.asking.maxAsks,
    confidenceBand: opts.policy?.confidenceBand ?? cfg.asking.confidenceBand,
    askableStatuses: opts.policy?.askableStatuses ?? cfg.asking.askableStatuses,
  };
  const markAsked = opts.markAsked ?? true;

  // 候选 = active 假设里：没问过、状态可问、把握度在"将信将疑"带内。
  const candidates = deps.cognitionStore
    .active(subjectId)
    .filter((c) => c.contentType === 'hypothesis')
    .filter((c) => c.askedAt == null)
    .filter((c) => policy.askableStatuses.includes(c.credStatus))
    .filter((c) => c.confidence >= policy.confidenceBand.min && c.confidence <= policy.confidenceBand.max)
    // 把握度高的优先问（最"将信将疑"、最值得求证的）。
    .sort((a, b) => b.confidence - a.confidence)
    .slice(0, policy.maxAsks);

  const before = deps.llm?.callCount ?? 0;
  const proposals: AskProposal[] = [];

  for (const cog of candidates) {
    // 附证据：取支撑该假设的证据，observed 优先亮出来（"我看你玩到3:30"）。
    const supportIds = deps.cognitionStore
      .sourcesOf(cog.id)
      .filter((l) => l.relation === 'support')
      .map((l) => l.evidenceId);
    const supportEvidence = supportIds
      .map((id) => deps.evidenceStore.get(id))
      .filter((e): e is NonNullable<typeof e> => e !== null)
      .sort((a, b) => (a.sourceKind === 'observed' ? -1 : 0) - (b.sourceKind === 'observed' ? -1 : 0));
    const evidence = supportEvidence.map((e) => ({ id: e.id, summary: e.summary || e.rawContent }));
    // 隐私护栏：只把允许上云的证据喂给（云端）措辞模型；返回给宿主展示的 evidence 保持完整（展示归宿主）。
    const cloudSafe = filterCloudReadable(supportEvidence).map((e) => ({ id: e.id, summary: e.summary || e.rawContent }));

    const question = deps.llm
      ? await phraseQuestion(cog.content, cloudSafe, deps.llm)
      : templateQuestion(cog.content, evidence);

    proposals.push({
      cognitionId: cog.id,
      kind: 'hypothesis',
      hypothesis: cog.content,
      question,
      evidence,
      confidence: cog.confidence,
      credStatus: cog.credStatus,
    });

    if (markAsked) deps.cognitionStore.update(cog.id, { askedAt: new Date().toISOString() });
  }

  return { proposals, llmCalls: (deps.llm?.callCount ?? 0) - before };
}
