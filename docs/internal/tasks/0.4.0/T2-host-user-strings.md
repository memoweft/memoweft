# T2 · 宿主/用户文案双语化

> 把「不发给模型、而是交给宿主或最终用户看/用」的中文串双语化。共用 T1 的语言开关。**波2，依赖 T1 机制先合。**

## 背景

面向宿主/用户的文案全是硬编码中文字面量，散在约 11 个文件，分三类：(1) 问法模板（asking 兜底问法）；(2) 兜底/降级文案（回话失败、导入校验）；(3) 报错/日志（`throw new Error('中文…')` 与 console）。

**关键前提（已核）**：现有 25+ eval/单测**喂中文 fixture、断言行为**，**没有一个断言模板或报错的固定中文措辞**（grep 具体短语零命中）。唯一沾边 `asking.test.ts:28` 断言 `p.question.includes('喝茶')`，但「喝茶」来自被插值的中文 `content`（fixture），非模板固定词——**改英文模板 eval 仍全绿**（除非同时改 fixture，本轮不改，见 B6）。

## 作者已拍板（本卡相关）

- **A1/A2**：用 T1 的语言开关，缺省 `en`。
- **B2 = `templateQuestion` 归本卡**（宿主文案路），与 `PHRASE_SYSTEM`（提示词路 T1）分开。
- **B4 = 内部日志一并英文化**（统一，顺手）。
- **B5 = 报错保留 `DLA_*` 兼容说明**：英文化后仍写出 "DLA_* still supported"，别顺手删。
- **B6 = 不动 tests 中文 fixture**（只英文化 `src`）。

## 改哪里

用 T1 的取词机制按 `config.language` 选。

**A) 面向用户必须双语（优先）**

1. `proposeAsk.ts:72` `templateQuestion`（`我看到${shown}，所以在想：${hypothesis}。是这样吗？` 及无证据分支）——抽双语，**保留插值位 `${shown}/${hypothesis}` 与「带证据留余地」语气**。
2. `revisitConflicts.ts:48` `templateQuestion`（`关于"${content}"——一方面${s}，另一方面又${c}。现在到底是哪样呢？` 及单面分支）——同上。
3. `conversation.ts:91` 回话失败兜底「（回话失败，但你的话已存为证据）」——直接返给宿主/用户，优先。
4. `nodeSqliteDriver.ts:83/96` 驱动缺失长文案（含升 Node≥24 / 装 better-sqlite3 指引）——**新用户最易撞见的第一道错**，必须双语。

**B) 面向宿主宜双语**

5. `throw` 报错：`client.ts:56/96/102/109`、`pool.ts:36`、`embedder.ts:66/72/77`、`migrations.ts:53/98/141`、`managementApi.ts:289-297`（6 处合并/校验拒绝原因）。
6. portable 校验文案：`validateBundle.ts`（~20 条 `errors[]`/`warnings[]`）、`importBundle.ts:150/151`（+ `:66/:118` warnings）。
7. **B5**：`client.ts:56/57` 与 `pool.ts:36` 报错里带 `MEMOWEFT_*`/`DLA_*` 双前缀说明——英文化后保留 "DLA_* still supported" 语义。

**C) 内部日志一并英文化（B4）**

8. `jsonRepair.ts:73/82` 默认 sink 正文、`managementApi.ts:438` `console.error('resetSubject 清向量索引失败…')`。

## 不许动

- asking 的**纪律逻辑**（只改兜底措辞，不改「只问低置信假设 / 带证据可证伪 / 提问不入证据库」）。
- `throw` 的**触发条件与控制流**（只改 message 文本）。
- `DLA_*` 兼容语义（B5，别删提示）；不动任何读 env 的回退逻辑。
- tests 中文 fixture（B6）。
- 不引 i18n / runtime 依赖。

## 验收（可执行核对）

- [ ] 三绿；离线 eval 全绿（无测试断言固定中文措辞；`asking.test.ts:28` 断的是插值内容不受影响）。
- [ ] `language` 切换下上述文案中英切换生效；缺省 `en`。
- [ ] 报错英文版仍含 "DLA_* still supported" 类兼容说明（`grep -i "DLA_" src/llm/client.ts src/llm/pool.ts` 命中）。
- [ ] runtime `dependencies` 仍 `{}`。

## 与其它卡的关系

- **依赖 T1 机制先合**（取词入口）。
- 与 T1 争 `asking/{proposeAsk,revisitConflicts}.ts`（T1 改 `PHRASE_SYSTEM`、本卡改 `templateQuestion`）→ **T1 先，T2 跟**。
- 与 T3 争 `client.ts`（本卡改 `throw` 报错、T3 改 temperature+剥 think，不同区块）→ 注意分区合并。

## 发现待办

- A1 探测备注：`apps/*`、`plugins/*`、testbench UI 里也有面向用户中文，但那属「可选带界面」的宿主侧，**不在本批** 0.4.0 核心库 `src` 英文化范围（见 README「本批明确不做」）。
