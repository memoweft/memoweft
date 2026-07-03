# reference/ · 只读基线快照

本目录是 DLA → MemoWeft 迁移时冻结的旧代码**只读基线快照**（来自 ../DLA_project），不在其上开发。
它**不参与构建与测试**（tsconfig include 与测试 glob 均不含本目录），**不再维护**，仅供对照历史实现。

- `migrated-baseline/` — 旧 Event 存储 / 最短闭环 / 短期窗口 / 测试台 + 已废件（topic 召回、单一权重、State/Profile 双层）。

现行实现一律在仓根 `src/`。
