/**
 * 交互语义模型 · 数据模型(v0.6 · D-0034)。
 *
 * 两个新概念,给「理解人机对话」打结构化地基:
 *   - InteractionContext:一段【用户可见】上下文的快照(assistant 可见回复 / user 消息 / 可见 tool 结果)。
 *     只提供语义环境、**不产 Cognition、永不成为证据**(铁律 3a);禁收 system prompt / 隐藏提示 /
 *     chain-of-thought / 记忆注入 / 内部工具入参。
 *   - SemanticResolution:一条证据的【语义解析】(这句是肯定/否定/选择/含糊、在回应谁提出的什么命题)。
 *     是【解释结果、不是新的事实来源】;Phase 2 起由 resolver 产出,Phase 1 只建表结构。
 *
 * 结构墙(3a/3d):两者承载的 AI 可见文本 / 解析结果**永不进 consolidate 的 support 白名单、永不给证据 id**——
 *   与「是否进便携包」正交(进包是数据迁移,不等于内容变证据)。
 */

/** 用户回应的言语行为(v0.6 resolver 判;Phase 2 起产)。 */
export type ResponseAct = 'affirm' | 'negate' | 'select' | 'elaborate' | 'ask' | 'none' | 'other';
/** AI 上一句(命题)的言语行为。 */
export type PromptAct = 'propose' | 'ask' | 'state' | 'none' | 'other';
/** 命题来源:用户主动提出 vs AI 提出。附和的关键——AI 提的、用户点头 → confirmed,不成 stated(v0.6 Phase 3 用)。 */
export type PropositionOrigin = 'user_stated' | 'assistant_proposed';
/** 断言强度:明确 vs 含糊("可能吧")。 */
export type AssertionStrength = 'explicit' | 'weak' | 'none';

/** 可见交互轮(context_json 的元素):只含【用户可见】内容。 */
export interface VisibleTurn {
  role: 'user' | 'assistant' | 'tool';
  content: string;
}

/** 一条交互上下文快照(落库后形状)。 */
export interface InteractionContext {
  id: string;
  subjectId: string;
  conversationId: string;
  /** 交互 episode 标识(宿主可选传;缺省库内按 idle 间隔切分)。 */
  episodeId: string;
  /** 用户可见上下文的有序快照(禁 system / hidden / CoT / memory-injection)。 */
  context: VisibleTurn[];
  /** context 的内容指纹(sha256),幂等去重用(写入前查重,非 DB 唯一约束——避免便携包跨库导入撞车)。 */
  contextHash: string;
  createdAt: string;
}

export interface InteractionContextInput {
  subjectId: string;
  conversationId: string;
  episodeId: string;
  context: VisibleTurn[];
}

/** 一条语义解析(落库后形状)。Phase 1 只建表结构;各解析字段由 Phase 2 resolver 填。 */
export interface SemanticResolution {
  id: string;
  /** 关联的证据(通过 evidence_id 关联 subject——本表不冗余存 subject_id)。 */
  evidenceId: string;
  /** 解释结果(如「用户确认自己喜欢研究 AI」)——**不是新的事实来源**,永不成为证据(3a)。 */
  resolvedContent: string;
  responseAct: ResponseAct | null;
  promptAct: PromptAct | null;
  propositionOrigin: PropositionOrigin | null;
  assertionStrength: AssertionStrength | null;
  /** 解析所依赖的上下文引用 / 快照(可空)。 */
  requiredContext: string | null;
  /** 产出这条解析的 resolver 版本(可追溯)。 */
  resolverVersion: string;
  createdAt: string;
}

export interface SemanticResolutionInput {
  evidenceId: string;
  resolvedContent: string;
  responseAct?: ResponseAct | null;
  promptAct?: PromptAct | null;
  propositionOrigin?: PropositionOrigin | null;
  assertionStrength?: AssertionStrength | null;
  requiredContext?: string | null;
  resolverVersion: string;
}
