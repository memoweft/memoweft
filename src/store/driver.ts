/**
 * SQLite 驱动接口：统一 node:sqlite 与 better-sqlite3 的最小能力面。
 *
 * 为什么要它：整个开库链是【同步】的（openStores / 各 Sqlite*Store 构造 / createMemoWeftCore），
 *   而 `node:sqlite` 到 Node 24 才稳定——Node 20/22 环境需要 better-sqlite3 回退。
 *   把"用哪个底层 SQLite 实现"收敛成这一个接缝：消费文件只依赖这里的接口类型，
 *   具体加载在 nodeSqliteDriver.ts 用 `createRequire` 同步 require（内置模块与 CJS 的 better-sqlite3 都能同步 require，
 *   且加载失败可转换为明确的配置错误），保持全链同步且不破坏公共 API。
 *
 * 接口面刻意最小（只覆盖全库现有用面，别扩）：
 *   - 连接：构造（路径或 ':memory:'）+ exec + prepare + close。
 *   - 语句：get / all / run（位置参数与对象参数两种绑定都要，仓库两种都在用）；
 *     run 返回值只暴露 changes（全库只读它做 Number(r.changes)；lastInsertRowid 未用，不进接口）。
 *
 * ⚠ 这是【底座接缝】、不是产品接口——不进 config、不对外导出给宿主当 API。
 *   步2（better-sqlite3 可选适配）会加同接口的第二实现，此接口即两实现的公共契约。
 */

/**
 * 绑定值类型：语句参数能接受的标量。等价于 `node:sqlite` 的 `SQLInputValue`
 * （抽缝后消费文件引这里、不再直接 import node:sqlite 的类型）。
 */
export type SqlInputValue = null | number | bigint | string | Uint8Array;

/** 一行查询结果（列名 → 值）。get/all 的返回按各自 Row 结构再断言。 */
export type SqlRow = Record<string, unknown>;

/**
 * 预备语句：get / all / run。
 * 绑定支持两种风格（node:sqlite 两种都收）：
 *   - 位置参数：`stmt.run(a, b, c)`。
 *   - 对象参数（命名键）：`stmt.run({ ... })`——传【单个】对象即按命名键绑定。
 */
export interface SqlStatement {
  /** 取一行；无参或位置参数或单对象绑定。无结果返回 undefined。 */
  get(...params: SqlInputValue[]): SqlRow | undefined;
  get(params: Record<string, SqlInputValue>): SqlRow | undefined;
  /** 取全部行；无参或位置参数或单对象绑定。 */
  all(...params: SqlInputValue[]): SqlRow[];
  all(params: Record<string, SqlInputValue>): SqlRow[];
  /** 执行写；只暴露 changes（受影响行数，可能是 number 或 bigint，调用方 Number() 统一转换）。 */
  run(...params: SqlInputValue[]): { changes: number | bigint };
  run(params: Record<string, SqlInputValue>): { changes: number | bigint };
}

/**
 * 数据库连接：exec / prepare / close。
 * 命名保持 `DatabaseSync`——原 7 个消费文件的类型标注一字不改即可切到接缝（把 import 源从
 * node:sqlite 换成这里，标注仍写 DatabaseSync），改动面最小、可读性不打折。
 */
export interface DatabaseSync {
  /** 直接执行一段 SQL（DDL / PRAGMA / BEGIN-COMMIT 等，不取结果）。 */
  exec(sql: string): void;
  /** 预备一条语句。 */
  prepare(sql: string): SqlStatement;
  /** 关闭连接。 */
  close(): void;
}

/**
 * 打开一条连接的构造器签名（`new`）。各消费文件用 `new DatabaseSync(path)` 开自有连接时，
 * 拿到的是【当前选中驱动】的这个构造器（见 nodeSqliteDriver.ts 导出的 DatabaseSync）。
 */
export interface DatabaseSyncConstructor {
  new (path: string): DatabaseSync;
}

/**
 * 兼容别名：原代码里 `SQLInputValue` 大量出现（evidence / cognition 的对象绑定断言用）。
 * 抽缝只换 import 源、类型名保持不变，消费文件的 `Record<string, SQLInputValue>` 一字不改。
 */
export type SQLInputValue = SqlInputValue;
