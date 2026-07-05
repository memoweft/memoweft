/**
 * Schema 版本化 + 迁移器（0.2.0 · 长期硬债收口）。
 *
 * 为什么要它：npm 上已发布 0.1.0，可能有人在攒真实数据。往后任何改表结构都必须【可版本化、可迁移、
 *   迁移前备份、可回滚、能拿旧库无损打开】——不能再靠各 store 里"缺列就补"的土办法蒙混。
 *
 * 机制（全库一个版本号，存在 SQLite 文件头的 `PRAGMA user_version`）：
 *   - **新库**（openStores 开库前文件不存在 / `:memory:`）：store 构造已建【最新】schema → 直接把
 *     user_version 盖到最新版，不跑任何迁移。
 *   - **老库**（已存在、user_version < 最新）：从 user_version 起，按序应用 pending 迁移。
 *     baseline（v1）对 0.1.0 老库是 no-op（表和列都在，本 stamp 只标"这库是 0.1.0 形状"），
 *     真正改结构的从 v2 起。
 *   - 每条迁移在【自己的事务】里跑，版本号也在同一事务内设——中途抛错整段 ROLLBACK（user_version
 *     可回滚，已实测），不会留半迁移的库。
 *   - 有真改动（v2+）的迁移执行前，若是磁盘真实库文件，先 copy 一份 `.bak`。
 *
 * 加新迁移的约定（重要）：
 *   1. 在下面 MIGRATIONS 追加一条 `{ version: N, name, up }`，`up` 里写这一版的 ALTER/DDL。
 *   2. **同时**把对应 store 的 `SCHEMA` 常量也改成带上新列——新库靠 store 直接建最新 schema，
 *      老库靠这条迁移升上来。两处都改，否则新库会缺列。
 */
import type { DatabaseSync } from './driver.ts';
import { copyFileSync, existsSync } from 'node:fs';
import { resolveLang } from '../config.ts';

export interface Migration {
  /** 这条迁移把库带到的目标版本号（从 1 递增，连续）。 */
  version: number;
  /** 人读名字（写进日志）。 */
  name: string;
  /** 结构改动；在事务里跑，抛错则整段回滚。baseline(v1) 为空——schema 由 store 构造建。 */
  up: (db: DatabaseSync) => void;
}

/**
 * 有序迁移列表。v1 = baseline（0.1.0 形状）：表由各 store 构造 `CREATE TABLE IF NOT EXISTS` 建，
 * 本条只做"标记这库到了 0.1.0 版"的 stamp，不含 DDL。往后真改结构从 v2 追加。
 */
export const MIGRATIONS: Migration[] = [
  { version: 1, name: 'baseline-0.1.0', up: () => { /* schema 由 store 构造建，此处只 stamp 版本号 */ } },
];

/** 当前代码支持的最新 schema 版本。 */
export const LATEST_SCHEMA_VERSION = MIGRATIONS.reduce((m, x) => Math.max(m, x.version), 0);

/** 读全库 schema 版本号（PRAGMA user_version，存在文件头；未设过=0）。 */
export function getSchemaVersion(db: DatabaseSync): number {
  const row = db.prepare('PRAGMA user_version').get() as { user_version?: number } | undefined;
  return Number(row?.user_version ?? 0);
}

function setSchemaVersion(db: DatabaseSync, v: number): void {
  // PRAGMA 不能参数化；v 校验成非负整数后再拼进去，杜绝注入。
  if (!Number.isInteger(v) || v < 0) {
    throw new Error(resolveLang() === 'zh' ? `非法 schema 版本号：${v}` : `Invalid schema version number: ${v}`);
  }
  db.exec(`PRAGMA user_version = ${v}`);
}

export interface RunMigrationsOptions {
  /** 库文件路径；用于迁移前备份。省略或 `:memory:` = 不备份。 */
  dbPath?: string;
  /** true = 新建库（store 已建最新 schema）→ 直接盖最新版、不跑迁移。openStores 按开库前文件是否存在判定。 */
  fresh?: boolean;
  /** 覆盖迁移列表（测试用；缺省 MIGRATIONS）。 */
  migrations?: Migration[];
  /** 只报计划不改库。 */
  dryRun?: boolean;
  /** 强制开/关备份；缺省 = 自动（有 v2+ 真改动才备份）。 */
  backup?: boolean;
  /** 日志回调（缺省静默）。 */
  log?: (msg: string) => void;
}

export interface MigrationResult {
  /** 迁移前版本号。 */
  from: number;
  /** 迁移后版本号（dryRun 时 = from，不实改）。 */
  to: number;
  /** 实际应用（或 dryRun 下计划应用）的版本号列表。 */
  applied: number[];
  /** 若做了备份，备份文件路径。 */
  backupPath?: string;
  dryRun: boolean;
}

/**
 * 把库升到最新 schema 版本。幂等：已是最新则啥都不做（可重复调）。
 * openStores 在开库后调它；也可单独调（如 dry-run 预检、CLI 迁移工具）。
 */
export function runMigrations(db: DatabaseSync, opts: RunMigrationsOptions = {}): MigrationResult {
  const migrations = (opts.migrations ?? MIGRATIONS).slice().sort((a, b) => a.version - b.version);
  const latest = migrations.reduce((m, x) => Math.max(m, x.version), 0);
  const log = opts.log ?? (() => {});
  const from = getSchemaVersion(db);

  // 降级防护（A）：库版本高于本代码支持的最新版 = 这库由【更新版本】的 memoweft 创建。
  //   绝不能让旧代码静默读写它不认识的 schema（会写坏数据）——直接拒，让用户升级。
  //   真·新库文件 from=0，触发不了；只挡"未来版本建的库被旧代码打开"。
  if (from > latest) {
    throw new Error(
      resolveLang() === 'zh'
        ? `数据库 schema 版本 v${from} 高于本版 memoweft 支持的 v${latest}：` +
            `这个库由更新版本的 memoweft 创建，请先升级 memoweft 再打开（拒绝用旧代码读写不认识的 schema，防写坏数据）。`
        : `Database schema version v${from} is higher than the v${latest} supported by this memoweft: ` +
            `this database was created by a newer version of memoweft. Please upgrade memoweft before opening it (old code refuses to read/write a schema it doesn't recognize, to avoid corrupting data).`,
    );
  }

  // 新库：store 已建最新 schema，直接盖最新版本号，不跑迁移。
  if (opts.fresh) {
    if (opts.dryRun) return { from, to: from, applied: [], dryRun: true };
    if (from < latest) setSchemaVersion(db, latest);
    return { from, to: latest, applied: [], dryRun: false };
  }

  const pending = migrations.filter((m) => m.version > from);
  if (pending.length === 0) return { from, to: from, applied: [], dryRun: !!opts.dryRun };

  // 有真改动（v2+）的迁移才备份；baseline(v1) 是 no-op stamp，不动数据、不必备份。
  const doesRealWork = pending.some((m) => m.version >= 2);
  const wantBackup = opts.backup ?? doesRealWork;

  if (opts.dryRun) {
    log(`[migrate] dry-run：${from} → ${latest}，将应用 [${pending.map((m) => m.version).join(', ')}]`);
    return { from, to: from, applied: pending.map((m) => m.version), dryRun: true };
  }

  let backupPath: string | undefined;
  if (wantBackup && opts.dbPath && opts.dbPath !== ':memory:' && existsSync(opts.dbPath)) {
    // 注：copyFileSync 只拷主库文件。当前默认 rollback journal + 单进程下一致、够用。
    //   ⚠ 若将来开 WAL，只拷主文件不拷 -wal 会得到不一致备份——那时改用 `VACUUM INTO '<bak>'` 更稳。
    backupPath = `${opts.dbPath}.bak-v${from}-${Date.now()}`;
    copyFileSync(opts.dbPath, backupPath);
    log(`[migrate] 迁移前备份 → ${backupPath}`);
  }

  const applied: number[] = [];
  for (const m of pending) {
    db.exec('BEGIN');
    try {
      m.up(db);
      setSchemaVersion(db, m.version); // 版本号在同一事务内设：抛错整段回滚，不留半迁移
      db.exec('COMMIT');
    } catch (e) {
      db.exec('ROLLBACK');
      throw new Error(
        resolveLang() === 'zh'
          ? `迁移 v${m.version}（${m.name}）失败，已回滚：${e instanceof Error ? e.message : String(e)}`
          : `Migration v${m.version} (${m.name}) failed and was rolled back: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
    applied.push(m.version);
    log(`[migrate] 已应用 v${m.version} ${m.name}`);
  }
  return { from, to: latest, applied, backupPath, dryRun: false };
}
