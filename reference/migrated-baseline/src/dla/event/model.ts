/**
 * Event 数据模型 —— DLA 的唯一真相（D-003）。
 * 对应决策：D-003（只存事实）/ D-009（字段表 + 正交维度）。
 * 阶段：TASK-01 实现。
 *
 * 关键约束（D-009，编译期焊死）：
 * - 用 event_form + is_directional_change，禁止 event_type 单字段
 * - 禁止字段：weight / pattern / repetition_count / is_correction（皆派生或已并入，不入表）
 */

/** 语义形态：明确陈述 / 纠正（D-009：用 event_form，不用 event_type 单字段）。 */
export type EventForm = 'explicit' | 'correction';

/** 情绪极性（正 / 负 / 中）。 */
export type Sentiment = 'positive' | 'negative' | 'neutral';

/** 来源：用户主动 / 后台观测（权重原料，D-009）。 */
export type SourceType = 'user' | 'observed';

/** 时间取向：长期 / 当下（权重原料，D-009）。 */
export type TemporalOrientation = 'long_term' | 'present';

/**
 * Event —— 唯一真相的一条原始事实记录。
 * 严格对应 D-009 字段表，不多一字段、不少一字段。
 * 内存表示用富类型（boolean / string[]）；落库时由存储层转换（见 store.ts）。
 */
export interface Event {
  // 身份
  id: string;
  /** 发生时间，Unix 毫秒。 */
  timestamp: number;
  /** 用户原话 / 观测原始描述。 */
  raw_content: string;

  // 语义
  event_form: EventForm;
  is_directional_change: boolean;
  /** 主题（召回粗筛用，本任务只存不查）。 */
  topic: string;
  /** 标签列表（召回粗筛用，本任务只存不查）。 */
  tags: string[];
  /** 一句话摘要（召回精挑用，本任务只存不查）。 */
  summary: string;
  sentiment: Sentiment;

  // 权重原料
  source_type: SourceType;
  temporal_orientation: TemporalOrientation;

  // 关联
  related_event_ids: string[];

  // 纠正
  /** 此纠正针对哪条 Event；非纠正时为 null。 */
  correction_target_id: string | null;
}

/**
 * 写入时由调用方提供的字段：除 id / timestamp 外的全部。
 * id 与 timestamp 由存储层在写入时生成（保证唯一与单调）。
 */
export type EventInput = Omit<Event, 'id' | 'timestamp'>;
