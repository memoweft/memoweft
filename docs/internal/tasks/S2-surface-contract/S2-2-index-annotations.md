# S2-2 · index.ts 分组稳定性注释 + 破坏性政策成文

**对应五关**：同 S2-1，库对外契约纪律。
**依赖**：S2-1（用它定好的每符号级别）。

## 背景

S2-1 定完每个符号的级，本任务把结论落成"机读可 grep"的形态：在 `src/index.ts` 每个导出分组块头加一行稳定性注释。`index.ts` 现在是纯 re-export 清单（无逻辑）、按能力分了组，加分组注释是最省事、最集中、不碰子模块源码的落法。同时把破坏性变更政策写进契约文档、CHANGELOG 口径对齐。

## 改哪里

### 1. `src/index.ts` 每个导出分组加一行稳定性注释

格式统一为 `// [stable] 说明` / `// [experimental] 说明` / `// [internal] 说明`。按 S2-1 定级归类，例如：

- **`// [stable]`** — 统一 Core 入口块（`createMemoWeftCore` / `MemoWeftCore` 及各 `*Input`/返回类型）、受控记忆管理 API（`MemoryManagementAPI` 接口 + 各入出参类型）、便携包格式常量与包结构类型、图谱 payload 类型、`MEMOWEFT_VERSION`、三层领域形状（`Evidence`/`Event`/`Cognition` 及按 S2-1 结论的入参）。
- **`// [experimental]`** — 召回 / 嵌入 / LLM 扩展点接口块（`Retriever`/`Embedder`/`LLMClient`）、`openStores`/`StoreBundle`、`LATEST_SCHEMA_VERSION`、`createRunLogger`/`RunLoggerOptions`，以及 **config 面：`config`/`cloudReadDefault` 的取用方式标 experimental（作者拍板 ⑥：配置项形状 stable、但"怎么拿到 config"这套单例访问 pre-1.0 可能变，预留 P2-5 去单例）**——注释里写清这个拆分。
- **`// [internal]`** — 三层 `Sqlite*Store` 实现类、写路径算子三件套（distill/consolidate/updateProfile/attribute/revisitConflicts + `*Deps`/`*Result`）、`jsonRepair`、`recallCognitions` 散装函数、`perceive`/`WorkingMemory`、各 config loader、散装 `exportBundle`/`buildMemoryGraph`（已被门面收口）、`SqliteManagementLog`。
- **混级分组**（如证据层块里 `Evidence` 系 stable、但 `SqliteEvidenceStore` 是 internal）：把该块的导出**按级分成两段、各加一行注释**——**只重排注释与导出行的分段，绝不增删导出的符号本身**。

### 2. 契约文档补"破坏性变更政策"章（写进 `docs/memory-surface-contract.md`）

- 破坏 stable 三要件（作者拍板 ②，中间偏松）：① CHANGELOG 标注 ② 迁移说明 ③ 能保旧名的走 `@deprecated` 别名——不强制保留整一版。
- experimental 面：minor 随改、CHANGELOG 提一句即可。
- 枚举加值口径（作者拍板 ③）：加值不算破坏，宿主须留 default 兜底；收窄才算破坏。
- 引 `DlaConfig`（`config.ts:136`）/ `DLA_VERSION`（`index.ts:210`）两处现成 `@deprecated` 作"已弃用样板"。

### 3. CHANGELOG 对齐

现有那句"pre-1.0 minor 可能含破坏性变更"后补一句指向契约文档（"稳定性分级与破坏性政策见 `docs/memory-surface-contract.md`"）。CHANGELOG 记一条 Added：Memory Surface Contract v1（文档 + `index.ts` 稳定性分级注释）。

## 不许动

- 任何 `export` 语句导出的**符号集合**——本任务只加 / 调注释与分段，**不增删导出的名字**（增删导出 = 破坏性变更，属第 10 步）。
- 各子模块源文件里的 JSDoc——本步不逐符号铺 `@stable`，只在 `index.ts` 分组注释落级。
- 两处现有 `@deprecated`（`config.ts:136`、`index.ts:210`）保持原样。
- 运行时逻辑、re-export 的 `from` 路径。

## 验收

- [ ] `src/index.ts` 每个导出分组块头都有稳定性注释：`grep -c "// \[stable\]\|// \[experimental\]\|// \[internal\]" src/index.ts` **≥ 19**（现有 19 个能力分组各至少一行；混级分组拆段后只会更多）；且 **38 条 `from './` 语句**没有一条落在无稳定性标签的分组里（逐组目视 + grep 交叉核）。
- [ ] **防越界机读闸**：`git diff src/index.ts` 的新增 / 改动行**只含以 `//` 开头的注释行、或纯导出行的分段重排**，不含任何 `export` 符号名字的增 / 删 / 改。
- [ ] `git diff` 只动 `src/index.ts` + `docs/memory-surface-contract.md` + `CHANGELOG.md`，无其他 `src/**` 改动。
- [ ] 契约文档"破坏性政策"章存在，含 stable 破坏三要件 + experimental 松口径 + 枚举加值口径（按作者拍板 ②③）。
- [ ] `npm run typecheck && npm test && npm run build` 三绿（只加注释 + 调导出分段，符号集合不变，必须绿）。
- [ ] `npm pack --dry-run` 导出面无实际符号增减（对照 S2-2 前后一致）。

---

## 全局规矩（S2-1 / S2-2 都默认包含）

1. **三绿**：`npm run typecheck && npm test && npm run build` 全过才算完成。本步不改运行时，三绿应无实质变化，跑是为兜"没误碰"。
2. **不扩范围**：只做任务书写明的。删导出、重排导出结构、逐符号铺 JSDoc、动 config 单例实现——都属第 10 步或另立，本步不做，发现记末尾"发现待办"。
3. **防偏移三问**：这是给库对外契约打地基（第 7/10 步地基）；是给库加纪律不是给宿主加戏；本步只加文档与注释、天然不碰灵魂——但若发现自己在改运行时形状 = 越界了，停。
4. **提交口径**：一份任务一个提交、说明写短。契约文档与政策属对外行为文档，S2-2 提交须在 CHANGELOG 记一条。
5. **兼容红线**：`DLA_*` 回退、`'./dla.db'` 默认路径、两处 `@deprecated` 别名——本步全不许动。
