/**
 * 召回注入块拼装（读路径的中性措辞层）。
 *
 * 边界（照 MemoWeft「Core 无头」纪律）：注入文案只搬 Core `src/pipeline/action.ts` 的
 *   knowledgeBlock 中性措辞（逐字对齐 adapter-ai-sdk），低置信条目明确标
 *   "only guesses—do not treat as established facts"。适配器里不自造人格/人设 prompt。
 *
 * 隐私硬约束（D-0024·不可违反）：本块【只】用 content/confidence/credStatus。
 *   provenance（证据原文 + 授权位）、contentType、id、score 一律【不】入块——
 *   provenance 进 prompt = 绕过 tier 把云受限原文喂给模型；这些字段只经 onRecall 交宿主。
 */
import type { ContentType, RecalledEvidence } from 'memoweft';

/**
 * 召回项形状。注入块只用前三个字段（content/confidence/credStatus）；
 * id/contentType/score/provenance 是召回 v2 面（D-0022/D-0021/D-0024）——【只】经 onRecall 透传给宿主，
 * 全部可选以保持与 Core 松耦合、兼容旧构造。
 *
 * 隐私硬约束（D-0024·不可违反）：provenance 是证据【原文】+ 授权位（含云受限的 observed/tool），
 *   【绝不】进 buildKnowledgeBlock / 注入 prompt（进 prompt = 绕过 tier 把受限原文喂给云模型）——
 *   只经 onRecall 交宿主，宿主转发云模型前据 allowCloudRead/allowInference 自筛。
 */
export interface RecalledLike {
  content: string;
  confidence: number;
  credStatus: string;
  /** 认知 id（D-0022）：随召回带回，仅经 onRecall 交宿主（管理/透视反查），不进注入块。 */
  id?: string;
  /** 认知类型（D-0022）：仅经 onRecall 交宿主，不进注入块。 */
  contentType?: ContentType;
  /** 相似度分：仅经 onRecall 交宿主观测，不进注入块。 */
  score?: number;
  /** 召回解释链（D-0021/D-0024·仅 explain 时带）：证据原文 + 授权位。仅经 onRecall 交宿主，绝不进注入 prompt。 */
  provenance?: RecalledEvidence[];
}

/**
 * 拼注入块：照搬 Core `src/pipeline/action.ts` 的 knowledgeBlock 中性措辞（逐字对齐，别自造人设）。
 * 空召回返回空串（调用方据此决定不注入）。
 *
 * 隐私硬约束（D-0024·不可违反）：本块【只】用 content/confidence/credStatus。
 *   provenance（证据原文 + 授权位）、contentType、id、score 一律【不】入块——provenance 进 prompt = 绕过 tier
 *   把云受限原文喂给模型；这些字段只经 onRecall 交宿主。改这里前先想清楚这条。
 */
export function buildKnowledgeBlock(relevant: RecalledLike[], lang: 'en' | 'zh' = 'en'): string {
  if (relevant.length === 0) return '';
  const lines = relevant
    .map((c) =>
      lang === 'zh'
        ? `- ${c.content}（把握度 ${c.confidence}/1000，${c.credStatus}）`
        : `- ${c.content} (confidence ${c.confidence}/1000, ${c.credStatus})`,
    )
    .join('\n');
  const head =
    lang === 'zh'
      ? '\n\n你已了解关于这个用户的一些情况（带把握度；低置信的只是假设，别当定论、别生硬复述）：\n'
      : '\n\nHere is some of what you already understand about this user (with confidence; low-confidence items are only guesses—do not treat them as established facts, and do not recite them stiffly):\n';
  return head + lines;
}
