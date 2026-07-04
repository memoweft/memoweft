/**
 * node:sqlite 驱动加载（T6 步1·唯一收敛处）。
 *
 * 全库【只此一处】接触 `node:sqlite`。收敛的意义：
 *   - 其余 7 个消费文件改成从 `./driver.ts` 引接口类型 + 从这里引 `DatabaseSync` 构造器，
 *     再不直接 import 'node:sqlite'——于是"底层用哪个 SQLite"只在这一个文件里定。
 *   - 加载走 `createRequire(import.meta.url)` 的【同步 require】：node:sqlite 是内置模块，
 *     能同步 require、失败能 catch——保持全链同步（不引入 await，不破坏 openStores/createMemoWeftCore 的同步 API），
 *     同时把"当前 Node 没有 node:sqlite"变成一句人话错误，而不是链接阶段的裸崩。
 *
 * ⚠ 顶层【急切】执行：解析驱动的 try/catch 写在模块顶层（不是等 openStores 调用时才做）。
 *   这样 `import 'memoweft'`（其入口链会 new VectorRetriever / 走 openStores，间接 import 本模块）
 *   本身就触发人话错误——这是步2 验收"Node 20 未装 better-sqlite3 时 import 就报人话错误"所依赖的设计。
 *
 * 步2 预留：这里将改为"node:sqlite 不可用 → 试 better-sqlite3 → 都不可用才抛"的选择链，
 *   错误文案里的第二条出路（装 better-sqlite3）也在步2 补全。本步只做 node:sqlite 一条路。
 */
import { createRequire } from 'node:module';
import type { DatabaseSyncConstructor, DatabaseSync as DbConn } from './driver.ts';

// 把连接【类型】也从这里透出（与下面的构造器【值】同名）：消费文件一句
//   `import { DatabaseSync } from '.../nodeSqliteDriver.ts'`
// 即同时拿到"开连接的构造器"（值）和"连接实例类型"（类型），类型标注 `db: DatabaseSync` 一字不改。
export type DatabaseSync = DbConn;

/** node:sqlite 导出的形状（只取用到的构造器）。 */
interface NodeSqliteModule {
  DatabaseSync: DatabaseSyncConstructor;
}

const require = createRequire(import.meta.url);

/**
 * 同步加载 node:sqlite。失败（当前 Node 无此内置模块 / 版本太低未转正）→ 抛人话错误：
 * 报当前 Node 版本 + 出路（升 Node ≥24；装 better-sqlite3 那条出路步2 落地时再补）。
 */
function loadNodeSqlite(): NodeSqliteModule {
  try {
    // 内置模块可同步 require；失败会同步 throw，能被 catch。
    return require('node:sqlite') as NodeSqliteModule;
  } catch (cause) {
    const ver = process.versions.node;
    throw new Error(
      `MemoWeft 需要 Node 内置模块 node:sqlite，但当前 Node ${ver} 加载不到它` +
        `（node:sqlite 到 Node 24 才转正）。出路：把 Node 升到 ≥24。`,
      { cause },
    );
  }
}

/** 当前选中的 SQLite 驱动的连接构造器。消费文件 `new DatabaseSync(path)` 用的就是它。 */
export const DatabaseSync: DatabaseSyncConstructor = loadNodeSqlite().DatabaseSync;
