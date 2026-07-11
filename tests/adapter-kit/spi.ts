/**
 * adapter-kit · 契约 SPI（Phase 3 §16.1「适配器更稳」）。
 *
 * 一份可复用契约套件喂两个适配器：每个适配器实现一个薄驱动（AdapterDriver，约 30-50 行），
 * 套件（contract.ts）据此产出 AD-1…AD-6 编号测试。SPI 只依赖 node 内置、与具体适配器 / Core
 * 解耦（驱动负责把真适配器桥接到这些形状）——「任何适配器接入即得 AD 编号测试」。
 *
 * AD 编号定义（PROJECT_PLAN.md §16.1，行 355-360）：
 *   AD-1 助手消息流经适配器 → evidence 表零新增（by-construction）
 *   AD-2 用户消息 → 恰好一条 evidence（spoken）
 *   AD-3 工具结果 → evidence 标 source=tool（本轮 N/A：SourceKind 无 'tool' 值，属契约分岔）
 *   AD-4 recall 呈现含置信度与冲突状态，格式锁 golden 快照
 *   AD-5 LLM 输出的虚构 evidenceId 被丢弃（本轮 N/A：无 LLM→evidenceId 回捞落库路径）
 *   AD-6 记忆层抛错/超时 → 适配器降级「无记忆但对话不中断」、注入 logger（本轮只测已有抛错降级）
 */

/** 召回呈现面自述类型：A=文本块（注入 prompt），B=结构化 JSON（structuredContent）。 */
export type RecallSurfaceKind = 'text-block' | 'structured-json';

/** 一条召回夹具。credStatus 用真实枚举（含一条 'conflicted' 走冲突路径）。 */
export interface RecallFixtureItem {
  id: string;
  content: string;
  confidence: number;
  /** 真实枚举，见 src/cognition/model.ts:29。 */
  credStatus: 'candidate' | 'low' | 'limited' | 'stable' | 'conflicted';
  score: number;
}

/**
 * 共享召回夹具：两适配器 AD-4 快照同源。第三条 credStatus='conflicted' —— 冲突经 credStatus
 * 隐式带出（src/consolidation/confidence.ts），锁进 golden 以证「冲突状态被如实呈现」。
 * 不新增任何冲突措辞（那是契约分岔，不碰 buildKnowledgeBlock / action.ts）。
 */
export const RECALL_FIXTURE: RecallFixtureItem[] = [
  { id: 'c1', content: 'Prefers concise answers', confidence: 820, credStatus: 'stable', score: 0.9 },
  { id: 'c2', content: 'Might be learning Rust', confidence: 220, credStatus: 'candidate', score: 0.7 },
  { id: 'c3', content: 'Home timezone', confidence: 500, credStatus: 'conflicted', score: 0.6 },
];

/** recallSurface 返回：rendered 自述类型 + 结构化 items。 */
export interface RecallSurface {
  kind: RecallSurfaceKind;
  /** A：注入进 prompt 的文本块；B：structuredContent 的 JSON 串。golden 锁这个。 */
  rendered: string;
  /** 结构化召回项，供字段级不变量断言（不依赖 golden）。 */
  items: Array<{ id: string; content: string; confidence: number; credStatus: string; score?: number }>;
}

/** 故障注入模式（AD-6）。本轮只跑 'throw'；'timeout'/'slow' 留 SPI 待后续超时契约。 */
export type FaultMode = 'throw' | 'timeout' | 'slow';

/** runWithFaultyCore 结果。 */
export interface FaultOutcome {
  /** 记忆层故障时适配器是否降级为「无记忆但对话不中断」。 */
  degraded: boolean;
  /** 是否经注入 logger 记录（本轮均 false：logger 属后续契约）。 */
  logged: boolean;
}

/** 某条 AD 对本适配器是否适用 + 理由（N/A 声明位）。 */
export interface Applicability {
  status: 'applicable' | 'na';
  reason: string;
}

/** 摄入一轮用户原话的结果（AD-2）。 */
export interface UserTurnResult {
  /** evidence 表增量（期望 +1）。 */
  delta: number;
  /** 落库证据的来源种类（期望 'spoken'）。 */
  sourceKind?: string;
  /** 落库证据的原话（期望 === 传入文本）。 */
  content?: string;
}

/** 薄驱动 SPI：每适配器实现一个。 */
export interface AdapterDriver {
  /** 适配器标识（测试名用），如 'ai-sdk' | 'mcp'。 */
  name: string;
  /** AD-2：摄入一轮用户原话 → evidence 增量（期望 +1）+ 落库形状。 */
  ingestUserTurn(text: string): Promise<UserTurnResult>;
  /** AD-1：让一条助手消息流经适配器 → evidence 增量（期望 0）。 */
  ingestAssistantTurn(text: string): Promise<number>;
  /** AD-2 幂等（A 专属）：同一轮用户原话 + 稳定 originId 触发 times 次 → 总增量（期望仍 1）。 */
  ingestUserTurnIdempotent?(text: string, times: number): Promise<number>;
  /** AD-4：按夹具召回，返回呈现面。lang 供 A 出 en/zh 两份；B 忽略。 */
  recallSurface(fixture: RecallFixtureItem[], lang?: 'en' | 'zh'): Promise<RecallSurface>;
  /** AD-6：对故障 Core 跑读路径，报告降级/日志。 */
  runWithFaultyCore(mode: FaultMode): Promise<FaultOutcome>;
  /** 各 AD 适用性声明（AD-3/AD-5/AD-6 用 N/A 声明位）。 */
  applicability: {
    ad3: Applicability;
    ad5: Applicability;
    ad6: Applicability;
  };
}
