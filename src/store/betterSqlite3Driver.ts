/**
 * better-sqlite3 可选驱动，用于 Node 20/22。
 *
 * 为什么要它：`node:sqlite` 到 Node 24 才转正，把仍大量在产的 Node 20/22 挡在门外。
 *   本文件给 driver.ts 的接口补第二实现——底层换成 better-sqlite3（社区最成熟的同步 SQLite 绑定），
 *   于是 Node 20/22 上装了 better-sqlite3 就能跑，而 Node 24 默认仍走零依赖的 node:sqlite。
 *
 * 硬约束（与 nodeSqliteDriver.ts 完全对齐）：
 *   - **同步加载**：用 `createRequire(import.meta.url)` 同步 require——better-sqlite3 是 CJS 原生模块，
 *     能同步 require、失败能 catch，保持整条开库链同步（不引入 await、不破坏 openStores/createMemoWeftCore）。
 *   - **别静态 import**：绝不 `import Database from 'better-sqlite3'`——它是可选依赖，默认没装；
 *     静态 import 会让"没装它的 Node 24 用户"在链接阶段就炸。只在真要用它时才同步 require。
 *   - **类型走宽松声明**：不依赖 `@types/better-sqlite3`（那是 dev 依赖、宿主装 memoweft 时没有），
 *     用本文件内的最小 `interface` 描述 better-sqlite3 的用面，typecheck 不因缺类型而红。
 *
 * ⚠ 这里不做急切加载：本文件被 require 进来时不立刻连 better-sqlite3。
 *   驱动的"急切解析 + 选择链"在 nodeSqliteDriver.ts 顶层做（node:sqlite 优先 → 这里兜底）。
 */
import { createRequire } from 'node:module';
import type {
  DatabaseSyncConstructor,
  DatabaseSync as DbConn,
  SqlStatement,
  SqlRow,
} from './driver.ts';

// ── better-sqlite3 的最小用面声明（宽松，不依赖 @types/better-sqlite3） ────────────
//   只描述本适配层实际调到的方法/字段，别的一律不声明。

/** better-sqlite3 的预备语句（用面子集）。它的 get/all/run 都吃"位置参数或单个命名对象"。 */
interface BetterStatement {
  get(...params: unknown[]): unknown;
  all(...params: unknown[]): unknown[];
  run(...params: unknown[]): { changes: number | bigint; lastInsertRowid: number | bigint };
}

/** better-sqlite3 的连接（用面子集）。 */
interface BetterDatabase {
  exec(sql: string): unknown;
  prepare(sql: string): BetterStatement;
  close(): unknown;
}

/** `new Database(path, options)` 的构造器形状。 */
interface BetterDatabaseConstructor {
  new (path: string, options?: Record<string, unknown>): BetterDatabase;
}

/** better-sqlite3 的模块导出（`export = Database`，CJS 默认导出即构造器）。 */
type BetterSqlite3Module = BetterDatabaseConstructor;

const require = createRequire(import.meta.url);

/**
 * 同步加载 better-sqlite3 的构造器。失败（没装 / 原生模块没编译出来）→ 抛错，
 * 交给 nodeSqliteDriver.ts 的选择链捕获并汇总成驱动配置错误（那里能判断 node:sqlite 是否也不可用）。
 */
export function loadBetterSqlite3(): BetterDatabaseConstructor {
  // CJS 的 better-sqlite3 `module.exports = Database`，require 回来的就是构造器本身。
  const mod = require('better-sqlite3') as BetterSqlite3Module;
  return mod;
}

/**
 * 绑定参数归一：把消费文件传进来的参数整理成 better-sqlite3 认得的形状。
 *
 * 仓库现有两种绑定风格（步1 已统一命名键为【裸键】，与占位符 `$name` 相配）：
 *   - 位置参数：`stmt.run(a, b, c)` —— 原样透传。
 *   - 单个命名对象：`stmt.run({ id, subject_id, ... })` —— 裸键。
 *
 * better-sqlite3 对 `$name` / `:name` / `@name` 占位符都用【裸键对象】绑定（与 node:sqlite 一致），
 *   所以裸键对象可原样透传。这里额外做一层【剥前缀兜底】：万一有对象键带了 `$`/`@`/`:` 前缀
 *   （历史遗留或将来误写），剥掉前缀再传，避免 better-sqlite3 因"键名带符号"报错——纯防御，
 *   对当前全裸键的调用是 no-op。
 */
function normalizeParams(params: unknown[]): unknown[] {
  // 命名对象绑定：唯一参数且是纯对象（非 null、非数组、非 Uint8Array/Buffer 等标量载体）。
  if (params.length === 1 && isPlainBindObject(params[0])) {
    return [stripKeyPrefixes(params[0] as Record<string, unknown>)];
  }
  // 位置参数（含 0 参）：原样透传。
  return params;
}

/** 判定"这是一个命名绑定对象"——排除 null / 数组 / 二进制载体（那些是位置标量值）。 */
function isPlainBindObject(v: unknown): boolean {
  if (v === null || typeof v !== 'object') return false;
  if (Array.isArray(v)) return false;
  if (v instanceof Uint8Array) return false;
  // Buffer 是 Uint8Array 子类，已被上面挡住；其余普通对象视为命名绑定。
  return true;
}

/** 剥掉对象键的 `$`/`@`/`:` 前缀（兜底；当前全库已是裸键，此函数对现状是恒等映射）。 */
function stripKeyPrefixes(obj: Record<string, unknown>): Record<string, unknown> {
  let changed = false;
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(obj)) {
    const bare = k.length > 0 && (k[0] === '$' || k[0] === '@' || k[0] === ':') ? k.slice(1) : k;
    if (bare !== k) changed = true;
    out[bare] = obj[k];
  }
  return changed ? out : obj;
}

/** 把 better-sqlite3 的一行结果收成 SqlRow（列名→值）。undefined 透传给 get 的"无结果"。 */
function asRow(r: unknown): SqlRow | undefined {
  return (r ?? undefined) as SqlRow | undefined;
}

/** better-sqlite3 语句 → driver.ts 的 SqlStatement 适配。 */
function wrapStatement(stmt: BetterStatement): SqlStatement {
  return {
    get(...params: unknown[]): SqlRow | undefined {
      return asRow(stmt.get(...normalizeParams(params)));
    },
    all(...params: unknown[]): SqlRow[] {
      return stmt.all(...normalizeParams(params)) as SqlRow[];
    },
    run(...params: unknown[]): { changes: number | bigint } {
      const info = stmt.run(...normalizeParams(params));
      // 只暴露 changes（全库 Number(r.changes) 收口；lastInsertRowid 未用，不外露）。
      return { changes: info.changes };
    },
    // 重载签名（位置 / 单对象）在实现层统一收成 rest 参数——两种调用都命中上面这一份实现。
  } as unknown as SqlStatement;
}

/** better-sqlite3 连接 → driver.ts 的 DatabaseSync 适配。 */
function wrapDatabase(db: BetterDatabase): DbConn {
  return {
    exec(sql: string): void {
      db.exec(sql);
    },
    prepare(sql: string): SqlStatement {
      return wrapStatement(db.prepare(sql));
    },
    close(): void {
      db.close();
    },
  };
}

/**
 * 导出与 node:sqlite 同形状的连接构造器：`new DatabaseSync(path)` 开一条 better-sqlite3 连接。
 * 供 nodeSqliteDriver.ts 的选择链在"node:sqlite 不可用"时选用。
 *
 * ⚠ 这是个工厂函数（返回构造器），不是模块顶层急切执行——只有选择链真选中 better-sqlite3 时才调，
 *   避免"没装 better-sqlite3 的 Node 24 用户"因本模块被 import 就触发 require 失败。
 */
export function makeBetterSqlite3Constructor(): DatabaseSyncConstructor {
  const Database = loadBetterSqlite3();
  // 返回一个满足 DatabaseSyncConstructor 的类：new 时开 better-sqlite3 连接、包成 driver 接口。
  class BetterSqlite3Connection {
    private readonly conn: DbConn;
    constructor(path: string) {
      // ':memory:' 与文件路径 better-sqlite3 都原生支持，语义与 node:sqlite 一致。
      this.conn = wrapDatabase(new Database(path));
    }
    exec(sql: string): void {
      this.conn.exec(sql);
    }
    prepare(sql: string): SqlStatement {
      return this.conn.prepare(sql);
    }
    close(): void {
      this.conn.close();
    }
  }
  return BetterSqlite3Connection as unknown as DatabaseSyncConstructor;
}
