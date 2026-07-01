/**
 * 认知层数据模型（地图 cell 6 / 8：判断层·多维用户模型）。
 *
 * 与 evidence（原料层）彻底分开 —— 记 ≠ 直接改画像：
 *   evidence 存"用户说了/做了什么"（事实），cognition 存"对用户的判断"。
 * 授权位挂在 evidence，不在这一层；这层只存判断。
 */

/**
 * 内容类型（地图 cell 8：多维之一，不互斥的描述属性）。
 * `hypothesis` = 可解释假设（阶段 3 M4 归因产物：从证据推"为什么"，低置信、挂证据、可推翻）。
 * `trend` = 跨会话趋势（阶段 4-B：反复出现的状态聚成的持续模式，如"最近持续低落"；
 *           基于客观频率用规则聚出 formed_by=ruled，比"特质"可信，会随好转衰减）。
 */
export type ContentType =
  | 'fact'
  | 'preference'
  | 'goal'
  | 'project'
  | 'state'
  | 'trait'
  | 'hypothesis'
  | 'trend';

/** 形成方式 —— 来源强度（亲口 > 观察 > 规则 > LLM 推测）。 */
export type FormedBy = 'stated' | 'observed' | 'ruled' | 'inferred';

/** 可信状态（地图 cell 8 规则 9）。 */
export type CredStatus = 'candidate' | 'low' | 'limited' | 'stable' | 'conflicted';

/** 溯源链上一条证据与认知的关系。 */
export type EvidenceRelation = 'support' | 'contradict';

export interface EvidenceLink {
  evidenceId: string;
  relation: EvidenceRelation;
}

/** 一条认知（落库后的完整形状）。 */
export interface Cognition {
  id: string;
  subjectId: string;
  content: string;
  contentType: ContentType;
  formedBy: FormedBy;
  /** 把握度 0~1000（MemoWeft 自算，非 LLM 自报）。 */
  confidence: number;
  credStatus: CredStatus;
  /** 适用场景；null = 通用。 */
  scope: string | null;
  validAt: string | null;
  invalidAt: string | null;
  /** 主动询问时间戳（阶段 3 M5）：null = 未问过；proposeAsk 发问后写入，用于"问过不再问"去重。 */
  askedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

/** 写入认知的入参；id / 时间由存储层生成，confidence/credStatus 由 consolidate 算好后传入。 */
export interface CognitionInput {
  subjectId: string;
  content: string;
  contentType: ContentType;
  formedBy: FormedBy;
  confidence: number;
  credStatus: CredStatus;
  scope?: string | null;
  validAt?: string | null;
  invalidAt?: string | null;
  /** 溯源链：这条认知靠哪些证据支持 / 反对。 */
  evidence?: EvidenceLink[];
}

/** 带溯源链的认知（读出展示用）。 */
export interface CognitionWithSources extends Cognition {
  sources: EvidenceLink[];
}
