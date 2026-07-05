# T1 · 双语层地基 + 提示词双语化

> 本批**最核心、认知纪律软化风险最高**的一卡。奠定全批双语机制，并把所有「发给模型的 prompt 文本」迁进双语表补英文。**波1（奠基），其它卡的语言开关都踩它。**

## 背景

全库无任何 i18n / locale 机制（grep 仅命中无关的 `localeCompare`）。所有发给模型的 prompt 100% 硬编码简体中文，散在 8 个源文件。架构上 prompt 责任已分层干净（`client.ts:4-5` 注释：「问什么不在这里」），利于抽取。

**为何风险最高**：`consolidate.ts:92` 的 `correct（明确纠正）` vs `conflict（矛盾但不替换）` 分界（:97/:100）、`distill.ts:24` 的「不出现助手的话/不加推测」、`attribute.ts:56` 的假设封顶铁律、`trends.ts:44` 的铁律——**这些中文措辞本身就是认知纪律的落地载体**。译英文若语义走样（如把「明确纠正」与「矛盾」的界线译模糊），会让「冲突暴露不合并 / 记≠信」在英文语境下实际弱化。这不是改代码逻辑，是改喂给模型的判定指令。

## 作者已拍板（本卡相关）

- **A1 = 运行期语言开关**：`config` 加 `language` 字段 + 可选 env `MEMOWEFT_LANG`，zh/en 常量表间切，零依赖纯常量表实现。
- **A2 = 缺省 `en`**：不设 language / 不设 env → 出英文。
- **B1 = few-shot 示例文本随指令一起英文化**（避免中英混排）。
- **B2 = `templateQuestion`（asking 兜底问法）不归本卡，归 T2**（它不发给模型）。

## 改哪里

**第一步 · 建双语机制地基**

1. `src/config.ts`：`MemoWeftConfig` 加 `language?: 'zh' | 'en'`（可选）；`config` 单例默认 `language: 'en'`（A2）。可选 env `MEMOWEFT_LANG` 覆盖（`MEMOWEFT_LANG` 无 `DLA_` 旧名，直接读 `process.env`；非法值回落 `'en'`）。
2. 建双语常量表 + 取词 helper：建议 `src/llm/prompts/`（按语言分文件或 `{ zh, en }` 结构）+ 一个轻量取词函数按 `cfg.language` 选。**纯常量、零依赖**。算子（`consolidate`/`distill`/`attribute`/`trends` 等）多已可注入 `cfg`（缺省 = 单例，见 `confidence.ts:25` 范式），顺着 `cfg.language` 取词即可。
3. **契约同步**：`MemoWeftConfig` 是 `index.ts:209` 标 **[stable]** 的形状，加**可选** `language` 属 additive、非破坏（旧宿主不传照跑）——须同步 `docs/memory-surface-contract.md` 与 `index.ts:209` 注释。

**第二步 · 迁移并英译 8 文件的 prompt（逐条等义）**

| 文件:行 | 内容 | 译时要点 |
|---|---|---|
| `action.ts:11` | `SYSTEM_PROMPT`（回话语气） | 纯语气类，宿主可覆盖；等义即可 |
| `action.ts:25` | `knowledgeBlock`（把握度文案「低置信的只是假设，别当定论」） | 把握度透明是纪律规则 7，语义不能弱化 |
| `distill.ts:24` | `SYSTEM`（4 条，禁系统自证） | 「不出现助手的话/不加推测」必须等义 |
| `consolidate.ts:92` | `SYSTEM`（14 条 + few-shot） | **最难**：`correct` vs `conflict` 分界（:97/:100）逐字对齐；`formed_by(stated/inferred)`、`content_type` 枚举保义 |
| `attribute.ts:56` | `SYSTEM`（铁律 + few-shot） | 「不用另一种主观感受解释现象」是难点1封顶 |
| `trends.ts:44` | `SYSTEM`（铁律 + few-shot） | 铁律等义 |
| `proposeAsk.ts:65` | `PHRASE_SYSTEM` | 「简短/真诚/不武断/亮证据」保义 |
| `revisitConflicts.ts:32` | `PHRASE_SYSTEM` | 「不预设立场/两边都点」保义 |
| `jsonRepair.ts:56` | `JSON_ONLY_NUDGE`（重试提示，会再发给模型） | 双语化，否则英文对话冒出中文纠偏突兀 |
| 各文件 `buildMessages` | 中文骨架标签【现有画像】【新材料】【假设】【证据】【现象】【近期反复出现的状态】「用户依次说了」等约十来处 | 逐个入双语表，别只译 SYSTEM 漏 user 骨架 |

few-shot JSON 示例内容（`consolidate` 的「用户喜欢咖啡」等）随指令一起英文化（B1），**只译示例文本、不改字段名与结构**。

## 不许动

- `consolidate/attribute/trends/distill` 的 **JSON 产出结构、字段名、判定语义**——只改「语言」不改「判定」。
- `correct` vs `conflict` 的判定边界必须英文**等义**保留（软化即破纪律）。
- `confidence.ts` 等置信度算法、任何认知纪律判定逻辑。
- 不引任何 i18n / runtime 依赖（纯常量表 + 内置取词）。
- `DLA_*` 回退与 `./dla.db` 默认路径。

## 验收（可执行核对）

- [ ] 三绿：`npm run typecheck && npm test && npm run build`。
- [ ] 离线 eval `tests/eval/cognition-discipline.eval.test.ts` 全绿（stubLLM 不依赖 prompt 语言，结构不变即绿）。
- [ ] `language='en'`（含缺省）时上述 8 处发给模型文本**全英文**；`language='zh'` 时**全中文**（`MEMOWEFT_LANG=zh` 或 config 设 zh，切换真生效）。
- [ ] **真模型 e2e**：`cognition-discipline.eval.e2e.ts` 补/换**英文对话输入**的用例，三条纪律（冲突暴露不合并 / 情绪封顶 / 记≠信）在英文侧仍成立——**这是英文纪律真生效的唯一验证**（离线断不了）。
- [ ] `MemoWeftConfig` 加 `language` 后 `memory-surface-contract.md` 与 `index.ts:209` 注释已同步。
- [ ] runtime `dependencies` 仍 `{}`。

## 与其它卡的关系

- **波1，先合**。T2 的取词入口、T4 与 T1 都动 `config.ts`（T4 是 :89 独立行，冲突小）→ T1 先。
- 与 T2 争 `asking/{proposeAsk,revisitConflicts}.ts`（T1 改 `PHRASE_SYSTEM`、T2 改 `templateQuestion`）→ T1 先合，T2 跟。
- 与 T3 争 `jsonRepair.ts`（T1 改 `JSON_ONLY_NUDGE`、T3 改 `extractJsonObject`，不同区块）。

## 发现待办

（施工中若发现某条 prompt 本身的纪律表述有漏洞，记这里、别顺手改判定语义——那要单独立项。）
