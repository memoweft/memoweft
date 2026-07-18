# tests/fixtures

## `memoweft-0.1.0.db` — 冻结的 0.1.0 schema fixture（**不要重新生成**）

一个由 **0.1.0 版 schema** 创建的 SQLite fixture：`user_version = 0`（0.1.0 还没有版本化），含 6 张表（evidence / event / event_evidence / cognition / cognition_evidence / management_log）+ 若干条 demo 数据（subject `demo`：2 条 evidence、2 条 cognition）。所有内容均为合成测试数据，不来自用户、日志或产品遥测。

`tests/migrations.test.ts` 用它验证 **"0.1.0 老库经 openStores 打开 → 无损升级、数据不丢"**，以及 fresh 库与"从本 fixture 迁上来的库" **schema 签名一致**（防"新库靠 store SCHEMA、老库靠迁移"两条路悄悄跑偏）。

**这是冻结的兼容性基线**：不要用当前代码重新生成它，否则 `SCHEMA` 变化会让名为 "0.1.0" 的 fixture 实际包含新 schema，失去迁移测试价值。该文件由等价于 0.1.0 的 store 实现生成，之后保持只读。
