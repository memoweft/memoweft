/**
 * 事件化（地图 cell 4 写路径）：把"还没整理成事件"的近期对话 → 总结成一个带情境的事件。
 *
 * 红线：只总结【用户的话 + 情境】，不含助手回话、不加 AI 推测评价（禁止系统自证 + 记≠改画像）。
 */
import type { EvidenceStore } from '../evidence/store.ts';
import type { EventStore } from '../event/store.ts';
import type { Event } from '../event/model.ts';
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import { filterCloudReadable } from '../evidence/privacy.ts';

export interface DistillDeps {
  evidenceStore: EvidenceStore;
  eventStore: EventStore;
  llm: LLMClient;
}

export interface DistillResult {
  event: Event | null;
  pendingCount: number;
  llmCalls: number;
}

const SYSTEM = [
  '你把用户的几句话总结成一段带情境的"事件"描述。',
  '规则：',
  '1. 只总结用户表达的内容和情境，按时间顺序串起来。',
  '2. 不要加入你的推测、评价或建议；不要出现"助手"的话。',
  '3. 一段话，简洁、具体，点出关键信息（在做什么、什么状态、提到什么）。',
  '4. 只输出这段总结文本，不要解释。',
].join('\n');

export async function distill(subjectId: string, deps: DistillDeps): Promise<DistillResult> {
  const evidence = deps.evidenceStore.all().filter((e) => e.subjectId === subjectId);
  const covered = new Set(deps.eventStore.coveredEvidenceIds(subjectId));
  const pending = evidence
    .filter((e) => !covered.has(e.id))
    .sort((a, b) => a.occurredAt.localeCompare(b.occurredAt));

  if (pending.length === 0) return { event: null, pendingCount: 0, llmCalls: 0 };

  // 隐私关：只把"允许上云"的原话喂给（云端）LLM；cloud=false 的不进 prompt。
  // deps.llm 假设是云端模型——接本地模型时需改（见 evidence/privacy.ts 前提注释）。
  const cloudSafe = filterCloudReadable(pending);
  // 极端情况：本批全是"不许上云"的证据（当前不会发生，现证据皆 cloud=true）→ 不拿空材料调云端模型。
  // 仍用 pending 做下方事件的 evidenceIds，故这些证据【算已被覆盖处理】、不会每轮被重复重捞。
  // TODO(4-A)：observed 默认 cloud=false 引入后，这些证据该走本地模型 / 覆盖语义如何记，随 4-A 折中 A/B 一并定。
  if (cloudSafe.length === 0) return { event: null, pendingCount: pending.length, llmCalls: 0 };

  const lines = cloudSafe.map((e) => `(${e.occurredAt.slice(0, 16)}) ${e.rawContent}`).join('\n');
  const messages: ChatMessage[] = [
    { role: 'system', content: SYSTEM },
    { role: 'user', content: `用户依次说了：\n${lines}` },
  ];

  const before = deps.llm.callCount;
  const summary = (await deps.llm.chat(messages)).trim();
  const llmCalls = deps.llm.callCount - before;

  const event = deps.eventStore.put({
    subjectId,
    summary,
    occurredAt: pending[0]!.occurredAt,
    evidenceIds: pending.map((e) => e.id),
  });

  return { event, pendingCount: pending.length, llmCalls };
}
