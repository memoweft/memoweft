# MemoWeft 2.0 主线分层（Owner 拍板：先C后A，2026-08-17）

独立评审（"能力齐备、形态未定"）之后的收敛决定。本文是主线分层的仓库内记录；
产品语义仍以工作区 `MEMOWEFT-2.0-AUTHORITY.md` 为唯一权威。

## 唯一生产正统（production mainline）

`memoweft/integrations/` 下的两条宿主链：

- **Hermes**：boundary store → batch adapter（v8 信封）→ world worker →
  确定性 Recall。live 验证 V1–V8、评测 F1 0.972。
- **WeftMate（DSH 桥）**：同一条编译/落库链的 stdio 包装，同一预算不变量。

生产链**从不导入** `memoweft.world`（原型引擎）与 1.0 parity 模块。

## Parity 保险层（parity insurance layer，永不上生产）

- `memoweft/world/`：loop / turn_meaning / extractor / recall / correction /
  identity 等第二世界引擎原型。只为 1.0 能力继承（decay/asking/conflict/
  correction）提供可验证的 parity 与回归测试设施，无任何生产调用方。
- 1.0 parity 模块：`update_profile` / `consolidate` / `perceive` / `ingest` /
  `store.keyword`（FTS5）。同样只有 parity 测试驱动。

删除保险层模块 ≠ 删除能力；如果某能力未来需要在生产主链表达，应在
integrations 链内实现，而不是把保险层模块接上生产。

## 2026-08-17 收敛动作（A）

- Golden gate 三件套移入 tests：`nanjing_gate` / `recall_gate` /
  `entity_continuity_gate`（冻结语料回归样本归位测试设施，§4.10/§4.11）。
- 删除唯一真死代码：`MemoryView.historical_cognitions`（全仓零引用）。
- 评审"死代码清单"中其余项经核实**并非死代码**（均有 parity/回归测试驱动），
  按保险层保留：`correction.py`、`store.keyword.py`、`PersistentIdentityAuthority`、
  `perceive/ingest`。

## 后续工程（按序自主施工）

1. 五终态在 Hermes 生产链的表达（clarification-required / out-of-scope 现折叠进
   no_change）。
2. DSH 桥对象图召回 + `allow_local_read` 权限门 + export 权限过滤。
3. portable bundle v3（entity / relationship / world_event 可导出）。
