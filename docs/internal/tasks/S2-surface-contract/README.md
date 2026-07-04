# S2 · Memory Surface Contract v1（记忆面契约 v1）· 任务书总览

> 归属：总纲第 2 步（0.3.0 补漏批之后的下一主线）。是第 7 步插件契约、第 10 步 1.0 API 收口的共同地基。
> 定性：把宿主接触记忆的公共接口，从"171 个符号平铺、谁稳谁不稳全靠猜"，变成一份带稳定性标签的清单。**交付物是文档 + 类型/导出标注，不动核心运行时逻辑。**
> 执行者：任何 AI 会话。开工前必读 `AGENTS.md`，然后只读本目录里自己领的那份任务书 + 它点名的源码文件。

## 背景

2026-07 全库审计记过："`src/index.ts` 现状 51 个值 + 120 个类型（共 171 符号）全裸奔、无稳定性分级"。宿主 `import 'memoweft'` 面对的是一堆没贴标签的东西，分不清"能靠"和"别碰"。本步给这条公共面贴上稳定性标签、写清破坏性政策，成为宿主可依赖的契约。

## 稳定性三档（判定标准）

- **stable（稳定面）**：宿主日常靠它做事、已门面收口、形状定型。承诺"不随手改"。—— `createMemoWeftCore` 及 `core.*` 24 个宿主接触方法、它们的入参/返回形状、三层落库形状（Evidence/Event/Cognition）、便携包 `MemoryBundle`、图谱 `MemoryGraphPayload`、`MEMOWEFT_VERSION`、`BUNDLE_FORMAT/BUNDLE_SCHEMA_VERSION`。
- **experimental（试验面）**：导出了、宿主可能碰，但**明说会变**，改了不算爽约。—— 源码已注明"以后要变 / V1 未生成"的字段（`Observation.meta`、`Observation.kind` 开放集、`ImportMode.replace`、图谱 `conflicts_with`/`corrects` 边、`Cognition.askedAt`）、可替换扩展点接口（`Retriever`/`Embedder`/`LLMClient`）、`openStores`/`StoreBundle`、`LATEST_SCHEMA_VERSION`、`createRunLogger`，以及 **config 的"取用方式"（见作者拍板 ⑥）**。
- **internal（内部件）**：门面已收口、宿主没理由碰的散装实现件 —— 三层 `Sqlite*Store` 实现类、写路径算子（distill/consolidate/updateProfile/attribute/revisitConflicts）及其 `*Deps`/`*Result`、`jsonRepair`、`perceive`/`WorkingMemory`、各 config loader、`recallCognitions` 散装函数。**本步只贴"别碰"牌、不删导出**（删=破坏性变更，属第 10 步）。

## 破坏性变更政策（pre-1.0，写进契约文档）

- **什么算破坏 stable**：改字段名 / 删字段 / 改可空性 / 改语义（如 confidence 量纲）。
- **破坏 stable 的代价（中间偏松·作者拍板 ②）**：允许在 minor 版破，但必须 ① CHANGELOG 明确标注 ② 给一句迁移说明（旧→新怎么改）③ 能保旧名的走 `@deprecated` 别名（照 `DlaConfig`/`DLA_VERSION` 样板）——**不强制"保留整一个版本再删"**。
- **枚举加值不算破坏（作者拍板 ③）**：给 `SourceKind`/`ContentType`/`CredStatus` 等加新取值不算破坏，但**契约必须明写"宿主对这些枚举要留 default 兜底分支"**，把漏分支的责任讲清、划给宿主。
- **experimental 面**：minor 版随便改，CHANGELOG 提一句即可，不欠迁移说明。

## 交付形态（作者拍板 ④⑤）

- **主交付 = 一份面向宿主的 Surface Contract 文档**，放 **`docs/memory-surface-contract.md`**（和 INSTALL/integration 同级，宿主直接读——这是对宿主的承诺，不藏进 internal）。单一事实源：逐门面方法 / 逐数据形状列级 + 破坏性政策 + 隐性契约。
- **辅助 = `src/index.ts` 分组注释**：每个导出分组块头加一行 `// [stable]` / `// [experimental]` / `// [internal]`，grep 可验、只改一个文件。
- **不逐符号铺 `@stable` JSDoc**（碰 16 个子模块源、易漂移，性价比低）；两处现有 `@deprecated` 保留。

## 作者已拍板（6 项，执行会话照此，不必重新权衡）

1. internal 件本步**只标不删**（删留第 10 步）。
2. 破坏性政策**中间偏松**（标注 + 迁移说明 + 能留别名就留，不强制保一版）。
3. 枚举加值**不算破坏**，但契约写明宿主须留 default 兜底。
4. 标注方式 = **契约文档 + index.ts 分组注释**（不逐符号 JSDoc）。
5. 契约文档放 **`docs/`**（宿主直接读）。
6. config：**"有哪些配置项"标 stable，"怎么拿到 config（单例访问）"标 experimental**（预留 P2-5 去单例路线）。

## 任务清单与顺序

| 序 | 任务书 | 一句话 | 大小 | 依赖 |
|---|---|---|---|---|
| 1 | [S2-1](./S2-1-contract-doc.md) | 存疑项定级 + 写 Surface Contract 文档 | 中 | 无 |
| 2 | [S2-2](./S2-2-index-annotations.md) | `index.ts` 分组稳定性注释 + 破坏性政策成文 + CHANGELOG 对齐 | 小 | S2-1（定级结论）|

S2-1 定"每个符号是什么级"，S2-2 把结论落到 `index.ts` 注释和政策文档。两份都不动运行时代码。

## 明确不动（守灵魂）

- 任何入参 / 返回的实际形状、运行时行为；不删导出、不重排导出结构（属第 10 步）。
- 认知纪律 / 隐私三红线 / 零运行时依赖。
- `DLA_*` 回退、`'./dla.db'` 默认路径、两处 `@deprecated`（`config.ts:136`、`index.ts:210`）。

## 关键数字（校对核准，验收判据用）

- **宿主接触方法 = 24**：`createMemoWeftCore`(1) + 门面顶层方法 8（`ingestUserMessage` / `ingestObservation` / `recall` / `handleConversationTurn` / `dropConversation` / `updateProfile` / `health` / `close`）+ `core.memory` **11** + `core.portable` 3 + `core.graph` 1。
- `src/index.ts` 导出：**19 个能力分组 / 38 条 `from './` 语句**。
