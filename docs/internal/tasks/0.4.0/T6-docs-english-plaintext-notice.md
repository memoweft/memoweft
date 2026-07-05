# T6 · INSTALL / integration 英文化 + 明文落盘声明

> 补齐「从 npm 首页到跑通全程零中文」的最后一段文档。**波4（末尾）。软依赖 T5（示例）与 T4（新默认名）。**

## 背景

- `docs/INSTALL.md`、`docs/integration.md` 两份**整篇中文**（标题/正文/表格全中文，仅代码块与 env 键英文）。docs 下**无 `.en` 变体**。
- **已确立范式可复用**：`README.md` 已走通「主文件英文 + `README.zh-CN.md` 中文版 + 顶部 `English | 简体中文` 互链」；`docs/deployment.md` 已**全英文**。所以是「让 INSTALL/integration 跟上已确立的英文范式」，不是从零决定。
- **两处旧/坏示例**（英文化若直译会把问题固化进英文文档）：
  - `integration.md:168` `import { ingestObservations, activeWindowToObservation } from 'memoweft'`——**已亲验跑不通**：采集整体迁出 Core（`index.ts:205-206` 注释），`activeWindowToObservation` 不再导出。
  - `INSTALL.md:197`（§8-9）与 `integration.md:97`（§5）用**旧散装 API**（`new SqliteEvidenceStore/Conversation…`），与 `README`/`minimal.ts` 推的 `createMemoWeftCore` 脱节。
- **明文落盘声明缺口**：grep 全 docs + README **无一处**写明「数据当前明文落盘、磁盘加密属宿主/系统责任」。事实核实：`openStores.ts:42` `new DatabaseSync(dbPath)` 建标准 SQLite、无 `PRAGMA key`/SQLCipher——**明文落盘属实**。

## 作者已拍板（本卡相关）

- **B12 = 照 README 范式**：主文件转英文 + 新建 `.zh-CN.md` 留中文 + 顶部互链；**顺手把旧散装示例对齐 `createMemoWeftCore`，必修 `integration.md:168`**。
- **B13 = 明文落盘声明**：英文进 `deployment.md:122` Design rules 段，中文进 INSTALL 隐私基线；措辞用总纲原话「当前明文落盘，磁盘加密属宿主/系统责任」。

## 改哪里

1. **`docs/INSTALL.md` 英文化**：主文件转英文（前置条件 / npm 安装 / `.env` 三档配置 / env 键表 / 跑测试台 / 跑最小示例 / API 速览 / FAQ）；新建 `docs/INSTALL.zh-CN.md` 保留中文；顶部互链。§8-9 示例对齐 `createMemoWeftCore`（B12）。
2. **`docs/integration.md` 英文化**：同范式（边界表 / 安装导入 / cloud-first 配置 / 30 秒心智模型 / 端到端最小接入 / 摄入观察 / 接入纪律 / 可替换点 / 常用导出 / 宿主最小责任清单）；新建 `docs/integration.zh-CN.md`；顶部互链。
   - §5 端到端示例对齐 `createMemoWeftCore`（B12）。
   - **§6 必修**：`activeWindowToObservation` 那段——改用通用 `ingestObservation` 观察口重写，或改为指向采集插件文档（`plugins/collector-active-window/`）。**别直译坏示例**。
3. **明文落盘声明（B13）**：
   - 英文：`docs/deployment.md:122` 附近 Design rules / privacy 段补一条 "Data at rest is unencrypted; disk encryption is the host/OS responsibility."。
   - 中文：`INSTALL.zh-CN.md` 隐私基线处补同义一句（总纲原话）。
4. **链接指向**：英文 `README.md` 指英文 `INSTALL.md`/`integration.md`，中文 `README.zh-CN.md` 指 `.zh-CN.md`——**核对双语 README 的 docs 链接不要英链中、中链英**。
5. 示例里的 `hostId` 取值与 T4 新默认 `'local'` 一致。

## 不许动

- 任何 `src/` 算法、`config`、env 回退、`./dla.db` 默认。
- `MEMOWEFT_*` 主名 + `DLA_*` 兼容的现有口径（INSTALL/integration 里照旧写主名 + 注明兼容）。
- 不引 runtime 依赖（纯文档 + 示例）。

## 验收（可执行核对）

- [ ] 三绿（纯文档/示例也跑一遍确认没碰坏）。
- [ ] `docs/INSTALL.md`、`docs/integration.md` 为英文，`docs/INSTALL.zh-CN.md`、`docs/integration.zh-CN.md` 存在，四文顶部互链正确。
- [ ] `integration.md` 无 `activeWindowToObservation`（坏示例已修）；INSTALL/integration 示例走 `createMemoWeftCore`（`grep -n "SqliteEvidenceStore" docs/INSTALL.md docs/integration.md` 若仍现则说明未对齐，需复核）。
- [ ] 明文落盘声明在 `deployment.md`（英文）与 `INSTALL.zh-CN.md`（中文）各一处（`grep -i "unencrypted\|明文落盘" docs/`）。
- [ ] 英文 README 的 docs 链接指英文版、中文 README 指中文版。

## 与其它卡的关系

- 软依赖 **T5**（英文文档里的示例应与 examples 一致、都以包名入口）与 **T4**（示例/文档里的 hostId 用新默认）。**T5 先，T6 跟。**

## 发现待办

- D 探测开放项：`integration.md §6` 改法（重写为 `ingestObservation` vs 指向采集插件文档）涉及采集插件当前对外接口形态，施工时先核 `plugins/collector-active-window/` 实际导出再定。
