/**
 * SQLite 驱动选择：优先 node:sqlite，并为旧版 Node 提供 better-sqlite3 回退。
 *
 * 全库【只此一处】接触底层 SQLite 实现（node:sqlite 与 better-sqlite3）。收敛的意义：
 *   - 其余消费文件改成从 `./driver.ts` 引接口类型 + 从这里引 `DatabaseSync` 构造器，
 *     再不直接 import 底层——于是"底层用哪个 SQLite"只在这一个文件里定。
 *   - 加载走 `createRequire(import.meta.url)` 的【同步 require】：node:sqlite 是内置模块、
 *     better-sqlite3 是 CJS 原生模块，两者都能同步 require、失败能 catch——保持全链同步
 *     （不引入 await，不破坏 openStores/createMemoWeftCore 的同步 API），
 *     同时在两个驱动都不可用时提供可执行的配置错误，而不是暴露底层模块加载异常。
 *
 * ⚠ 顶层【急切】执行：解析驱动的选择链写在模块顶层（不是等 openStores 调用时才做）。
 *   这样 `import 'memoweft'`（其入口链会 new VectorRetriever / 走 openStores，间接 import 本模块）
 *   本身就会报告驱动缺失，使不兼容环境在导入阶段得到明确的修复建议。
 *
 * 选择顺序（零依赖优先）：
 *   1. node:sqlite 可用 → 用它（Node ≥24 默认，零 runtime 依赖）。
 *   2. 否则试 better-sqlite3（Node 20/22 需 `npm i better-sqlite3`；可选 peer 依赖）。
 *   3. 都不可用 → 抛出可执行的配置错误（升级 Node ≥24 或安装 better-sqlite3）。
 *
 * 测试钩子：环境变量 `MEMOWEFT_TEST_DRIVER=better-sqlite3` 可强制走 better-sqlite3 分支
 *   （多版本测试矩阵的 Node 22 job 用它验第二驱动）。仅测试用，生产别设。
 */
import { createRequire } from 'node:module';
import { resolveLang } from '../config.ts';
import type { DatabaseSyncConstructor, DatabaseSync as DbConn } from './driver.ts';
import { makeBetterSqlite3Constructor } from './betterSqlite3Driver.ts';

// 把连接【类型】也从这里透出（与下面的构造器【值】同名）：消费文件一句
//   `import { DatabaseSync } from '.../nodeSqliteDriver.ts'`
// 即同时拿到"开连接的构造器"（值）和"连接实例类型"（类型），类型标注 `db: DatabaseSync` 一字不改。
export type DatabaseSync = DbConn;

/** node:sqlite 导出的形状（只取用到的构造器）。 */
interface NodeSqliteModule {
  DatabaseSync: DatabaseSyncConstructor;
}

const require = createRequire(import.meta.url);

/** 当前选中的驱动名——供测试/内省断言"实际走了哪条路"（Node 24 job 验零依赖路径用）。 */
export type ActiveDriver = 'node:sqlite' | 'better-sqlite3';

/**
 * 同步尝试加载 node:sqlite。成功返回构造器；失败（当前 Node 无此内置模块 / 版本太低未转正）返回 null，
 * 交给选择链去试 better-sqlite3。
 */
function tryLoadNodeSqlite(): DatabaseSyncConstructor | null {
  try {
    // 内置模块可同步 require；失败会同步 throw，能被 catch。
    return (require('node:sqlite') as NodeSqliteModule).DatabaseSync;
  } catch {
    return null;
  }
}

/**
 * 同步尝试加载 better-sqlite3（可选依赖，默认没装）。成功返回适配后的构造器；失败返回 null。
 * 真正的 require 在 betterSqlite3Driver.ts 里做（本模块只在选择链需要时才调它）。
 */
function tryLoadBetterSqlite3(): DatabaseSyncConstructor | null {
  try {
    return makeBetterSqlite3Constructor();
  } catch {
    return null;
  }
}

interface DriverPick {
  ctor: DatabaseSyncConstructor;
  name: ActiveDriver;
}

/**
 * 选择链（顶层执行）：node:sqlite 优先（零依赖），不可用再试 better-sqlite3；均不可用时报告配置错误。
 * `MEMOWEFT_TEST_DRIVER=better-sqlite3` 时跳过 node:sqlite、直接要 better-sqlite3（测试矩阵用）。
 */
function pickDriver(): DriverPick {
  const forced = process.env.MEMOWEFT_TEST_DRIVER;
  const lang = resolveLang();

  if (forced === 'better-sqlite3') {
    const better = tryLoadBetterSqlite3();
    if (better) return { ctor: better, name: 'better-sqlite3' };
    throw new Error(
      lang === 'zh'
        ? `MEMOWEFT_TEST_DRIVER=better-sqlite3 强制走 better-sqlite3，但它加载不到` +
            `（没装或原生模块未编译）。请先 \`npm i better-sqlite3\`。`
        : `MEMOWEFT_TEST_DRIVER=better-sqlite3 forces the better-sqlite3 driver, but it cannot be loaded` +
            ` (not installed, or the native module is not built). Run \`npm i better-sqlite3\` first.`,
    );
  }

  const nodeSqlite = tryLoadNodeSqlite();
  if (nodeSqlite) return { ctor: nodeSqlite, name: 'node:sqlite' };

  const better = tryLoadBetterSqlite3();
  if (better) return { ctor: better, name: 'better-sqlite3' };

  const ver = process.versions.node;
  throw new Error(
    lang === 'zh'
      ? `MemoWeft 需要一个 SQLite 驱动，但当前 Node ${ver} 两个都拿不到：` +
          `内置 node:sqlite 加载不到（它到 Node 24 才转正），可选的 better-sqlite3 也没装。\n` +
          `两条出路（任选其一）：\n` +
          `  1. 把 Node 升到 ≥24——node:sqlite 转正，零额外依赖开箱即用；\n` +
          `  2. 保持当前 Node，装可选驱动：\`npm i better-sqlite3\`（原生模块，一般走 prebuilt 二进制）。`
      : `MemoWeft needs a SQLite driver, but neither is available on the current Node ${ver}: ` +
          `the built-in node:sqlite cannot be loaded (it only becomes stable in Node 24), and the optional better-sqlite3 is not installed.\n` +
          `Two ways out (pick either):\n` +
          `  1. Upgrade Node to >=24 — node:sqlite becomes stable and works out of the box with zero extra dependencies;\n` +
          `  2. Keep the current Node and install the optional driver: \`npm i better-sqlite3\` (a native module, usually via a prebuilt binary).`,
  );
}

const picked = pickDriver();

/** 当前选中的 SQLite 驱动的连接构造器。消费文件 `new DatabaseSync(path)` 用的就是它。 */
export const DatabaseSync: DatabaseSyncConstructor = picked.ctor;

/** 当前实际选中的驱动名（测试/内省用；生产代码不该依赖它分支）。 */
export const activeDriver: ActiveDriver = picked.name;
