/**
 * 证据存储层（地图 cell 6 / 7：证据是 MemoWeft 自有的唯一真相）。
 * 参考 reference/migrated-baseline/event/store.ts 的 node:sqlite 模式。
 *
 * 职责：只做存储（建表 + 写入 + 读取 + 时间窗查询）。不做判断（召回 / 画像 / 冲突都不在此层）。
 * 授权位、双时态时间在写入时按规则补默认（见 put）。
 * 幂等：带 originId 的重复写入只存一次（防重试重复落库）。
 */
import { DatabaseSync, type SQLInputValue } from 'node:sqlite';
import { randomUUID } from 'node:crypto';
import { config, cloudReadDefault, type MemoWeftConfig } from '../config.ts';
import type { Evidence, EvidenceInput, SourceKind } from './model.ts';

const SCHEMA = `
CREATE TABLE IF NOT EXISTS evidence (
  id                   TEXT    PRIMARY KEY,
  subject_id           TEXT    NOT NULL,
  source_kind          TEXT    NOT NULL,
  host_id              TEXT    NOT NULL,
  origin_id            TEXT,
  occurred_at          TEXT    NOT NULL,
  recorded_at          TEXT    NOT NULL,
  raw_content          TEXT    NOT NULL,
  summary              TEXT    NOT NULL,
  allow_local_read     INTEGER NOT NULL,
  allow_cloud_read     INTEGER NOT NULL,
  allow_inference      INTEGER NOT NULL,
  corrects_evidence_id TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_evidence_origin
  ON evidence(origin_id) WHERE origin_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_evidence_occurred ON evidence(occurred_at);
`;

interface EvidenceRow {
  id: string;
  subject_id: string;
  source_kind: string;
  host_id: string;
  origin_id: string | null;
  occurred_at: string;
  recorded_at: string;
  raw_content: string;
  summary: string;
  allow_local_read: number;
  allow_cloud_read: number;
  allow_inference: number;
  corrects_evidence_id: string | null;
}

function toRow(e: Evidence): EvidenceRow {
  return {
    id: e.id,
    subject_id: e.subjectId,
    source_kind: e.sourceKind,
    host_id: e.hostId,
    origin_id: e.originId,
    occurred_at: e.occurredAt,
    recorded_at: e.recordedAt,
    raw_content: e.rawContent,
    summary: e.summary,
    allow_local_read: e.allowLocalRead ? 1 : 0,
    allow_cloud_read: e.allowCloudRead ? 1 : 0,
    allow_inference: e.allowInference ? 1 : 0,
    corrects_evidence_id: e.correctsEvidenceId,
  };
}

function fromRow(r: EvidenceRow): Evidence {
  return {
    id: r.id,
    subjectId: r.subject_id,
    sourceKind: r.source_kind as SourceKind,
    hostId: r.host_id,
    originId: r.origin_id,
    occurredAt: r.occurred_at,
    recordedAt: r.recorded_at,
    rawContent: r.raw_content,
    summary: r.summary,
    allowLocalRead: r.allow_local_read === 1,
    allowCloudRead: r.allow_cloud_read === 1,
    allowInference: r.allow_inference === 1,
    correctsEvidenceId: r.corrects_evidence_id,
  };
}

/** 证据存储接口（可替换底座的存储侧；阶段 0 实现 = SqliteEvidenceStore）。 */
export interface EvidenceStore {
  /** 写入一条；带 originId 时幂等（已存在则返回原条，不重复写）。 */
  put(input: EvidenceInput): Evidence;
  get(id: string): Evidence | null;
  all(): Evidence[];
  /** 按发生时间（occurredAt）区间取回，升序——时间窗粗筛用。 */
  byTimeRange(fromIso: string, toIso: string): Evidence[];
  /** 用户主动修改一条证据的原文 / 摘要 / 授权位（cell 8 规则 10；6-A 记忆管理页可改 cloud/inference 授权）。返回更新后的，不存在返回 null。 */
  update(
    id: string,
    patch: { rawContent?: string; summary?: string; allowCloudRead?: boolean; allowInference?: boolean },
  ): Evidence | null;
  /** 用户主动删除一条证据（cell 6 条件性真删；非系统自动删）。返回是否删除。 */
  remove(id: string): boolean;
  /** 按幂等键 originId 查回一条（摄入层判重用：已存在则跳过、不重复落库）。不存在返回 null。 */
  findByOrigin(originId: string): Evidence | null;
  /** 按【原 id 与时间戳】原样插入（导入/恢复用；不生成新 id）。调用方须先判重：id 或 originId 已存在会抛约束错。 */
  insert(evidence: Evidence): void;
  close(): void;
}

export class SqliteEvidenceStore implements EvidenceStore {
  private readonly db: DatabaseSync;
  private readonly ownsDb: boolean;
  /** 本 store 的配置（put 补默认授权位用）；可注入（P2-5），缺省=全局单例。 */
  private readonly cfg: MemoWeftConfig;

  /**
   * @param db SQLite 文件路径（自开连接，默认 './dla.db'；测试传 ':memory:'），
   *   或一个【已打开的共享 DatabaseSync】——多个 store 共用一条连接时才能跨表事务（见 store/openStores.ts）。
   * @param cfg 可注入配置（P2-5 config 去单例）：不传 = 用全局单例；put 补授权默认（evidenceDefaults / privacyMode）按这份。
   */
  constructor(db: string | DatabaseSync = './dla.db', cfg: MemoWeftConfig = config) {
    this.ownsDb = typeof db === 'string';
    this.db = typeof db === 'string' ? new DatabaseSync(db) : db;
    this.cfg = cfg;
    this.db.exec(SCHEMA);
  }

  put(input: EvidenceInput): Evidence {
    // 幂等：带 originId 且已存在 → 返回原条，不重复落库。
    if (input.originId) {
      const existing = this.findByOrigin(input.originId);
      if (existing) return existing;
    }

    const recordedAt = new Date().toISOString();
    const evidence: Evidence = {
      id: randomUUID(),
      subjectId: input.subjectId,
      sourceKind: input.sourceKind,
      hostId: input.hostId,
      originId: input.originId ?? null,
      occurredAt: input.occurredAt ?? recordedAt,
      recordedAt,
      rawContent: input.rawContent,
      summary: input.summary ?? input.rawContent, // v1：摘要先等于原文
      allowLocalRead: input.allowLocalRead ?? this.cfg.evidenceDefaults.allowLocalRead,
      allowCloudRead: input.allowCloudRead ?? cloudReadDefault(this.cfg), // 跟随（注入的）配置
      allowInference: input.allowInference ?? this.cfg.evidenceDefaults.allowInference,
      correctsEvidenceId: input.correctsEvidenceId ?? null,
    };

    const row = toRow(evidence);
    this.db
      .prepare(
        `INSERT INTO evidence (
          id, subject_id, source_kind, host_id, origin_id,
          occurred_at, recorded_at, raw_content, summary,
          allow_local_read, allow_cloud_read, allow_inference, corrects_evidence_id
        ) VALUES (
          $id, $subject_id, $source_kind, $host_id, $origin_id,
          $occurred_at, $recorded_at, $raw_content, $summary,
          $allow_local_read, $allow_cloud_read, $allow_inference, $corrects_evidence_id
        )`,
      )
      .run(row as unknown as Record<string, SQLInputValue>);
    return evidence;
  }

  get(id: string): Evidence | null {
    const row = this.db
      .prepare('SELECT * FROM evidence WHERE id = ?')
      .get(id) as unknown as EvidenceRow | undefined;
    return row ? fromRow(row) : null;
  }

  all(): Evidence[] {
    const rows = this.db
      .prepare('SELECT * FROM evidence ORDER BY recorded_at ASC, rowid ASC')
      .all() as unknown as EvidenceRow[];
    return rows.map(fromRow);
  }

  byTimeRange(fromIso: string, toIso: string): Evidence[] {
    const rows = this.db
      .prepare(
        'SELECT * FROM evidence WHERE occurred_at >= ? AND occurred_at <= ? ORDER BY occurred_at ASC, rowid ASC',
      )
      .all(fromIso, toIso) as unknown as EvidenceRow[];
    return rows.map(fromRow);
  }

  update(
    id: string,
    patch: { rawContent?: string; summary?: string; allowCloudRead?: boolean; allowInference?: boolean },
  ): Evidence | null {
    const cur = this.get(id);
    if (!cur) return null;
    const rawContent = patch.rawContent ?? cur.rawContent;
    const summary = patch.summary ?? cur.summary;
    // 授权位（6-A）：未提供则保持原值；布尔转 0/1 落库（表结构不变）。
    const allowCloudRead = patch.allowCloudRead ?? cur.allowCloudRead;
    const allowInference = patch.allowInference ?? cur.allowInference;
    this.db
      .prepare(
        'UPDATE evidence SET raw_content = ?, summary = ?, allow_cloud_read = ?, allow_inference = ? WHERE id = ?',
      )
      .run(rawContent, summary, allowCloudRead ? 1 : 0, allowInference ? 1 : 0, id);
    return this.get(id);
  }

  remove(id: string): boolean {
    const r = this.db.prepare('DELETE FROM evidence WHERE id = ?').run(id);
    return Number(r.changes) > 0;
  }

  findByOrigin(originId: string): Evidence | null {
    const row = this.db
      .prepare('SELECT * FROM evidence WHERE origin_id = ?')
      .get(originId) as unknown as EvidenceRow | undefined;
    return row ? fromRow(row) : null;
  }

  insert(evidence: Evidence): void {
    const row = toRow(evidence);
    this.db
      .prepare(
        `INSERT INTO evidence (
          id, subject_id, source_kind, host_id, origin_id,
          occurred_at, recorded_at, raw_content, summary,
          allow_local_read, allow_cloud_read, allow_inference, corrects_evidence_id
        ) VALUES (
          $id, $subject_id, $source_kind, $host_id, $origin_id,
          $occurred_at, $recorded_at, $raw_content, $summary,
          $allow_local_read, $allow_cloud_read, $allow_inference, $corrects_evidence_id
        )`,
      )
      .run(row as unknown as Record<string, SQLInputValue>);
  }

  close(): void {
    if (this.ownsDb) this.db.close(); // 共享连接由 openStores 统一关；单个 store 不关，免得关掉别人还在用的连接
  }
}
