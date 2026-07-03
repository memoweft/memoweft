/**
 * Event 存储层 —— Node 内置 node:sqlite（D-021，经 2026-06-21 修订改用内置引擎）。
 * 对应决策：D-003 / D-006（只生不灭：不提供物理删除接口）/ D-008（分数整数千分制）/ D-021。
 * 阶段：TASK-01 实现。
 *
 * 职责边界：只做存储（建表 + 写入一条 + 读取）。
 * 不做任何判断逻辑（权重 / 召回 / State / Profile 一律不在本层）。
 */

import { DatabaseSync, type SQLInputValue } from 'node:sqlite';
import { randomUUID } from 'node:crypto';
import type { Event, EventInput } from './model.ts';

/** 建表 DDL —— 严格对应 D-009 字段表，不多不少。 */
const SCHEMA = `
CREATE TABLE IF NOT EXISTS event (
  id                    TEXT    PRIMARY KEY,
  timestamp             INTEGER NOT NULL,
  raw_content           TEXT    NOT NULL,
  event_form            TEXT    NOT NULL,
  is_directional_change INTEGER NOT NULL,
  topic                 TEXT    NOT NULL,
  tags                  TEXT    NOT NULL,
  summary               TEXT    NOT NULL,
  sentiment             TEXT    NOT NULL,
  source_type           TEXT    NOT NULL,
  temporal_orientation  TEXT    NOT NULL,
  related_event_ids     TEXT    NOT NULL,
  correction_target_id  TEXT
);
`;

/** 数据库里一行的原始形状（布尔存 0/1，列表存 JSON 字符串）。 */
interface EventRow {
  id: string;
  timestamp: number;
  raw_content: string;
  event_form: string;
  is_directional_change: number;
  topic: string;
  tags: string;
  summary: string;
  sentiment: string;
  source_type: string;
  temporal_orientation: string;
  related_event_ids: string;
  correction_target_id: string | null;
}

/** 把内存 Event 映射成 DB 行（boolean→0/1，string[]→JSON）。 */
function toRow(e: Event): EventRow {
  return {
    id: e.id,
    timestamp: e.timestamp,
    raw_content: e.raw_content,
    event_form: e.event_form,
    is_directional_change: e.is_directional_change ? 1 : 0,
    topic: e.topic,
    tags: JSON.stringify(e.tags),
    summary: e.summary,
    sentiment: e.sentiment,
    source_type: e.source_type,
    temporal_orientation: e.temporal_orientation,
    related_event_ids: JSON.stringify(e.related_event_ids),
    correction_target_id: e.correction_target_id,
  };
}

/** 把 DB 行还原成内存 Event（0/1→boolean，JSON→string[]）。 */
function fromRow(r: EventRow): Event {
  return {
    id: r.id,
    timestamp: r.timestamp,
    raw_content: r.raw_content,
    event_form: r.event_form as Event['event_form'],
    is_directional_change: r.is_directional_change === 1,
    topic: r.topic,
    tags: JSON.parse(r.tags) as string[],
    summary: r.summary,
    sentiment: r.sentiment as Event['sentiment'],
    source_type: r.source_type as Event['source_type'],
    temporal_orientation: r.temporal_orientation as Event['temporal_orientation'],
    related_event_ids: JSON.parse(r.related_event_ids) as string[],
    correction_target_id: r.correction_target_id,
  };
}

/**
 * Event 存储层。
 *
 * 注意（D-006 只生不灭）：本类刻意【不提供】任何物理删除 / 清空接口。
 * 这不是遗漏，而是设计——Event 只生不灭。
 */
export class EventStore {
  private readonly db: DatabaseSync;

  /** @param dbPath 数据库文件路径；测试传 ':memory:' 用内存库。默认 './dla.db'。 */
  constructor(dbPath: string = './dla.db') {
    this.db = new DatabaseSync(dbPath);
    this.db.exec(SCHEMA);
  }

  /**
   * 写入一条 Event。id 与 timestamp 由本层生成。
   * @returns 新生成的 Event id。
   */
  write(input: EventInput): string {
    const event: Event = {
      ...input,
      id: randomUUID(),
      timestamp: Date.now(),
    };
    const row = toRow(event);
    this.db
      .prepare(
        `INSERT INTO event (
          id, timestamp, raw_content, event_form, is_directional_change,
          topic, tags, summary, sentiment, source_type,
          temporal_orientation, related_event_ids, correction_target_id
        ) VALUES (
          $id, $timestamp, $raw_content, $event_form, $is_directional_change,
          $topic, $tags, $summary, $sentiment, $source_type,
          $temporal_orientation, $related_event_ids, $correction_target_id
        )`,
      )
      .run(row as unknown as Record<string, SQLInputValue>);
    return event.id;
  }

  /** 按 id 读取一条；读不到返回 null。 */
  read(id: string): Event | null {
    const row = this.db
      .prepare('SELECT * FROM event WHERE id = ?')
      .get(id) as unknown as EventRow | undefined;
    return row ? fromRow(row) : null;
  }

  /** 读取全部，按 timestamp 升序（供测试与后续链路使用）。 */
  readAll(): Event[] {
    const rows = this.db
      .prepare('SELECT * FROM event ORDER BY timestamp ASC')
      .all() as unknown as EventRow[];
    return rows.map(fromRow);
  }

  /**
   * 取库里现有的全部去重 topic（召回 A1 用：让模型在真实 topic 中挑相关）。只读（D-003）。
   *
   * 口子（D-006 校准点）：当前返回全部 distinct topic。自用规模（几十个）足够。
   * 将来 topic 列表变长成为问题时，在此加按近期/频次截断的参数，调用方无需改。
   * @param limit 可选上限；不传则全取。
   */
  distinctTopics(limit?: number): string[] {
    const sql = limit
      ? `SELECT topic, MAX(timestamp) AS t FROM event GROUP BY topic ORDER BY t DESC LIMIT ?`
      : `SELECT DISTINCT topic FROM event`;
    const rows = (limit
      ? this.db.prepare(sql).all(limit)
      : this.db.prepare(sql).all()) as unknown as Array<{ topic: string }>;
    return rows.map((r) => r.topic);
  }

  /**
   * 按一组 topic 精确取回 Event（召回 A1 SQL 粗筛）。只读（D-003）。
   * 用 SQL `IN` 查询，不把全表读进内存再过滤。
   * 排序：时间倒序（最近在前）——TODO(TASK-05) 换成权重排序。
   * @param topics 目标 topic 列表；空数组直接返回空。
   * @param limit  候选上限（防极端情况 IN 出太多）。
   */
  findByTopics(topics: string[], limit?: number): Event[] {
    if (topics.length === 0) return [];
    const placeholders = topics.map(() => '?').join(', ');
    const sql =
      `SELECT * FROM event WHERE topic IN (${placeholders}) ORDER BY timestamp DESC` +
      (limit ? ` LIMIT ${Number(limit)}` : '');
    const rows = this.db.prepare(sql).all(...topics) as unknown as EventRow[];
    return rows.map(fromRow);
  }

  /**
   * 统计某 topic 下的 Event 条数（权重·重复度近似用）。只读（D-003）。
   * 用 SQL COUNT，不取回行。
   */
  countByTopic(topic: string): number {
    const row = this.db
      .prepare('SELECT COUNT(*) AS n FROM event WHERE topic = ?')
      .get(topic) as unknown as { n: number };
    return row.n;
  }

  /** 关闭数据库连接。 */
  close(): void {
    this.db.close();
  }
}
