# T6 · 数据库驱动抽缝 + better-sqlite3 可选适配（Node 20/22 触达）

**对应五关**：触达关。**作者已拍板**（2026-07-04 决策 4：加 better-sqlite3 可选层）。本批最大任务，分两步，**步 1 单独合入后再做步 2**。步 1 完成后必须由架构会话（Fable）验收接缝设计再进步 2。

## 背景

Node ≥24 硬门槛（`node:sqlite` 到 24 才转正）把仍大量在产的 Node 20/22 环境挡在门外（注：20 已过 EOL 但存量不小，22 是在维 LTS），是商用漏斗顶端的第一道墙。作者拍板：加 better-sqlite3 **可选**适配层。硬约束：**core 的 `dependencies` 必须保持空**——better-sqlite3 只能进 `peerDependencies` + `peerDependenciesMeta.optional:true`，装不装用户自己定，"零运行时依赖"卖点不许做没。

## 关键设计约束（执行前必须理解）

1. **全链必须保持同步**：`createMemoWeftCore` / `openStores` 是同步 API（`src/store/openStores.ts:40`），动态 `import()` 是异步的，会把整个开库链变 async = 破坏公共 API。**解法**：驱动加载用 `createRequire(import.meta.url)` 同步 require——Node 内置模块和 CJS 的 better-sqlite3 都能同步 require，且失败可 catch。
2. **静态 import 收敛**：`node:sqlite` 目前在 **7 个文件**顶部静态 import：`src/evidence/store.ts:9`、`src/event/store.ts:5`、`src/cognition/store.ts:7`、`src/memory/managementLog.ts:8`、`src/store/openStores.ts:12`、`src/store/migrations.ts:22`、**`src/retrieval/vectorRetriever.ts:13`**（易漏！它在包入口链上：index.ts:91 导出 VectorRetriever、createCore.ts:172 会 new 它——漏了它，Node 20/22 上 `import 'memoweft'` 照样在链接阶段炸）。全部收敛到唯一驱动文件。另外 `src/memory/managementApi.ts`（254/255/310 行）虽不 import node:sqlite 但直接用 `bundle.db.prepare`——`StoreBundle.db` 的类型换成驱动接口时会连带覆盖，一起确认。
3. **驱动解析在驱动模块顶层急切执行**：加载驱动的 try/catch 链写在驱动文件顶层（不是 openStores 调用时才做），这样 `import 'memoweft'` 本身就能触发人话错误——验收第 3 条依赖这个设计。
4. **类型解耦**：上述文件的类型标注（`DatabaseSync`、`SQLInputValue`）改为引用驱动接口类型，不再直接 import `node:sqlite` 的类型。

## 步 1 · 抽缝（纯重构，行为零变化）

**驱动接口按此清单定**（校对员已代查全库用面，执行时 grep 复核一遍即可）：

- 连接：构造（路径或共享连接）+ `exec` + `prepare` + `close`，**别无其他方法**。
- 语句：`get` / `all` / `run`（位置参数和对象参数两种绑定都有）；`run` 返回值只用 `changes`（6 处 `Number(r.changes)`：evidence:212、event:155/165、cognition:290/300、managementLog:73），`lastInsertRowid` 未用。
- `SQLInputValue` 类型实际用在 evidence(164/236) 和 cognition(181/280)；`event/store.ts:5` import 了但没用到（顺手清掉）。
- migrations 读 `user_version` 用 `prepare('PRAGMA user_version').get()`（47 行）、写用 `exec` 拼串（54 行）。
- `toRow` 已把 boolean 转 0/1（evidence:62-64,206；event:144），全库绑定值无裸 boolean——这条风险已排除，不用再查。

活儿：新建 `src/store/driver.ts`（接口）+ `src/store/nodeSqliteDriver.ts`（createRequire 加载 node:sqlite，失败抛人话错误：当前 Node 版本 x.y + 两条出路〔升 Node ≥24 / 装 better-sqlite3——后半句步 2 落地时再补〕）。7 个消费文件改用驱动接口。

**绑定风格顺手统一**（为步 2 铺路，行为零变化）：仓库两种绑定并存——evidence 用裸键（`.run(row)`），cognition 用 `$` 前缀键（166-181、265-280 行）。better-sqlite3 只认裸键。步 1 把 cognition 统一成裸键（node:sqlite 两种都收，改了无感知），步 2 的驱动再做剥前缀兜底。

**验收步 1**：
- [ ] 全部现有测试**断言零改动**全绿；三绿。
- [ ] `grep -rn "from 'node:sqlite'" src/` 只剩驱动文件 1 处命中。
- [ ] 注意：`tests/migrations.test.ts:10`、`tests/memoryApi.test.ts:13`、`tests/retrieval.test.ts:10` 三个测试文件自己也静态 import node:sqlite（拿原生连接做 setup）——**允许**把它们的 setup 改走驱动或测试 helper（只动拿连接的方式，断言一行不动），否则步 2 的多版本矩阵永远跑不起来。

## 步 2 · better-sqlite3 实现 + 触达面打开

- 新建 `src/store/betterSqlite3Driver.ts`：同接口第二实现。已知差异（逐一核对别想当然）：绑定对象键的 `$/@/:` 前缀剥除（见步 1 统一后应已无前缀键）、`run` 返回结构、`PRAGMA user_version` 读写、busy_timeout 设法（T4 的 pragma 两个驱动都要生效）。
- 驱动选择顺序（驱动模块顶层急切执行）：`node:sqlite` 可用 → 用它（零依赖优先）；不可用 → 试 better-sqlite3；都不可用 → 人话错误补全两条出路。
- `package.json`：`peerDependencies` + `peerDependenciesMeta` 标 optional；`devDependencies` 加 better-sqlite3 与 @types/better-sqlite3（仅测试用）；`engines` 降为 `">=20"`；`_comment` 更新说明"零运行时依赖指 dependencies 为空，可选 peer 不算破戒"。
- **多版本测试矩阵**（校对实锤过两堵墙，跑法写死如下，别自行发挥）：
  - **Node 24 job（验证零依赖路径）**：better-sqlite3 进了 devDependencies 后 `npm ci` 必装上，所以"不装"要靠动作：`npm ci` 后 `rm -rf node_modules/better-sqlite3` 再跑全测试，或加断言确认实际驱动 = node:sqlite。
  - **Node 22 job**：用 **22.18+**（原生剥 .ts 类型 22.18 起才默认开，低于此版本 `node --test tests/**/*.ts` 起不来）+ 环境变量强制走 better-sqlite3 驱动（如 `MEMOWEFT_TEST_DRIVER=better-sqlite3`），全测试。
  - **Node 20 job**：Node 20 **没有**原生剥类型能力，.ts 测试套件物理上跑不了。跑法：`npm run build` 后用一个 **dist 冒烟脚本**（纯 JS：开库→存话→迁移→关库，走 better-sqlite3）验证；全套 .ts 测试不试图在 20 上跑。若想在 20 上跑全量，需要引 tsx 之类新 dev 依赖——CONTRIBUTING 规定新依赖默认拒绝，除非作者另行拍板。
- 文档更新清单：README（EN/中文）、INSTALL、deployment 的 Node 要求段；**CONTRIBUTING.md:19-20**（"Node ≥ 24"与"只装 typescript 和 @types/node 两个 devDependency"两句都会变错话）；**ci.yml 第 9、38 行注释**（"锁 24 别放宽"与新矩阵冲突）。统一口径："Node ≥24 开箱即用；Node 20/22 需 `npm i better-sqlite3`"。
- **验收步 2**：
  - [ ] 三个 CI job 全绿。
  - [ ] Node 20 不装 better-sqlite3 时 `import 'memoweft'`（用 dist 产物）报人话错误——手测记录进 PR。
  - [ ] `npm pkg get dependencies` 输出 `{}`（**别**用 `npm pack --dry-run` 验这条——它只列打包文件，不打印依赖）。
  - [ ] CHANGELOG 记（新增可选驱动 + engines 变化，这是 0.3.0 的头条之一）。

## 不许动 / 风险注记

- 迁移器语义（版本号、备份、事务回滚）在两个驱动下必须逐字一致——`tests/migrations.test.ts` 9 条是硬闸，包括 0.1.0 fixture 无损升级。
- better-sqlite3 是原生模块，一般走 prebuilt 二进制，装不上会回落 node-gyp 编译——INSTALL 里如实写一句，别承诺"一定装得上"。
- 若步 2 试做中发现两驱动语义差异大到要改 store 层逻辑（不只是薄适配），**停下来报告**，不许为兼容第二驱动改动核心行为。
