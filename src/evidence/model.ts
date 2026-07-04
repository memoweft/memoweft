/**
 * 证据层数据模型（地图 cell 6 / 7：证据是唯一真相，只存"原料"，不存判断）。
 *
 * 字段全是"关于证据的事实"——来源、时间、授权、幂等、纠正指向。
 * 判断类（置信度 / 可信状态 / 适用范围）不在这里，属阶段 1+ 的 cognition 层。
 *
 * 双时态（借 Graphiti）：occurredAt = 事情发生时；recordedAt = MemoWeft 存入时。
 */

/** 来源种类——来源强度分层（地图 cell 8 规则 2）：亲口 > 推测 > 观察。 */
export type SourceKind = 'spoken' | 'inferred' | 'observed';

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
}
