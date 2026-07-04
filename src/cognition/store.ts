/**
 * 认知存储层（地图 cell 6：判断层）。参考 evidence/store.ts 的 node:sqlite 模式。
 * 两张表：cognition（认知）+ cognition_evidence（溯源链）。
 *
 * 用户可查/改/删（cell 8 规则 10）。consolidate 用 removeBySubject 做"重算替换"（merge 留阶段 2）。
 */
import { DatabaseSync } from '../store/nodeSqliteDriver.ts';
import type { SQLInputValue } from '../store/driver.ts';
import { randomUUID } from 'node:crypto';
import { BUSY_TIMEOUT_MS } from '../store/busyTimeout.ts';
import type {
  Cognition,
  CognitionInput,
  ContentType,
  FormedBy,
  CredStatus,
  EvidenceLink,
  EvidenceRelation,
} from './model.ts';

const SCHEMA = `
CREATE TABLE IF NOT EXISTS cognition (
  id           TEXT    PRIMARY KEY,
  subject_id   TEXT    NOT NULL,
  content      TEXT    NOT NULL,
  content_type TEXT    NOT NULL,
  formed_by    TEXT    NOT NULL,
  confidence   INTEGER NOT NULL,
  cred_status  TEXT    NOT NULL,
  scope        TEXT,
  valid_at     TEXT,
  invalid_at   TEXT,
  asked_at     TEXT,
  archived_at  TEXT,
  created_at   TEXT    NOT NULL,
  updated_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cognition_subject ON cognition(subject_id);
CREATE TABLE IF NOT EXISTS cognition_evidence (
  cognition_id TEXT NOT NULL,
  evidence_id  TEXT NOT NULL,
  relation     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cogev_cog ON cognition_evidence(cognition_id);
`;

interface CognitionRow {
  id: string;
  subject_id: string;
  content: string;
  content_type: string;
  formed_by: string;
  confidence: number;
  cred_status: string;
  scope: string | null;
  valid_at: string | null;
  invalid_at: string | null;
  asked_at: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

function fromRow(r: CognitionRow): Cognition {
  return {
    id: r.id,
    subjectId: r.subject_id,
    content: r.content,
    contentType: r.content_type as ContentType,
    formedBy: r.formed_by as FormedBy,
    confidence: r.confidence,
    credStatus: r.cred_status as CredStatus,
    scope: r.scope,
    validAt: r.valid_at,
    invalidAt: r.invalid_at,
    askedAt: r.asked_at,
    archivedAt: r.archived_at,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}

export interface CognitionPatch {
  content?: string;
  confidence?: number;
  credStatus?: CredStatus;
  scope?: string | null;
  /** 标失效（被纠正/过期）；保留条目可溯源（cell 6 默认保留 + cell 8 反证）。 */
  invalidAt?: string | null;
  /** 主动询问时间戳（阶段 3 M5）：proposeAsk 发问后写入，用于"问过不再问"去重。 */
  askedAt?: string | null;
  /** 归档时间（批次2 受控管理）：非 null = 已归档，召回跳过；传 null 可恢复。经 core.memory.archiveCognition 写入。 */
  archivedAt?: string | null;
}

export interface CognitionStore {
  put(input: CognitionInput): Cognition;
  get(id: string): Cognition | null;
  all(subjectId?: string): Cognition[];
  /** 只取【未失效 且 未归档】的（invalid_at IS NULL AND archived_at IS NULL）——召回 / 写路径读现有认知用。
   *  归档全面雪藏（批次3 用户拍板）：画像更新不当现有认知、不被主动问起、定期清理不碰（保住可恢复）。 */
  active(subjectId: string): Cognition[];
  sourcesOf(cognitionId: string): EvidenceLink[];
  /** 用户主动改一条认知 / 标失效（cell 8 规则 10 / cell 6）。 */
  update(id: string, patch: CognitionPatch): Cognition | null;
  /** 给一条认知补挂证据（增量强化用）。 */
  addEvidence(cognitionId: string, links: EvidenceLink[]): void;
  /** 按【原 id 与全部时间戳】原样插入（导入/恢复用；保留 asked_at/valid_at/invalid_at/created_at/updated_at）。 */
  insert(cognition: Cognition, sources: EvidenceLink[]): void;
  /** 用户主动删一条认知（连溯源链）。 */
  remove(id: string): boolean;
  /** 删某 subject 全部认知（consolidate 重算替换用）。返回删除条数。 */
  removeBySubject(subjectId: string): number;
  close(): void;
}

export class SqliteCognitionStore implements CognitionStore {
  private readonly db: DatabaseSync;
  private readonly ownsDb: boolean;

  /** @param db 文件路径（自开连接，默认 './dla.db'）或共享 DatabaseSync（多 store 共用一条连接、可跨表事务，见 store/openStores.ts）。 */
  constructor(db: string | DatabaseSync = './dla.db') {
    this.ownsDb = typeof db === 'string';
    this.db = typeof db === 'string' ? new DatabaseSync(db) : db;
    // 自开连接才设并发保底；共享连接由 openStores 已设过，别重复设。
    if (this.ownsDb) this.db.exec(`PRAGMA busy_timeout = ${BUSY_TIMEOUT_MS}`);
    this.db.exec(SCHEMA);
    this.migrate();
  }

  /** 幂等迁移：旧库补上后加的列（阶段 3 asked_at / 批次2 archived_at）。新库由 SCHEMA 直接带上。 */
  private migrate(): void {
    const cols = this.db
      .prepare("SELECT name FROM pragma_table_info('cognition')")
      .all() as unknown as Array<{ name: string }>;
    if (!cols.some((c) => c.name === 'asked_at')) {
      this.db.exec('ALTER TABLE cognition ADD COLUMN asked_at TEXT');
    }
    if (!cols.some((c) => c.name === 'archived_at')) {
      this.db.exec('ALTER TABLE cognition ADD COLUMN archived_at TEXT');
    }
  }

  put(input: CognitionInput): Cognition {
    const now = new Date().toISOString();
    const cog: Cognition = {
      id: randomUUID(),
      subjectId: input.subjectId,
      content: input.content,
      contentType: input.contentType,
      formedBy: input.formedBy,
      confidence: input.confidence,
      credStatus: input.credStatus,
      scope: input.scope ?? null,
      validAt: input.validAt ?? null,
      invalidAt: input.invalidAt ?? null,
      askedAt: null, // 新建的认知一律未问过；提问后由 proposeAsk 经 update 写入
      archivedAt: null, // 新建的认知一律未归档；归档走 core.memory.archiveCognition
      createdAt: now,
      updatedAt: now,
    };
    this.db
      .prepare(
        `INSERT INTO cognition (
          id, subject_id, content, content_type, formed_by,
          confidence, cred_status, scope, valid_at, invalid_at, asked_at, archived_at, created_at, updated_at
        ) VALUES ($id,$subject_id,$content,$content_type,$formed_by,
          $confidence,$cred_status,$scope,$valid_at,$invalid_at,$asked_at,$archived_at,$created_at,$updated_at)`,
      )
      // 绑定用【裸键】（无 $ 前缀）：node:sqlite 裸键 / $ 前缀都收，行为不变；
      // 与 evidence store 统一，且为步2 的 better-sqlite3（只认裸键）铺路。
      .run({
        id: cog.id,
        subject_id: cog.subjectId,
        content: cog.content,
        content_type: cog.contentType,
        formed_by: cog.formedBy,
        confidence: cog.confidence,
        cred_status: cog.credStatus,
        scope: cog.scope,
        valid_at: cog.validAt,
        invalid_at: cog.invalidAt,
        asked_at: cog.askedAt,
        archived_at: cog.archivedAt,
        created_at: cog.createdAt,
        updated_at: cog.updatedAt,
      } as unknown as Record<string, SQLInputValue>);

    const links = input.evidence ?? [];
    const stmt = this.db.prepare(
      'INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) VALUES (?,?,?)',
    );
    for (const l of links) stmt.run(cog.id, l.evidenceId, l.relation);
    return cog;
  }

  get(id: string): Cognition | null {
    const row = this.db
      .prepare('SELECT * FROM cognition WHERE id = ?')
      .get(id) as unknown as CognitionRow | undefined;
    return row ? fromRow(row) : null;
  }

  all(subjectId?: string): Cognition[] {
    const rows = (
      subjectId
        ? this.db
            .prepare('SELECT * FROM cognition WHERE subject_id = ? ORDER BY confidence DESC, created_at ASC')
            .all(subjectId)
        : this.db.prepare('SELECT * FROM cognition ORDER BY confidence DESC, created_at ASC').all()
    ) as unknown as CognitionRow[];
    return rows.map(fromRow);
  }

  /** 语义升级（批次3 用户拍板·归档全面雪藏）：active = 未失效【且未归档】。
   *  升级后 consolidate/attribute/proposeAsk/revisitConflicts/expire 等走 active() 的写路径自动跳过归档。 */
  active(subjectId: string): Cognition[] {
    const rows = this.db
      .prepare(
        'SELECT * FROM cognition WHERE subject_id = ? AND invalid_at IS NULL AND archived_at IS NULL ORDER BY confidence DESC, created_at ASC',
      )
      .all(subjectId) as unknown as CognitionRow[];
    return rows.map(fromRow);
  }

  sourcesOf(cognitionId: string): EvidenceLink[] {
    const rows = this.db
      .prepare('SELECT evidence_id, relation FROM cognition_evidence WHERE cognition_id = ?')
      .all(cognitionId) as unknown as Array<{ evidence_id: string; relation: string }>;
    return rows.map((r) => ({ evidenceId: r.evidence_id, relation: r.relation as EvidenceRelation }));
  }

  update(id: string, patch: CognitionPatch): Cognition | null {
    const cur = this.get(id);
    if (!cur) return null;
    const next = {
      content: patch.content ?? cur.content,
      confidence: patch.confidence ?? cur.confidence,
      credStatus: patch.credStatus ?? cur.credStatus,
      scope: patch.scope === undefined ? cur.scope : patch.scope,
      invalidAt: patch.invalidAt === undefined ? cur.invalidAt : patch.invalidAt,
      askedAt: patch.askedAt === undefined ? cur.askedAt : patch.askedAt,
      archivedAt: patch.archivedAt === undefined ? (cur.archivedAt ?? null) : patch.archivedAt,
      updatedAt: new Date().toISOString(),
    };
    this.db
      .prepare(
        'UPDATE cognition SET content=?, confidence=?, cred_status=?, scope=?, invalid_at=?, asked_at=?, archived_at=?, updated_at=? WHERE id=?',
      )
      .run(next.content, next.confidence, next.credStatus, next.scope, next.invalidAt, next.askedAt, next.archivedAt, next.updatedAt, id);
    return this.get(id);
  }

  /** 给一条认知补挂证据（增量"强化"用）。 */
  addEvidence(cognitionId: string, links: EvidenceLink[]): void {
    const stmt = this.db.prepare(
      'INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) VALUES (?,?,?)',
    );
    for (const l of links) stmt.run(cognitionId, l.evidenceId, l.relation);
  }

  insert(cognition: Cognition, sources: EvidenceLink[]): void {
    this.db
      .prepare(
        `INSERT INTO cognition (
          id, subject_id, content, content_type, formed_by,
          confidence, cred_status, scope, valid_at, invalid_at, asked_at, archived_at, created_at, updated_at
        ) VALUES ($id,$subject_id,$content,$content_type,$formed_by,
          $confidence,$cred_status,$scope,$valid_at,$invalid_at,$asked_at,$archived_at,$created_at,$updated_at)`,
      )
      // 裸键绑定（同 put）：node:sqlite 两种键都收，为步2 better-sqlite3 铺路，行为零变化。
      .run({
        id: cognition.id,
        subject_id: cognition.subjectId,
        content: cognition.content,
        content_type: cognition.contentType,
        formed_by: cognition.formedBy,
        confidence: cognition.confidence,
        cred_status: cognition.credStatus,
        scope: cognition.scope,
        valid_at: cognition.validAt,
        invalid_at: cognition.invalidAt,
        asked_at: cognition.askedAt,
        archived_at: cognition.archivedAt ?? null, // 旧包没有此字段 → null（未归档）
        created_at: cognition.createdAt,
        updated_at: cognition.updatedAt,
      } as unknown as Record<string, SQLInputValue>);
    const stmt = this.db.prepare(
      'INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) VALUES (?,?,?)',
    );
    for (const l of sources) stmt.run(cognition.id, l.evidenceId, l.relation);
  }

  remove(id: string): boolean {
    this.db.prepare('DELETE FROM cognition_evidence WHERE cognition_id = ?').run(id);
    const r = this.db.prepare('DELETE FROM cognition WHERE id = ?').run(id);
    return Number(r.changes) > 0;
  }

  removeBySubject(subjectId: string): number {
    this.db
      .prepare(
        'DELETE FROM cognition_evidence WHERE cognition_id IN (SELECT id FROM cognition WHERE subject_id = ?)',
      )
      .run(subjectId);
    const r = this.db.prepare('DELETE FROM cognition WHERE subject_id = ?').run(subjectId);
    return Number(r.changes);
  }

  close(): void {
    if (this.ownsDb) this.db.close(); // 共享连接由 openStores 统一关
  }
}
