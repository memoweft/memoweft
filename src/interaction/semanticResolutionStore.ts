/**
 * 语义解析存储层。
 *
 * 挂 openStores 的共享连接;单测可传路径 / ':memory:' 自开连接。
 * 一条证据一条语义解析(通过 evidence_id 关联,本表不冗余存 subject_id)。
 *
 * :只建表结构 + insert / 读接口(供便携包与迁移收敛)。**写路径(put)由  resolver 调用**——
 *  不产解析数据(既有对话无解析字段)。resolvedContent / 各解析维度是【解释结果、不是证据】,
 * 解析结果不是证据，永不进入 consolidate 的 support 白名单。
 */
import { DatabaseSync } from '../store/nodeSqliteDriver.ts';
import { randomUUID } from 'node:crypto';
import { BUSY_TIMEOUT_MS } from '../store/busyTimeout.ts';
import { systemClock, type Clock } from '../clock.ts';
import type {
  SemanticResolution,
  SemanticResolutionInput,
  ResponseAct,
  PromptAct,
  PropositionOrigin,
  AssertionStrength,
} from './model.ts';

const SCHEMA = `
CREATE TABLE IF NOT EXISTS semantic_resolution (
  id                 TEXT PRIMARY KEY,
  evidence_id        TEXT NOT NULL,
  resolved_content   TEXT NOT NULL,
  response_act       TEXT,
  prompt_act         TEXT,
  proposition_origin TEXT,
  assertion_strength TEXT,
  required_context   TEXT,
  resolver_version   TEXT NOT NULL,
  created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_semres_evidence ON semantic_resolution(evidence_id);
`;

interface Row {
  id: string;
  evidence_id: string;
  resolved_content: string;
  response_act: string | null;
  prompt_act: string | null;
  proposition_origin: string | null;
  assertion_strength: string | null;
  required_context: string | null;
  resolver_version: string;
  created_at: string;
}

function fromRow(r: Row): SemanticResolution {
  return {
    id: r.id,
    evidenceId: r.evidence_id,
    resolvedContent: r.resolved_content,
    responseAct: r.response_act as ResponseAct | null,
    promptAct: r.prompt_act as PromptAct | null,
    propositionOrigin: r.proposition_origin as PropositionOrigin | null,
    assertionStrength: r.assertion_strength as AssertionStrength | null,
    requiredContext: r.required_context,
    resolverVersion: r.resolver_version,
    createdAt: r.created_at,
  };
}

export interface SemanticResolutionStore {
  /** 持久化一条 resolver 解析结果。 */
  put(input: SemanticResolutionInput): SemanticResolution;
  get(id: string): SemanticResolution | null;
  /** 某条证据的解析(1↔1;无则 null)。 */
  ofEvidence(evidenceId: string): SemanticResolution | null;
  /** 一批证据的解析(便携包导出:按导出的证据集过滤)。 */
  forEvidenceIds(evidenceIds: string[]): SemanticResolution[];
  /** 按原 id 原样插入(便携包导入)。 */
  insert(res: SemanticResolution): void;
  removeByEvidenceIds(evidenceIds: string[]): number;
  close(): void;
}

export class SqliteSemanticResolutionStore implements SemanticResolutionStore {
  private readonly db: DatabaseSync;
  private readonly ownsDb: boolean;
  private readonly clock: Clock;

  constructor(db: string | DatabaseSync = ':memory:', clock: Clock = systemClock) {
    this.ownsDb = typeof db === 'string';
    this.db = typeof db === 'string' ? new DatabaseSync(db) : db;
    if (this.ownsDb) this.db.exec(`PRAGMA busy_timeout = ${BUSY_TIMEOUT_MS}`);
    this.clock = clock;
    this.db.exec(SCHEMA);
  }

  put(input: SemanticResolutionInput): SemanticResolution {
    const res: SemanticResolution = {
      id: randomUUID(),
      evidenceId: input.evidenceId,
      resolvedContent: input.resolvedContent,
      responseAct: input.responseAct ?? null,
      promptAct: input.promptAct ?? null,
      propositionOrigin: input.propositionOrigin ?? null,
      assertionStrength: input.assertionStrength ?? null,
      requiredContext: input.requiredContext ?? null,
      resolverVersion: input.resolverVersion,
      createdAt: this.clock().toISOString(),
    };
    this.insertRow(res);
    return res;
  }

  private insertRow(res: SemanticResolution): void {
    this.db
      .prepare(
        `INSERT INTO semantic_resolution
         (id, evidence_id, resolved_content, response_act, prompt_act, proposition_origin, assertion_strength, required_context, resolver_version, created_at)
         VALUES (?,?,?,?,?,?,?,?,?,?)`,
      )
      .run(
        res.id,
        res.evidenceId,
        res.resolvedContent,
        res.responseAct,
        res.promptAct,
        res.propositionOrigin,
        res.assertionStrength,
        res.requiredContext,
        res.resolverVersion,
        res.createdAt,
      );
  }

  get(id: string): SemanticResolution | null {
    const row = this.db
      .prepare('SELECT * FROM semantic_resolution WHERE id = ?')
      .get(id) as unknown as Row | undefined;
    return row ? fromRow(row) : null;
  }

  ofEvidence(evidenceId: string): SemanticResolution | null {
    const row = this.db
      .prepare(
        'SELECT * FROM semantic_resolution WHERE evidence_id = ? ORDER BY created_at ASC, rowid ASC LIMIT 1',
      )
      .get(evidenceId) as unknown as Row | undefined;
    return row ? fromRow(row) : null;
  }

  forEvidenceIds(evidenceIds: string[]): SemanticResolution[] {
    if (evidenceIds.length === 0) return [];
    const placeholders = evidenceIds.map(() => '?').join(',');
    const rows = this.db
      .prepare(
        `SELECT * FROM semantic_resolution WHERE evidence_id IN (${placeholders}) ORDER BY created_at ASC, rowid ASC`,
      )
      .all(...evidenceIds) as unknown as Row[];
    return rows.map(fromRow);
  }

  insert(res: SemanticResolution): void {
    this.insertRow(res);
  }

  removeByEvidenceIds(evidenceIds: string[]): number {
    if (evidenceIds.length === 0) return 0;
    const placeholders = evidenceIds.map(() => '?').join(',');
    const r = this.db
      .prepare(`DELETE FROM semantic_resolution WHERE evidence_id IN (${placeholders})`)
      .run(...evidenceIds);
    return Number(r.changes);
  }

  close(): void {
    if (this.ownsDb) this.db.close();
  }
}
