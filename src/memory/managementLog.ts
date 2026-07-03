/**
 * 受控管理审计表（架构归位·批次2）：每个管理操作（失效/归档/合并/删除/授权变更）落一行"谁被怎么了、为什么"。
 *
 * 为什么独立成表：管理操作改的是用户记忆资产，原因（reason）必须留痕、可追溯（路线 §5.3
 * "记录操作原因"）；不塞进 cognition/evidence 表——那两张表存的是记忆本体，不是操作历史。
 * 挂在 openStores 的共享连接上（与三个 store 同连接，管理操作的写+审计能包进一个事务）。
 */
import { DatabaseSync } from 'node:sqlite';

const SCHEMA = `
CREATE TABLE IF NOT EXISTS management_log (
  op          TEXT NOT NULL,
  target_kind TEXT NOT NULL,
  target_id   TEXT NOT NULL,
  reason      TEXT NOT NULL,
  detail      TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mgmt_target ON management_log(target_id);
`;

/** 一条审计行（读出形状；detail 已从 JSON 解析）。 */
export interface ManagementLogEntry {
  /** 操作名：invalidate / archive / merge / remove_evidence / remove_cognition / update_authorization。 */
  op: string;
  /** 目标类型：cognition / evidence。 */
  targetKind: string;
  targetId: string;
  reason: string;
  /** 操作细节快照（如 merge 的 {sourceId,targetId}、force 删除时的 blockers）；无则 null。 */
  detail: Record<string, unknown> | null;
  createdAt: string;
}

export interface ManagementLog {
  /** 落一行审计（createdAt 由本方法生成）。 */
  append(e: Omit<ManagementLogEntry, 'createdAt'>): ManagementLogEntry;
  /** 读审计行（可按 targetId 过滤；按落表顺序升序）。 */
  list(targetId?: string): ManagementLogEntry[];
  /** 清空全部审计行，返回清掉的行数。仅供「恢复出厂」整库擦除用（批次3 用户拍板：出厂=无历史，
   *  连 management_log 一起清）——逐条管理操作永远只 append、不清。 */
  clear(): number;
}

export class SqliteManagementLog implements ManagementLog {
  private readonly db: DatabaseSync;

  /** @param db openStores 打开的共享连接（本表与三个 store 同连接，才能同事务提交）。 */
  constructor(db: DatabaseSync) {
    this.db = db;
    this.db.exec(SCHEMA);
  }

  append(e: Omit<ManagementLogEntry, 'createdAt'>): ManagementLogEntry {
    const entry: ManagementLogEntry = { ...e, createdAt: new Date().toISOString() };
    this.db
      .prepare(
        'INSERT INTO management_log (op, target_kind, target_id, reason, detail, created_at) VALUES (?,?,?,?,?,?)',
      )
      .run(
        entry.op,
        entry.targetKind,
        entry.targetId,
        entry.reason,
        entry.detail == null ? null : JSON.stringify(entry.detail),
        entry.createdAt,
      );
    return entry;
  }

  clear(): number {
    const r = this.db.prepare('DELETE FROM management_log').run();
    return Number(r.changes);
  }

  list(targetId?: string): ManagementLogEntry[] {
    const rows = (
      targetId
        ? this.db.prepare('SELECT * FROM management_log WHERE target_id = ? ORDER BY rowid ASC').all(targetId)
        : this.db.prepare('SELECT * FROM management_log ORDER BY rowid ASC').all()
    ) as unknown as Array<{
      op: string;
      target_kind: string;
      target_id: string;
      reason: string;
      detail: string | null;
      created_at: string;
    }>;
    return rows.map((r) => ({
      op: r.op,
      targetKind: r.target_kind,
      targetId: r.target_id,
      reason: r.reason,
      detail: r.detail == null ? null : (JSON.parse(r.detail) as Record<string, unknown>),
      createdAt: r.created_at,
    }));
  }
}
