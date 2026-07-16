/**
 * 证据层数据模型（地图 cell 6 / 7：证据是唯一真相，只存"原料"，不存判断）。
 *
 * 字段全是"关于证据的事实"——来源、时间、授权、幂等、纠正指向。
 * 判断类（置信度 / 可信状态 / 适用范围）不在这里，属阶段 1+ 的 cognition 层。
 *
 * 双时态（借 Graphiti）：occurredAt = 事情发生时；recordedAt = MemoWeft 存入时。
 */

/** 来源种类——来源强度分层（地图 cell 8 规则 2）：亲口 > 推测 > 观察/工具。
 *  'tool'（AD-3/D-0013）= 工具执行的【返回结果】（外部客观数据）；LLM 的调用意图/入参是助手输出，禁摄入（铁律 3a）。 */
export type SourceKind = 'spoken' | 'inferred' | 'observed' | 'tool';

/** 一条证据（落库后的完整形状）。 */
export interface Evidence {
  id: string;
  /** 哪个用户的证据（多 subject）。 */
  subjectId: string;
  /** 亲口 / AI 推测 / 行为观察。 */
  sourceKind: SourceKind;
  /** 来自哪个宿主（星瑶 / Hermes / …）。 */
  hostId: string;
  /** 原始消息号，幂等防重写；可空。 */
  originId: string | null;
  /** 事情实际发生时间（ISO）。 */
  occurredAt: string;
  /** MemoWeft 收到并存下来的时间（ISO）。 */
  recordedAt: string;
  /** 用户原话 / 原始观察。 */
  rawContent: string;
  /** 召回用摘要；v1 = rawContent，阶段 1 起 LLM 抽取。 */
  summary: string;
  /** 能否提供给本地 Agent。 */
  allowLocalRead: boolean;
  /** 能否发送给云端模型。 */
  allowCloudRead: boolean;
  /** 能否据此推测画像 / 动机。 */
  allowInference: boolean;
  /** 若是在纠正旧记录，指向旧证据；逻辑在阶段 3（M6），现可空。 */
  correctsEvidenceId: string | null;
}

/**
 * 写入证据的入参。id / recordedAt 由存储层生成；
 * occurredAt / summary / 授权位 缺省时由存储层按规则补默认。
 * 授权缺省按 sourceKind 分流：observed 缺省授权 = observedDefaults（local✓/cloud✗/infer✓）；
 *   spoken/inferred 走通用默认（evidenceDefaults + cloud 跟随 privacyMode）。显式传值永远优先。
 */
export interface EvidenceInput {
  subjectId: string;
  sourceKind: SourceKind;
  hostId: string;
  originId?: string | null;
  occurredAt?: string;
  rawContent: string;
  summary?: string;
  allowLocalRead?: boolean;
  allowCloudRead?: boolean;
  allowInference?: boolean;
  correctsEvidenceId?: string | null;
  /**
   * 上一轮【AI 那句】的原文（附和/AI 上下文机制,D-0033 Phase 1b)——**只写入,不读回**。
   * 用途:孤儿回应("AI:你喜欢爬山吧? 用户:是的")的信息只藏在 AI 那句里;把它作为【只读上下文】
   *   注入 distill/consolidate,让附和产得出 `confirmed` 认知。**永不成为证据、永不给证据 id**(3a/3d)。
   * 关键(结构墙):此字段**只在 EvidenceInput(写入端),不在 Evidence(读结构),也不进 fromRow** →
   *   落进 SQLite 的 `preceding_ai_context` 列后,任何读回路径(exportBundle/listEvidence/MCP/host/TurnOutcome)
   *   物理上都拿不到它 → AI 话不外泄=结构保证(不靠"记得剥离")。distill/consolidate 要用它,经专用只读方法
   *   `EvidenceStore.precedingAiContextOf(id)` 取,且只对已过隐私门(tier+inference)的证据行注入。
   * 仅走 Conversation 的路会设它(handleConversationTurn 先存后答·working memory 尚存上一轮 AI);
   *   裸 ingest/observed/tool 路不设(缺省 null)。
   */
  precedingAiContext?: string | null;
}
