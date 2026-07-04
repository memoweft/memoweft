# T4 · SQLite 并发保底：busy_timeout（WAL 本批明确不做）

**对应五关**：生产可靠性欠账（审计风险第 4 条）。小任务。

## 背景（审计结论）

SQLite 连接从不设 `busy_timeout`（等锁重试时长），也没开 WAL（`grep busy_timeout/WAL/journal_mode` 于 src/ 无命中）。两个进程同库并发写时，后写的一方会**立即**裸抛 `SQLITE_BUSY`，core 无任何重试。单进程内因 DatabaseSync 全同步 API 天然串行，问题只在多进程——真实场景如 **Host 与 testbench 同时指向同一个库文件**，或第三方进程直连 store（注：采集插件不算——它只 HTTP POST 给 Host，从不直接开库）。

## 改哪里

给库内**所有自开的连接**设 `PRAGMA busy_timeout = 5000`（毫秒；写锁被占时等待重试最长 5 秒再报错，而不是立刻报错）。自开连接经核实共 5 处：

1. `src/store/openStores.ts:44` `new DatabaseSync(dbPath)` 之后、建 store 之前，`db.exec('PRAGMA busy_timeout = 5000')`。
2. 三个 store 的"传字符串路径自开连接"分支（`ownsDb` 为 true 时）：`src/evidence/store.ts:120-124`、`src/event/store.ts`、`src/cognition/store.ts` 的同型构造分支。共享连接分支不重复设（openStores 已设过）。
3. `src/retrieval/vectorRetriever.ts:45` 的 `new DatabaseSync(dbPath)`——向量表缺省与主库**同一个文件**（`createCore.ts:167` 是 `vectorDbPath ?? dbPath`）且独立开第二条连接、有 INSERT/DELETE 写路径，漏了它多进程下向量写照样裸奔。
4. **managementLog 不改**：`src/memory/managementLog.ts:49` 构造函数只接已打开的共享连接（无字符串分支、无 ownsDb），openStores 处已覆盖——**不要**为了"完成任务"给它新加字符串分支，那是扩接口面。

数值抽成一个常量（如 `BUSY_TIMEOUT_MS = 5000`，放 `src/store/` 下的小文件），各引用处统一引它、别写魔法数。retrieval/evidence 等目录反向 import `src/store/` 这个常量文件是**允许的**（新增依赖方向，仅此一个常量）。不进 config（这不是产品参数，是底座参数；进 config 会扩公共接口面，与 Surface Contract 主线冲突）。

## 为什么 WAL 本批不做（防有人"顺手"开）

`src/store/migrations.ts:125-126` 的迁移前自动备份**只拷主库文件**。开 WAL 后热数据在 `-wal` 文件里，只拷主文件会拿到不一致备份——先开 WAL 等于把迁移备份弄坏。WAL 必须和备份策略一起改，排后续批次。本任务只做 busy_timeout。

## 测试

- 补一条：`openStores(':memory:')` 后 `db.prepare('PRAGMA busy_timeout').get()` **返回 `{ timeout: 5000 }`**——注意结果列名是 `timeout`（Node 24 实测），断言取 `.timeout === 5000`，别断言裸数字。文件路径直连的 store 与 VectorRetriever 同验。挂在 tests/ 下现有 store 相关测试文件或新建（先 `ls tests/` 认准命名习惯）。
- 真并发抢锁的集成测试**不要求**（起两个进程的测试又慢又脆；busy_timeout 是 SQLite 自身语义，验证 pragma 生效即可）。

## 验收

- [ ] pragma 测试绿 + 现有全部测试三绿（迁移测试有独立开连接的路径，确认没漏设也没重复设出错）。
- [ ] CHANGELOG 记一条（行为变化：多进程写冲突从立刻报错变为最多等 5 秒——对外可感知）。
- [ ] `docs/integration.md` 第 6 节「摄入行为观察」（约 163 行起）末尾新增一句："库内已设 busy_timeout=5000；仍不建议两个进程同时跑写路径。"（该文档现无任何多进程段落，落点写死在这，别找"若有"。）
