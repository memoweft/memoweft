/**
 * 交互上下文存储层(v0.6 · D-0034)。
 *
 * 挂 openStores 的共享连接(与三层 store 同连接,才能同事务);单测可传路径 / ':memory:' 自开连接。
 * 只存「某段用户可见上下文」,**不产 Cognition、永不成为证据**(铁律 3a);内容永不进 consolidate 的 support 白名单。
 *
 * 幂等:record 按 context_hash 写入前查重(非 DB 唯一约束——避免便携包跨库导入时同内容不同 id 撞约束)。
 */
import { DatabaseSync } from '../store/nodeSqliteDriver.ts';
import { randomUUID, createHash } from 'node:crypto';
import { BUSY_TIMEOUT_MS } from '../store/busyTimeout.ts';
import { systemClock, type Clock } from '../clock.ts';
import type { InteractionContext, InteractionContextInput, VisibleTurn } from './model.ts';

const SCHEMA = `
CREATE TABLE IF NOT EXISTS interaction_context (
  id              TEXT PRIMARY KEY,
  subject_id      TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  episode_id      TEXT NOT NULL,
  context_json    TEXT NOT NULL,
  context_hash    TEXT NOT NULL,
  created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ictx_subject ON interaction_context(subject_id);
CREATE INDEX IF NOT EXISTS ix_ictx_conversation ON interaction_context(conversation_id);
CREATE INDEX IF NOT EXISTS ix_ictx_hash ON interaction_context(context_hash);
`;

interface Row {
  id: string;
  subject_id: string;
  conversation_id: string;
  episode_id: string;
  context_json: string;
  context_hash: string;
  created_at: string;
}

function fromRow(r: Row): InteractionContext {
  return {
    id: r.id,
    subjectId: r.subject_id,
    conversationId: r.conversation_id,
    episodeId: r.episode_id,
    context: JSON.parse(r.context_json) as VisibleTurn[],
    contextHash: r.context_hash,
    createdAt: r.created_at,
  };
}

/** 内容指纹(sha256 over 规范化 JSON)——幂等去重用。 */
export function hashContext(context: VisibleTurn[]): string {
  return createHash('sha256').update(JSON.stringify(context)).digest('hex');
}

export interface InteractionContextStore {
  /** 落一条上下文快照;按 context_hash 写入前查重(同一快照重复写返回已存在的,不重复落库)。 */
  record(input: InteractionContextInput): InteractionContext;
  get(id: string): InteractionContext | null;
  /** 某 subject 的全部(便携包导出用);缺省全 subject。 */
  all(subjectId?: string): InteractionContext[];
  byConversation(conversationId: string): InteractionContext[];
  /** 按原 id 原样插入(便携包导入);调用方须先判重(id 已存在会撞主键)。 */
  insert(ctx: InteractionContext): void;
  removeBySubject(subjectId: string): number;
  close(): void;
}

export class SqliteInteractionContextStore implements InteractionContextStore {
  private readonly db: DatabaseSync;
  private readonly ownsDb: boolean;
  private readonly clock: Clock;

  /** @param db 共享 DatabaseSync(挂 openStores 连接)或路径 / ':memory:'(自开,单测用)。
   *  @param clock 落库时间源;缺省真实系统时间。 */
  constructor(db: string | DatabaseSync = ':memory:', clock: Clock = systemClock) {
    this.ownsDb = typeof db === 'string';
    this.db = typeof db === 'string' ? new DatabaseSync(db) : db;
    if (this.ownsDb) this.db.exec(`PRAGMA busy_timeout = ${BUSY_TIMEOUT_MS}`);
    this.clock = clock;
    this.db.exec(SCHEMA);
  }

  record(input: InteractionContextInput): InteractionContext {
    const contextHash = hashContext(input.context);
    const existing = this.db
      .prepare('SELECT * FROM interaction_context WHERE context_hash = ?')
      .get(contextHash) as unknown as Row | undefined;
    if (existing) return fromRow(existing);
    const ctx: InteractionContext = {
      id: randomUUID(),
      subjectId: input.subjectId,
      conversationId: input.conversationId,
      episodeId: input.episodeId,
      context: input.context,
      contextHash,
      createdAt: this.clock().toISOString(),
    };
    this.insertRow(ctx);
    return ctx;
  }

  private insertRow(ctx: InteractionContext): void {
    this.db
      .prepare(
        `INSERT INTO interaction_context (id, subject_id, conversation_id, episode_id, context_json, context_hash, created_at)
         VALUES (?,?,?,?,?,?,?)`,
      )
      .run(
        ctx.id,
        ctx.subjectId,
        ctx.conversationId,
        ctx.episodeId,
        JSON.stringify(ctx.context),
        ctx.contextHash,
        ctx.createdAt,
      );
  }

  get(id: string): InteractionContext | null {
    const row = this.db
      .prepare('SELECT * FROM interaction_context WHERE id = ?')
      .get(id) as unknown as Row | undefined;
    return row ? fromRow(row) : null;
  }

  all(subjectId?: string): InteractionContext[] {
    const rows = (
      subjectId
        ? this.db
            .prepare('SELECT * FROM interaction_context WHERE subject_id = ? ORDER BY created_at ASC, rowid ASC')
            .all(subjectId)
        : this.db.prepare('SELECT * FROM interaction_context ORDER BY created_at ASC, rowid ASC').all()
    ) as unknown as Row[];
    return rows.map(fromRow);
  }

  byConversation(conversationId: string): InteractionContext[] {
    const rows = this.db
      .prepare('SELECT * FROM interaction_context WHERE conversation_id = ? ORDER BY created_at ASC, rowid ASC')
      .all(conversationId) as unknown as Row[];
    return rows.map(fromRow);
  }

  insert(ctx: InteractionContext): void {
    this.insertRow(ctx);
  }

  removeBySubject(subjectId: string): number {
    const r = this.db.prepare('DELETE FROM interaction_context WHERE subject_id = ?').run(subjectId);
    return Number(r.changes);
  }

  close(): void {
    if (this.ownsDb) this.db.close(); // 共享连接由 openStores 统一关
  }
}
