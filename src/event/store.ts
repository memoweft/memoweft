/**
 * 事件存储层。参考 evidence/store.ts 的 node:sqlite 模式。
 * 两张表：event（事件）+ event_evidence（事件覆盖了哪些原话证据）。
 */
import { DatabaseSync } from '../store/nodeSqliteDriver.ts';
import { randomUUID } from 'node:crypto';
import { BUSY_TIMEOUT_MS } from '../store/busyTimeout.ts';
import { systemClock, type Clock } from '../clock.ts';
import type { Event, EventInput } from './model.ts';

const SCHEMA = `
CREATE TABLE IF NOT EXISTS event (
  id           TEXT PRIMARY KEY,
  subject_id   TEXT NOT NULL,
  summary      TEXT NOT NULL,
  occurred_at  TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  consolidated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_event_subject ON event(subject_id);
CREATE TABLE IF NOT EXISTS event_evidence (
  event_id    TEXT NOT NULL,
  evidence_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_evev_event ON event_evidence(event_id);
`;

interface EventRow {
  id: string;
  subject_id: string;
  summary: string;
  occurred_at: string;
  created_at: string;
}

function fromRow(r: EventRow): Event {
  return {
    id: r.id,
    subjectId: r.subject_id,
    summary: r.summary,
    occurredAt: r.occurred_at,
    createdAt: r.created_at,
  };
}

export interface EventStore {
  put(input: EventInput): Event;
  get(id: string): Event | null;
  all(subjectId?: string): Event[];
  /** 某事件覆盖的原话证据 id。 */
  evidenceOf(eventId: string): string[];
  /** 某 subject 已被某事件覆盖的全部证据 id（算"未整理"用）。 */
  coveredEvidenceIds(subjectId: string): string[];
  /** 还没被消化进画像的事件（增量 consolidate 用）。 */
  unconsolidated(subjectId: string): Event[];
  /** 标记一批事件已消化进画像。 */
  markConsolidated(ids: string[]): void;
  /** 按【原 id 与时间戳】原样插入（导入/恢复用）。consolidated 缺省 false；导入已带 cognition 的包时应传 true 防重复消化。 */
  insert(event: Event, evidenceIds: string[], opts?: { consolidated?: boolean }): void;
  remove(id: string): boolean;
  removeBySubject(subjectId: string): number;
  close(): void;
}

export class SqliteEventStore implements EventStore {
  private readonly db: DatabaseSync;
  private readonly ownsDb: boolean;
  /** 落库时间源（created_at）；可注入以求确定性/时间旅行，缺省=真实系统时间。 */
  private readonly clock: Clock;

  /** @param db 文件路径（自开连接，默认 './dla.db'）或共享 DatabaseSync（多 store 共用一条连接，见 store/openStores.ts）。
   *  @param clock 时间源；缺省真实系统时间。 */
  constructor(db: string | DatabaseSync = './dla.db', clock: Clock = systemClock) {
    this.ownsDb = typeof db === 'string';
    this.db = typeof db === 'string' ? new DatabaseSync(db) : db;
    // 自开连接才设并发保底；共享连接由 openStores 已设过，别重复设。
    if (this.ownsDb) this.db.exec(`PRAGMA busy_timeout = ${BUSY_TIMEOUT_MS}`);
    this.clock = clock;
    this.db.exec(SCHEMA);
    // 迁移：旧库 event 表可能缺 consolidated 列（增量消化追踪）。
    const cols = this.db.prepare('PRAGMA table_info(event)').all() as unknown as Array<{
      name: string;
    }>;
    if (!cols.some((c) => c.name === 'consolidated')) {
      this.db.exec('ALTER TABLE event ADD COLUMN consolidated INTEGER NOT NULL DEFAULT 0');
    }
  }

  put(input: EventInput): Event {
    const now = this.clock().toISOString();
    const ev: Event = {
      id: randomUUID(),
      subjectId: input.subjectId,
      summary: input.summary,
      occurredAt: input.occurredAt,
      createdAt: now,
    };
    this.db
      .prepare(
        'INSERT INTO event (id, subject_id, summary, occurred_at, created_at) VALUES (?,?,?,?,?)',
      )
      .run(ev.id, ev.subjectId, ev.summary, ev.occurredAt, ev.createdAt);
    const stmt = this.db.prepare('INSERT INTO event_evidence (event_id, evidence_id) VALUES (?,?)');
    for (const eid of input.evidenceIds) stmt.run(ev.id, eid);
    return ev;
  }

  get(id: string): Event | null {
    const row = this.db.prepare('SELECT * FROM event WHERE id = ?').get(id) as unknown as
      EventRow | undefined;
    return row ? fromRow(row) : null;
  }

  all(subjectId?: string): Event[] {
    const rows = (subjectId
      ? this.db
          .prepare('SELECT * FROM event WHERE subject_id = ? ORDER BY occurred_at ASC')
          .all(subjectId)
      : this.db
          .prepare('SELECT * FROM event ORDER BY occurred_at ASC')
          .all()) as unknown as EventRow[];
    return rows.map(fromRow);
  }

  evidenceOf(eventId: string): string[] {
    const rows = this.db
      .prepare('SELECT evidence_id FROM event_evidence WHERE event_id = ?')
      .all(eventId) as unknown as Array<{ evidence_id: string }>;
    return rows.map((r) => r.evidence_id);
  }

  coveredEvidenceIds(subjectId: string): string[] {
    const rows = this.db
      .prepare(
        'SELECT evidence_id FROM event_evidence WHERE event_id IN (SELECT id FROM event WHERE subject_id = ?)',
      )
      .all(subjectId) as unknown as Array<{ evidence_id: string }>;
    return rows.map((r) => r.evidence_id);
  }

  unconsolidated(subjectId: string): Event[] {
    const rows = this.db
      .prepare(
        'SELECT * FROM event WHERE subject_id = ? AND consolidated = 0 ORDER BY occurred_at ASC',
      )
      .all(subjectId) as unknown as EventRow[];
    return rows.map(fromRow);
  }

  markConsolidated(ids: string[]): void {
    if (ids.length === 0) return;
    const stmt = this.db.prepare('UPDATE event SET consolidated = 1 WHERE id = ?');
    for (const id of ids) stmt.run(id);
  }

  insert(event: Event, evidenceIds: string[], opts: { consolidated?: boolean } = {}): void {
    const consolidated = opts.consolidated ? 1 : 0;
    this.db
      .prepare(
        'INSERT INTO event (id, subject_id, summary, occurred_at, created_at, consolidated) VALUES (?,?,?,?,?,?)',
      )
      .run(
        event.id,
        event.subjectId,
        event.summary,
        event.occurredAt,
        event.createdAt,
        consolidated,
      );
    const stmt = this.db.prepare('INSERT INTO event_evidence (event_id, evidence_id) VALUES (?,?)');
    for (const eid of evidenceIds) stmt.run(event.id, eid);
  }

  remove(id: string): boolean {
    this.db.prepare('DELETE FROM event_evidence WHERE event_id = ?').run(id);
    const r = this.db.prepare('DELETE FROM event WHERE id = ?').run(id);
    return Number(r.changes) > 0;
  }

  removeBySubject(subjectId: string): number {
    this.db
      .prepare(
        'DELETE FROM event_evidence WHERE event_id IN (SELECT id FROM event WHERE subject_id = ?)',
      )
      .run(subjectId);
    const r = this.db.prepare('DELETE FROM event WHERE subject_id = ?').run(subjectId);
    return Number(r.changes);
  }

  close(): void {
    if (this.ownsDb) this.db.close(); // 共享连接由 openStores 统一关
  }
}
