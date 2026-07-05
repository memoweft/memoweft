# 第 4 步 · 英文化与模型兼容（0.4.0）· 任务书**草稿**

> **状态：A 档大方向作者已拍板（2026-07-05）；6 张分卡已拆出 → `T1`–`T6`（本目录）。** 可开工，波次见下「并行冲突图」。
> 依据：`后续批次总纲.md` 第 4 步（第 18–20 行）+ 五路只读清点（2026-07-05，全带 file:line 证据、已主线亲验）。体例照 `quality-evidence/`。
> 执行者：任何 AI 会话。开工前必读 `AGENTS.md`，然后只读本目录里自己领的那份分卡 + 它点名的源码文件。

## 批次目标

让英文开发者**从 npm 首页到跑通全程零中文、零求助**（总纲完成标志）。落成三件事：

1. **英文化**：库产出（提示词/事件摘要/认知文本）与面向宿主/用户的文案（问法/兜底/报错）中英双语或英文化；INSTALL / integration 英文版；examples 扩到 3 个、以包名为入口。
2. **模型兼容**：`temperature` 可配；响应解析兼容 reasoning 模型的分段输出（`<think>…</think>`）。
3. **兑现两笔延期账**：`hostId` 默认名改掉（T5-repo-cleanup 延到 0.4 的决策）；文档明示「当前明文落盘，磁盘加密属宿主/系统责任」。

**红线**：不改认知纪律判定算法（`consolidation/`、`confidence.ts`、`cognition/`）；双语化只改喂模型文本的**语言**、不改**判定语义**；解析加固只「去噪」、不「放宽」任何纪律阈值；零运行时依赖（双语层**不许引 i18n 库**）；`DLA_*` 环境变量回退与 `./dla.db` 默认路径不动。

## 现状底座（已亲验 · 2026-07-05）

- **三绿**：`typecheck` 0 错 / `test` 194 pass 0 fail / `lint` 0 错 6 警（存量）。
- **零运行时依赖坐实**：`package.json:43` `"dependencies": {}`；`better-sqlite3` 是可选 peer（`_comment` 已说明）。
- **temperature × 置信度隔离坐实**：`confidence.ts:13-18` 的 `ConfidenceInputs` 只吃 `{contentType, formedBy, supportCount, contradictCount}`，**无 LLM 自报、无 temperature 入口**（第 4 行注释：「confidence 由 MemoWeft 按规则算，不采信 LLM 自报」）。放开 temperature 碰不到置信度。
- **全库无 i18n/locale 机制**：grep `lang|locale|i18n` 仅命中无关的 `localeCompare`；所有中文皆硬编码字面量。双语层要**从零起一个「当前语言从哪来」的入口**。
- **认知纪律 eval 回归网**（全批验收锚点）：离线套 `tests/eval/cognition-discipline.eval.test.ts`（进 `npm test` 护栏，~21 例，stubLLM 断产出结构/状态，**不断提示词中文字面**）+ 真模型套 `tests/eval/cognition-discipline.eval.e2e.ts`（`test:e2e`，未配 LLM 整组 skip，现输入为中文）。

## 决策（A 档作者已拍板 2026-07-05 · B 档推荐默认照走）

### 作者已拍板（2026-07-05）

- **A1 双语层机制 = 运行期语言开关**：`config` 加 `language` 字段 + 可选 env `MEMOWEFT_LANG`，在 zh/en 常量表间切，库自带双语（零依赖、纯常量表实现，与 `temperature` 同注入口）。
- **A2 缺省语言 = 英文 `en`**：库默认出英文。**已知并接受的破坏性默认**——现有中文宿主的落库文本（事件摘要/假设等 content）将变英文，产出结构不变。
- **A3 hostId 新默认名 = `'local'`**：中性占位，守 `naming.md`（非人格名）。
- **A4 老数据迁移 = 不迁移**：`host_id` 非查询键（已亲验），老库照读，仅新证据用新名；同库 host_id 新旧混存可接受。
- **B 档 13 项**：作者未否任何一条 → 全部按推荐默认执行（见下表）。

> 下方 A 档表保留为**决策依据留档**（含未采纳选项与权衡），施工按上面「已拍板」执行。

### A 档决策依据（留档 · 含未采纳选项）

| # | 决策 | 选项与前提 | 影响 |
|---|---|---|---|
| **A1** | **双语层机制**（gate T1/T2） | (a) **运行期语言开关**：`config` 加 `language` 字段（+可选 env `MEMOWEFT_LANG`），在 zh/en 常量表间切，库自带双语——**推荐**（契合「内存热调」定稿，与 temperature 同注入口，零依赖纯常量表可实现）。 (b) **单一英文缺省**：库统一出英文，中文交宿主——最省，但丢掉库自带中文体验。 (c) **只英文化报错/日志**，面向用户的问法（asking）彻底推给宿主（守 cell 9「表达归宿主」边界）。 | 决定 T1/T2 的改动量与「库自带中文体验」是否保留 |
| **A2** | **缺省语言 en / zh**（破坏性默认） | 进英文市场 → 默认切 `en`：会改变现有中文宿主的落库文本语言（**产出结构不变**，但事件摘要/假设等 content 变英文）。默认保 `zh`：英文开发者需显式切。 | 破坏现有中文宿主的默认行为 |
| **A3** | **hostId 新默认名**（现 `'testbench'`，兑现 `T5-repo-cleanup.md:20` 延期） | 候选：`'local'` / `'default'` / `'memoweft'`（与 `exportBundle.ts:71` 的 `source.hostId` 默认 `'memoweft'` 统一）/ 宿主必填无默认。**守 `naming.md`：不取人格名（非「星瑶」），中性/工程化。** | 今后新落库证据的 `host_id` 取值 |
| **A4** | **hostId 老数据迁移** | (A) **不迁**：`host_id` 非查询键（**已亲验**：全仓 `host_id` 仅现于 `evidence/store.ts` 的 schema 列 / toRow / fromRow / INSERT，无任何 `WHERE host_id=`、无索引），老库照读照查，仅新证据用新名——**技术上足够**，同库 host_id 新旧混存。 (B) **一次性脚本**批量改老库 `'testbench'`→新名（口径统一；按纪律**删改类脚本先在副本库验**）。 | 老库一致性 vs 省事（推荐 A） |

### B 档 · 推荐默认（你不反对即照走）

| # | 决策 | 推荐默认 |
|---|---|---|
| B1 | few-shot 示例文本是否随指令一起英文化 | **是**（避免英文指令 + 中文示例混排） |
| B2 | `templateQuestion`（asking 兜底问法）归哪路 | 归 **T2**（宿主文案路），与 `PHRASE_SYSTEM`（提示词路 T1）分开，避免两路重复清点 |
| B3 | 切语言后历史数据中英混存 | **不迁移、不标注**（语言非纪律维度，记≠信不受影响） |
| B4 | 内部日志（`jsonRepair` 正文、`managementApi.ts:438` console）是否一并英文化 | **是**（统一，顺手） |
| B5 | 报错里 `DLA_*`/`MEMOWEFT_*` 双前缀说明的英文写法 | 保留 **"DLA_* still supported"**（守兼容承诺，别顺手删） |
| B6 | 是否连 tests 中文 fixture 一并英文化 | **否**（本轮只英文化 `src`；`asking.test.ts:28` 断言不受影响，改 fixture 反而牵连断言） |
| B7 | subjectId 默认 `'owner'` 动不动 | **不动**（分区键，改=换库主人+老数据失联；`configInjection.test.ts:67` 硬断言 'owner'。若要改必单独立项配迁移） |
| B8 | temperature 落地 | `LLMConfig` 加 `temperature?`，`loadLLMConfig` 从 env 读，body 用 `?? 0.3` 保缺省——**不配 = 全 0.3，零行为变更**。按 `LLMPurpose` 可分别配（write 已有独立 env 前缀 `MEMOWEFT_WRITE_LLM_*`，天然可加位）；**是否给 write 设更低的缺省值是另一小决策，默认不设、保持 0.3**。键名 `MEMOWEFT_LLM_TEMPERATURE` / `MEMOWEFT_WRITE_LLM_TEMPERATURE`，沿双前缀 |
| B9 | reasoning 兼容落点 | **在 client 取 content 后剥 `<think>…</think>`**（一处全用途受益）为主守——**只剥有闭合 `</think>` 的成对标签；无闭合标签不动，防把真答案误剥**。`extractJsonObject` 改「括号配平扫描」为兜底；`reasoning_content` 字段忽略即可 |
| B10 | examples 新增 2 个选题 | **②记忆管理 `memory.*` + ③便携包 `portable`**（最能展示差异化卖点）——可换 |
| B11 | examples 入口 | **全改包名 `'memoweft'`** + 头注「先 `npm run build` 再跑」（贴真实宿主用法，满足「以包名为入口」） |
| B12 | INSTALL/integration 英文化范式 | 照 README 已确立范式：**主文件转英文 + 新建 `.zh-CN.md` 留中文 + 顶部互链**；顺手把旧散装 API 示例对齐 `createMemoWeftCore`，**必修** `integration.md:168` 跑不通的 `activeWindowToObservation` |
| B13 | 明文落盘声明落点 | 英文进 `deployment.md:122` Design rules 段，中文进 INSTALL 隐私基线；措辞用总纲原话 |

## 任务清单（6 卡 · 待 A 档定后拆独立施工卡）

| 序 | 卡 | 一句话 | 大小 | 依赖 / gate |
|---|---|---|---|---|
| **T1** | 双语层地基 + 提示词双语化 | 建双语机制（A1）+ 译 8 文件的 `SYSTEM`/user 骨架/`knowledgeBlock`/`JSON_ONLY_NUDGE` | 中（**最核心**） | gate = A1/A2 |
| **T2** | 宿主/用户文案双语化 | asking 兜底问法 + 回话失败文案 + 一堆 `throw` 报错 + portable 校验文案 | 中 | 依赖 T1 机制；与 T1 争 `asking/*` |
| **T3** | temperature 可配 + reasoning 解析兼容 | client 加 `temperature`（B8）+ 剥 `<think>` + `extract` 加固（B9） | 小 + 中 | 与 T1 争 `jsonRepair.ts`；与 T2 争 `client.ts`（temperature 走 `LLMConfig`+env，**不碰 `config.ts`**） |
| **T4** | hostId 默认名改名 | `config.ts:89` 一行 + 连带文档/测试文案更新 | 小 | gate = A3/A4；与 T1/T3 争 `config.ts` |
| **T5** | examples 扩到 3 + 以包名入口 | `minimal.ts` 改包名 + 补 2 例（B10）+ 修旧/坏示例 | 中 | 软依赖 T4（示例 hostId）、最终 API |
| **T6** | INSTALL/integration 英文化 + 明文落盘声明 | 两文英文版 + 修坏示例 + 补声明 | 中偏大 | 软依赖 T5（示例）、T4 |

## 并行冲突图（worktree 隔离并行时看合并顺序）

**热点文件与抢占方**：

- `src/config.ts`：T1（加 `language`）/ T4（改 `identity` 默认 @:89）。→ **T1 先合**奠定 config 双语位；T4 动的是独立行（:89），与 T1 几乎不冲突。（**注**：T3 的 `temperature` 落在 `LLMConfig`（`client.ts`）+ env，**不进 `config.ts`**，故不占此热点。若日后要「内存热调」再把 temperature additive 加到 `MemoWeftConfig`。）
- `src/asking/{proposeAsk,revisitConflicts}.ts`：T1（`PHRASE_SYSTEM`）/ T2（`templateQuestion`）同两文件。→ **T1 先，T2 跟**。
- `src/llm/jsonRepair.ts`：T1（`JSON_ONLY_NUDGE` 双语）/ T3（`extractJsonObject` 加固）——不同区块，合并留意。
- `src/llm/client.ts`：T2（`throw` 报错）/ T3（`temperature` + 剥 `<think>`）——不同区块。

**建议波次**：波1 = **T1（奠基）** → 波2 = **T2 / T3**（踩 T1 的 config + 机制，并行但注意 asking/jsonRepair/client 分区）**+ T4**（独立，可任意波）→ 波3 = **T5** → 波4 = **T6**（踩最终 API/示例）。

## 全局规矩（照 0.3.0 / quality-evidence）

1. **三绿**：`npm run typecheck && npm test && npm run build` 全过才算完成（`AGENTS.md` 铁律）。
2. **不扩范围**：只做卡里写明的事，顺手发现记进卡末「发现待办」。
3. **防偏移三问**：对应商用五关哪一关？给库加固还是给宿主加戏？动没动灵魂（认知纪律 / 隐私三红线 / 零运行时依赖）？——**本批任何卡不许改认知纪律判定算法**；双语化只改文本语言不改判定语义；解析加固只去噪不放宽。
4. **提交口径**：一卡一提交，说明写短，CHANGELOG 有行为变化才记。
5. **兼容红线**：`DLA_*` 回退与 `./dla.db` 默认路径保留。
6. **接口契约同步**：T1/T3 若给 `MemoWeftConfig`（`index.ts:209` 标 **[stable]** 的形状）加 `language`/`temperature` 可选字段，属 **additive、非破坏**（旧宿主不传照跑），但须同步 `docs/memory-surface-contract.md`（第 2 步产物）与 `index.ts` 分级注释；若走**纯 env**（不进 config 形状）则不碰契约。`LLMConfig` 是 [experimental]，加字段无契约负担。

## 批次验收（草案 · 全批合完跑一遍）

- [ ] 按 A2 的缺省语言下：examples 全跑通、`npm test` 194+ 全绿、离线 eval 全绿。
- [ ] 真模型 e2e 套补/换**英文对话输入**，三条纪律（冲突暴露不合并 / 情绪封顶 / 记≠信）在英文侧仍成立。
- [ ] 新增单测：带 `<think>` 前缀（含花括号）的响应能正确抠出 JSON；重试路径也剥干净；**无闭合 `</think>` 的畸形响应不被误剥**（真答案保住）。
- [ ] `temperature` 可配且默认行为不变（不配 = 0.3）；`temperature` 不出现在 `confidence` 任何入参。
- [ ] `hostId` 默认名已改；不迁移路径下老库仍可读。
- [ ] examples = 3 个、全以包名 `import`、各自跑通。
- [ ] INSTALL / integration 英文版存在且与中文版互链、无跑不通示例；明文落盘声明在 deployment / INSTALL 各一处。
- [ ] runtime `dependencies` 仍为 `{}`。

## 本批明确不做

- 不改 `confidence.ts` / `consolidate.ts` 等置信度、认知纪律判定算法（只可能改喂模型的**文本语言**）。
- 不动 `subjectId` 默认（分区键）。
- 不引任何 i18n / runtime 依赖。
- 不做磁盘加密（只文档声明责任归属）。
- `apps/*` / `plugins/*` / testbench UI 的英文化不在本批（核心库 `src` 优先；除 T4 连带的少量身份文案）。
- 不赌真模型每次判定，e2e 断言保持宽松。

## 附 · 五路清点证据索引（file:line）

> 本索引的高风险断言（config/client/jsonRepair/pool/host_id 用法/exportBundle/integration/consolidate/confidence/package.json/eval 位置）已于 2026-07-05 **主线亲验核对，零漂移**。（3 视角对抗校对工作流因网络中断未产出，故证据核对改由主线亲做。）

**A1 提示词（发给模型）**：`action.ts:11` SYSTEM_PROMPT、`action.ts:25` knowledgeBlock（把握度文案）、`distill.ts:24` SYSTEM、`consolidate.ts:92` SYSTEM（14 条 + few-shot，最核心）、`attribute.ts:56` SYSTEM、`trends.ts:44` SYSTEM、`proposeAsk.ts:65` PHRASE_SYSTEM、`revisitConflicts.ts:32` PHRASE_SYSTEM、`jsonRepair.ts:56` JSON_ONLY_NUDGE + 各处 buildMessages 中文骨架标签（【现有画像】【新材料】…约十来处）。

**A2 宿主/用户文案**：`proposeAsk.ts:72` / `revisitConflicts.ts:48` templateQuestion（问法兜底）、`conversation.ts:91`「（回话失败，但你的话已存为证据）」、`client.ts:56/96/102/109`、`pool.ts:36`、`embedder.ts:66/72/77`、`migrations.ts:53/98`、`nodeSqliteDriver.ts:83/96`、`managementApi.ts:289-297`（6 处 throw）/ `:438` console、`validateBundle.ts`（~20 条 errors/warnings）、`importBundle.ts:150/151`。

**B temperature / reasoning**：`client.ts:90` `temperature: 0.3` 硬编码（唯一处）、`client.ts:104-107` 只取 `choices[0].message.content`（不理 `reasoning_content`/`<think>`）、`jsonRepair.ts:24-30` `extractJsonObject` 贪婪取最外层花括号（`<think>` 含花括号会污染）、`pool.ts:14` 用途仅 `'chat'|'write'`。

**C hostId**：`config.ts:89` `identity: { subjectId:'owner', hostId:'testbench' }`（唯一 set 点）、`perceive.ts:19` / `ingest.ts:61` 缺省注入、`evidence/store.ts:21` host_id 普通列（无 WHERE/index）、`exportBundle.ts:71` 另一独立默认 `'memoweft'`、`configInjection.test.ts:67` 断言 'owner'、`config-meta.js:27`「v1 恒为 testbench」、`T5-repo-cleanup.md:20` 延期出处。**反向证据**：无 `DLA_HOST_ID`/`DLA_SUBJECT_ID` env（改名碰不到 DLA_* 红线）；`./dla.db` 与 identity 独立。

**D examples / 文档 / 明文落盘**：`examples/minimal.ts`（唯一）、`:25` 相对路径 import（非包名）、`:44` hostId:'example'、`docs/INSTALL.md`（整篇中文，§8-9 旧散装示例 @:197）、`docs/integration.md`（整篇中文，§5 旧散装 @:97、**§6 跑不通** `activeWindowToObservation` @:168）、`docs/deployment.md:122`（已全英文，明文落盘声明天然落点）、`openStores.ts:42` `new DatabaseSync` 无加密（明文落盘属实）、`后续批次总纲.md:19` 声明出处。README 已走通「主英文 + `.zh-CN` + 互链」范式（可复用）。
