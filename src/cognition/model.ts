/**
 * 认知层数据模型：判断层·多维用户模型。
 *
 * 与 evidence（原料层）彻底分开 —— 记 ≠ 直接改画像：
 *   evidence 存"用户说了/做了什么"（事实），cognition 存"对用户的判断"。
 * 授权位挂在 evidence，不在这一层；这层只存判断。
 */

/**
 * 内容类型：多维之一，不互斥的描述属性。
 * `hypothesis` = 可解释假设（归因产物：从证据推"为什么"，低置信、挂证据、可推翻）。
 * `trend` = 跨会话趋势：反复出现的状态聚成的持续模式，如"最近持续低落"；
 *           基于客观频率用规则聚出 formed_by=ruled，比"特质"可信，会随好转衰减）。
 */
export type ContentType =
  'fact' | 'preference' | 'goal' | 'project' | 'state' | 'trait' | 'hypothesis' | 'trend';

/** 形成方式（亲口 > 观察 > 规则 > 附和 > LLM 推测）。
 *  `confirmed`（附和，）：用户【点头认可 AI 主动提出的猜测】（AI:"你喜欢爬山吧?" 用户:"是的"）——
 *  比 inferred 强（用户确实点了头）、比 observed/stated 弱（附和诱导性猜测有客气/顺着说成分，非主动披露）。
 *  底分 280、自然封顶 480（<limited 500）→ 纯附和顶天"低置信"；只有用户【主动】说才升级破顶。 */
export type FormedBy = 'stated' | 'observed' | 'ruled' | 'confirmed' | 'inferred';

/** 认知可信状态。
 *  `conflicted` 与 `contested` 都表示"存在反对证据"，区别在力量对比：
 *    - `contested` 支撑条数【多于】反证 —— 有争议但仍成立；
 *    - `conflicted` 反证与支撑对峙或占优 —— 不消解、原样暴露（public contract）。
 *  两者都不是"已被推翻"（那是 invalidAt）。 */
export type CredStatus = 'candidate' | 'low' | 'limited' | 'stable' | 'conflicted' | 'contested';

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
  /** 主动询问时间戳：null = 未问过；proposeAsk 发问后写入，用于"问过不再问"去重。 */
  askedAt: string | null;
  /** 归档时间：非 null = 已归档，召回跳过但数据保留且可恢复。
   *  可选字段以兼容既有构造处（旧代码不填 = 未归档）。 */
  archivedAt?: string | null;
  /** 静音时间：非 null = 已静音，仅从召回跳过，但仍 active 并参与 consolidation/画像演化。
   *  （区别于 archive 从全部活动路径排除，以及 invalidate 标记不再为真）。静音只改变召回资格，不改变 confidence。
   *  可选字段以兼容既有构造处（旧代码不填 = 未静音）；经 core.memory.muteCognition 置/清。 */
  mutedAt?: string | null;
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
