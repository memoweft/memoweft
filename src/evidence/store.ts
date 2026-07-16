/**
 * 证据存储层（地图 cell 6 / 7：证据是 MemoWeft 自有的唯一真相）。
 * 参考 reference/migrated-baseline/event/store.ts 的 node:sqlite 模式。
 *
 * 职责：只做存储（建表 + 写入 + 读取 + 时间窗查询）。不做判断（召回 / 画像 / 冲突都不在此层）。
 * 授权位、双时态时间在写入时按规则补默认（见 put）。
 * 幂等：带 originId 的重复写入只存一次（防重试重复落库）。
 */
import { DatabaseSync } from '../store/nodeSqliteDriver.ts';
import type { SQLInputValue } from '../store/driver.ts';
import { randomUUID } from 'node:crypto';
import { config, cloudReadDefault, type MemoWeftConfig } from '../config.ts';
import { systemClock, type Clock } from '../clock.ts';
import { BUSY_TIMEOUT_MS } from '../store/busyTimeout.ts';
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
  corrects_evidence_id TEXT,
  preceding_ai_context TEXT
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
  // 注:`preceding_ai_context` 列【故意不列在 EvidenceRow 里】(D-0033 Phase 1b 结构墙)——
  //   fromRow 的入参类型因此永不含它、结构上无法把 AI 上文映射进 Evidence 读结构。
  //   写入端(put/insert)把它作为额外参数键塞进 INSERT;读取端(注入用)走专用 precedingAiContextOf。
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
  /** 取某条证据的【上一轮 AI 那句】只读上下文（D-0033 Phase 1b）——**唯一读取该列的路径**,专供
   *  distill/consolidate 注入(且只对已过隐私门的证据行调)。不进 Evidence 读结构=永不外泄为证据。
   *  无 AI 上文 / 证据不存在 → null。**永不返回证据 id、永不进 consolidate 的 support 白名单(3a/3d)**。 */
  precedingAiContextOf(evidenceId: string): string | null;
  /** 按【原 id 与时间戳】原样插入（导入/恢复用；不生成新 id）。调用方须先判重：id 或 originId 已存在会抛约束错。 */
  insert(evidence: Evidence): void;
  close(): void;
}

export class SqliteEvidenceStore implements EvidenceStore {
  private readonly db: DatabaseSync;
  private readonly ownsDb: boolean;
  /** 本 store 的配置（put 补默认授权位用）；可注入（P2-5），缺省=全局单例。 */
  private readonly cfg: MemoWeftConfig;
  /** 落库时间源（recordedAt）；可注入以求确定性/时间旅行，缺省=真实系统时间。 */
  private readonly clock: Clock;

  /**
   * @param db SQLite 文件路径（自开连接，默认 './dla.db'；测试传 ':memory:'），
   *   或一个【已打开的共享 DatabaseSync】——多个 store 共用一条连接时才能跨表事务（见 store/openStores.ts）。
   * @param cfg 可注入配置（P2-5 config 去单例）：不传 = 用全局单例；put 补授权默认（evidenceDefaults / privacyMode）按这份。
   */
  constructor(db: string | DatabaseSync = './dla.db', cfg: MemoWeftConfig = config, clock: Clock = systemClock) {
    this.ownsDb = typeof db === 'string';
    this.db = typeof db === 'string' ? new DatabaseSync(db) : db;
    // 自开连接才设并发保底；共享连接由 openStores 已设过，别重复设。
    if (this.ownsDb) this.db.exec(`PRAGMA busy_timeout = ${BUSY_TIMEOUT_MS}`);
    this.cfg = cfg;
    this.clock = clock;
    this.db.exec(SCHEMA);
    this.migrate();
  }

  /** 幂等迁移:旧库补上后加的 nullable 列（D-0033 Phase 1b: preceding_ai_context）。新库由 SCHEMA 直接带上。
   *  为何走这里而非 migrations.ts formal v2:它是 nullable 状态列、无数据变换,缺列补对【任何构造路径】
   *  (含直接构造老库、不经 openStores/runMigrations)都稳;formal 迁移路径留给需版本化/备份/数据变换的迁移
   *  (同 D-0023 muted_at 先例;LATEST_SCHEMA_VERSION 仍 v1,migrations.test 的 schema 签名收敛测试会兜住漏补)。 */
  private migrate(): void {
    const cols = this.db
      .prepare("SELECT name FROM pragma_table_info('evidence')")
      .all() as unknown as Array<{ name: string }>;
    if (!cols.some((c) => c.name === 'preceding_ai_context')) {
      this.db.exec('ALTER TABLE evidence ADD COLUMN preceding_ai_context TEXT');
    }
  }

  put(input: EvidenceInput): Evidence {
    // 幂等：带 originId 且已存在 → 返回原条，不重复落库。
    if (input.originId) {
      const existing = this.findByOrigin(input.originId);
      if (existing) return existing;
    }

    const recordedAt = this.clock().toISOString();
    // 授权缺省按 sourceKind 分流（红线 B 下沉 put，最后防线）：
    //   'observed'/'tool' → 三个默认取各自保守分支（observedDefaults / toolDefaults，均 local✓/cloud✗/infer✓），
    //     任何入口落 observed/tool 都一次性兜住不上云（工具返回值常含敏感外部数据，与 observed 同级保守，AD-3/D-0013）；
    //   其余（spoken/inferred）→ 维持原通用默认（evidenceDefaults + cloudReadDefault 跟随 privacyMode）。
    // 显式传值永远优先（下面 ?? 左侧）。
    const conservative =
      input.sourceKind === 'observed' ? this.cfg.observedDefaults
      : input.sourceKind === 'tool' ? this.cfg.toolDefaults
      : null;
    const localDefault = conservative ? conservative.allowLocalRead : this.cfg.evidenceDefaults.allowLocalRead;
    const cloudDefault = conservative ? conservative.allowCloudRead : cloudReadDefault(this.cfg);
    const inferDefault = conservative ? conservative.allowInference : this.cfg.evidenceDefaults.allowInference;
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
      allowLocalRead: input.allowLocalRead ?? localDefault,
      allowCloudRead: input.allowCloudRead ?? cloudDefault,
      allowInference: input.allowInference ?? inferDefault,
      correctsEvidenceId: input.correctsEvidenceId ?? null,
    };

    // preceding_ai_context（D-0033 Phase 1b）:AI 上文只写不读、不在 Evidence 读结构里,故作为额外参数键
    //   塞进 INSERT(不经 toRow/Evidence)。缺省 null;仅 Conversation 路会传值。
    const row = { ...toRow(evidence), preceding_ai_context: input.precedingAiContext ?? null };
    this.db
      .prepare(
        `INSERT INTO evidence (
          id, subject_id, source_kind, host_id, origin_id,
          occurred_at, recorded_at, raw_content, summary,
          allow_local_read, allow_cloud_read, allow_inference, corrects_evidence_id,
          preceding_ai_context
        ) VALUES (
          $id, $subject_id, $source_kind, $host_id, $origin_id,
          $occurred_at, $recorded_at, $raw_content, $summary,
          $allow_local_read, $allow_cloud_read, $allow_inference, $corrects_evidence_id,
          $preceding_ai_context
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

  precedingAiContextOf(evidenceId: string): string | null {
    // 只 SELECT 这一列(不经 SELECT */fromRow → AI 上文永不进 Evidence 读结构)。
    const row = this.db
      .prepare('SELECT preceding_ai_context FROM evidence WHERE id = ?')
      .get(evidenceId) as unknown as { preceding_ai_context: string | null } | undefined;
    return row?.preceding_ai_context ?? null;
  }

  insert(evidence: Evidence): void {
    // 导入/恢复路径:Evidence 无 precedingAiContext 字段(导出已结构性剥离)→ 该列恒写 null
    //   (AI 上文永不跨导出/导入边界)。同 put,作为额外参数键塞进 INSERT。
    const row = { ...toRow(evidence), preceding_ai_context: null };
    this.db
      .prepare(
        `INSERT INTO evidence (
          id, subject_id, source_kind, host_id, origin_id,
          occurred_at, recorded_at, raw_content, summary,
          allow_local_read, allow_cloud_read, allow_inference, corrects_evidence_id,
          preceding_ai_context
        ) VALUES (
          $id, $subject_id, $source_kind, $host_id, $origin_id,
          $occurred_at, $recorded_at, $raw_content, $summary,
          $allow_local_read, $allow_cloud_read, $allow_inference, $corrects_evidence_id,
          $preceding_ai_context
        )`,
      )
      .run(row as unknown as Record<string, SQLInputValue>);
  }

  close(): void {
    if (this.ownsDb) this.db.close(); // 共享连接由 openStores 统一关；单个 store 不关，免得关掉别人还在用的连接
  }
}
