/**
 * 事件层数据模型：位于 evidence 原料层与 cognition 判断层之间。
 *
 * 事件 = 一段对话的"情境化摘要"，挂回它覆盖的原话证据。
 * 画像（consolidate）从事件生成（带上下文），溯源仍落到原话证据。
 *
 * 隐私不变量：事件摘要只含【用户的话 + 情境】，不含助手回话（禁止系统自证，evidence contract）。
 */

export interface Event {
  id: string;
  subjectId: string;
  /** 带情境的总结。 */
  summary: string;
  /** 覆盖证据的时间锚（取最早发生时间）。 */
  occurredAt: string;
  createdAt: string;
}

export interface EventInput {
  subjectId: string;
  summary: string;
  occurredAt: string;
  /** 这个事件覆盖了哪些原话证据。 */
  evidenceIds: string[];
}

export interface EventWithEvidence extends Event {
  evidenceIds: string[];
}
