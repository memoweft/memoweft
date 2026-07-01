# 安装与快速开始 · MemoWeft

> MemoWeft 是套在大模型 / Agent **外部**的“用户认知层”。它是一个被宿主（如 Hermes）`import` 的**库**，自己不做聊天 / 角色 / UI。
> 本文带你：**装上 → 配好 env → 15 分钟看到它工作**（跑测试台 + 跑最小示例）。发布相关见 [PUBLISHING.md](PUBLISHING.md)。

---

## 0. 前置条件（必读）

| 要求 | 说明 |
| --- | --- |
| **Node ≥ 22.6** | 库直接跑 `.ts`、用 Node 内置 `node:sqlite`（作者实测 **v24**）。低于 22.6 会报 `node:sqlite` 找不到或跑不了 `.ts`。用 `node --version` 确认。 |
| **一个 OpenAI 兼容的模型端点** | 对话 + 写路径都靠它。云端（如小米 MiMo）或本地（Ollama / LM Studio）都行——只要是 `/chat/completions` 接口。 |
| **（可选）一个嵌入端点** | 用于语义召回。没有也能跑：召回自动降级为空，画像照写，只是回话不注入。作者用本地 Ollama `bge-m3`。 |
| **零运行时依赖** | MemoWeft 的 `dependencies` 是空的——存储 / HTTP / 向量全用 Node 内置。你不需要装 SQLite、也不需要装向量库。 |

> ℹ️ **隐私 / 用云端还是本地模型，是宿主 + 用户的选择，不是库替你定的。** MemoWeft 只保证“可切换 + 留好口”：模型口在 `.env`（`MEMOWEFT_LLM_*` / `MEMOWEFT_EMBED_*`），授权位在证据上（`allowCloudRead` 等）。想全本地就把端点指向本地模型即可。

---

## 1. 从 GitHub 装（当前形态：源码使用）

MemoWeft 目前**以源码形式**使用（尚未发布到 npm；发布流程见 [PUBLISHING.md](PUBLISHING.md)）。

```bash
git clone <仓库地址> memoweft
cd memoweft
npm install        # 只装 devDependencies（typescript + @types/node），无运行时依赖
```

装完先跑一遍护栏，确认环境 OK：

```bash
npm run typecheck   # 类型全绿
npm test            # 54 个测试全过（作者实测 54 passing）
npm run build       # 产出 dist/（编译后的 .js + .d.ts）
```

三条都绿 = 环境就绪。**这三条是本项目的硬护栏**：任何改动后都要重新跑绿（项目在转 git 前无法回滚，务必每步验证）。

> 将来发布到 npm 后，宿主开发者的用法会变成 `npm install memoweft` + `import ... from 'memoweft'`。在那之前，宿主可用 `npm install <本地路径>` 或 git submodule 引入本仓，`import` 走相对路径 `../memoweft/src/index.ts`（或 build 后的 `dist/index.js`）。

---

## 2. 配 `.env`

在**仓库根目录**建 `.env`（与 `package.json` 同级）。库启动时用 Node 内置 `process.loadEnvFile()` 自动读它。

> 💡 **省事做法**：可直接复制仓库根的 `.env.example` 成 `.env` 再改——模板已按 **必填 / 可选** 标好每一段（对话模型必填、写路径小模型与嵌入均可选），还带一个体验界面开关 `MEMOWEFT_EXPERIENCE_UI`。`.env.example` 只含占位符、可提交；`.env` 含密钥、已被 `.gitignore` 忽略。

### 2.1 env 命名：新名主推、旧名兼容

改名后**每个键都双认**：代码先读 `MEMOWEFT_*` 主名，读不到再回退旧名 `DLA_*`。所以：

- **新装的人**：一律用 `MEMOWEFT_*`（本文示例都用它）。
- **老用户**：现有只含 `DLA_*` 的 `.env` **零改动继续可用**，不用迁移。
- 两个前缀都没配 → 对话模型会在真调用时报错（提示里同时列两种前缀）。

### 2.2 最小 `.env`（只配对话模型，够跑起来）

```ini
# ── 对话大模型（必配）──────────────────────────────
MEMOWEFT_LLM_BASE_URL=https://your-endpoint/v1
MEMOWEFT_LLM_API_KEY=sk-xxxx
MEMOWEFT_LLM_MODEL=your-chat-model
```

### 2.3 完整 `.env`（对话 + 写路径小模型 + 嵌入召回）

```ini
# ── 对话大模型（chat）：质量优先 ─────────────────────
MEMOWEFT_LLM_BASE_URL=https://your-endpoint/v1
MEMOWEFT_LLM_API_KEY=sk-xxxx
MEMOWEFT_LLM_MODEL=your-chat-model

# ── 写路径小快模型（write）：可选，缺配则回退对话大模型 ──
# 写路径（整理事件 / 生成画像 / 归因）用小快模型更省时，不拖慢“更新画像”。
MEMOWEFT_WRITE_LLM_BASE_URL=https://your-endpoint/v1
MEMOWEFT_WRITE_LLM_API_KEY=sk-xxxx
MEMOWEFT_WRITE_LLM_MODEL=your-small-fast-model

# ── 嵌入器（embed）：可选，用于语义召回。缺配则召回降级为空 ──
# 作者用本地 Ollama bge-m3（多语言 / 中文好，1024 维）。
MEMOWEFT_EMBED_BASE_URL=http://localhost:11435/v1
MEMOWEFT_EMBED_API_KEY=ollama
MEMOWEFT_EMBED_MODEL=bge-m3
```

**九个键一览**（每个都 `MEMOWEFT_*` 主名 / `DLA_*` 兼容）：

| 用途 | 键（主名） | 兼容旧名 | 缺配时 |
| --- | --- | --- | --- |
| 对话 | `MEMOWEFT_LLM_{BASE_URL,API_KEY,MODEL}` | `DLA_LLM_*` | 真调用时报错 |
| 写路径小模型 | `MEMOWEFT_WRITE_LLM_{BASE_URL,API_KEY,MODEL}` | `DLA_WRITE_LLM_*` | 回退对话大模型（行为同旧） |
| 嵌入召回 | `MEMOWEFT_EMBED_{BASE_URL,API_KEY,MODEL}` | `DLA_EMBED_*` | 召回降级为空（画像照写） |

> ⚠️ **不要提交 `.env`**（含密钥）。仓库应有 `.gitignore` 忽略它——转 git 仓时务必先加。
> ⚠️ **本地 Ollama 嵌入端口**：作者环境 Ollama 跑在 `11435`（避开 codex 占用的 11434）。起服务：`OLLAMA_HOST=127.0.0.1:11435 ollama serve`。你的端口按自己环境改，与 `.env` 里的 `MEMOWEFT_EMBED_BASE_URL` 对齐即可。

---

## 3. 跑测试台，体验 MemoWeft（推荐先做这个）

测试台是个本地网页：**左边像正常聊天，右边是 MemoWeft 的“透视区”**——你能实时看到证据怎么落库、怎么整理成事件、怎么沉淀成画像、以及“该主动问什么”。这是理解这库最快的方式。

```bash
npm run testbench
# → 启动本地服务，打开 http://localhost:7888
```

它做了什么：
- 用**独立的** `testbench/testbench-evidence.db`，**不碰**你正式的 `./dla.db`。
- 配了 `MEMOWEFT_EMBED_*`（或兼容 `DLA_EMBED_*`）就用向量召回；没配就降级为空召回（照常聊天，只是不注入画像）。
- 每轮把全部“内幕”落盘到 `logs/run-*.jsonl`（逐轮记录 + 各步耗时），方便事后诊断。

面板里能做：聊天、看证据 / 事件 / 画像、手动“更新画像”、归因 / 主动询问、注入活动窗口观察（4-A）。

**灌样例数据（可选）**：`testbench/seed-dogfood.ps1` 可给测试台灌一批样例证据，省得从零聊起。

> 💡 **看不到画像更新？** 测试台是**攒批更新**的：攒够 5 条新对话、或空闲 30 分钟才后台自动 `updateProfile`（避免每聊一句就跑一次、太勤又费模型）。想立刻看效果，点面板上的“**立即更新画像**”按钮手动触发。

---

## 4. 跑最小代码示例（看懂 API 闭环）

仓库带了一个可运行的最小示例 [`examples/minimal.ts`](../examples/minimal.ts)，演示**写路径 → 读路径**一整圈：

```bash
node examples/minimal.ts
```

它做的事（全用真实导出 API）：

1. 建三层 store：`SqliteEvidenceStore` / `SqliteEventStore` / `SqliteCognitionStore`（都指向独立的 `./example.db`，不碰 `./dla.db`）。
2. 装配 `loadLLMPool()` 取对话 / 写路径模型 + `loadEmbedConfig()` 装嵌入器（缺配自动降级）。
3. 写一条“亲口证据” → 调 `updateProfile()`（一键写路径：整理事件 → 画像 → 归因 → 建索引）→ 打印生成的画像。
4. 用 `Conversation.handle()` 处理下一条消息 → 召回相关画像并注入回话。
5. 关库收尾。

> 这段真调你 `.env` 里配的模型，耗时取决于模型快慢（`updateProfile` 返回 `timings` 能看慢在哪步）。只配了对话模型也能跑——没配嵌入时召回为空，示例照样演示写路径。

### 4.1 核心 API 速览（宿主接入就用这几个）

```ts
import {
  SqliteEvidenceStore, SqliteEventStore, SqliteCognitionStore,
  VectorRetriever, NullRetriever, OpenAICompatEmbedder, loadEmbedConfig,
  loadLLMPool, updateProfile, Conversation, ingestObservations,
  MEMOWEFT_VERSION,
} from 'memoweft'; // 源码使用时改成相对路径 '../memoweft/src/index.ts'
```

| 你想做的事 | 用什么 |
| --- | --- |
| 存三层数据 | `SqliteEvidenceStore(dbPath?)` / `SqliteEventStore(dbPath?)` / `SqliteCognitionStore(dbPath?)`（默认 `./dla.db`） |
| 装模型 | `loadLLMPool()` → `.for('chat' \| 'write')`；嵌入 `loadEmbedConfig()` + `OpenAICompatEmbedder` |
| 写路径（一键沉淀画像） | `updateProfile(subjectId, { evidenceStore, eventStore, cognitionStore, retriever, llm })` |
| 读路径（回话召回注入） | `new Conversation({ store, retriever, cognitionStore, llm }).handle(msg, opts)` |
| 摄入行为观察（4-A） | `ingestObservations(subjectId, observations, { evidenceStore, hostId? })` |

> 完整导出清单见 [`STATE.md`](../STATE.md) 的“当前可用接口”段（那里逐个列了签名，不用读实现）。宿主负责什么、库负责什么见 `docs/integration.md`（若已生成）。

---

## 5. 命令速查

| 命令 | 作用 |
| --- | --- |
| `npm run typecheck` | 类型检查（tsc，不产出） |
| `npm test` | 跑 `tests/`（54 个，全过） |
| `npm run build` | 编译到 `dist/`（发布 / 交付用） |
| `npm run testbench` | 起测试台（http://localhost:7888） |
| `npm run experience` | 起体验界面（同 `testbench`，同端口 7888；`.env` 里 `MEMOWEFT_EXPERIENCE_UI=off` 则不起网页） |
| `node examples/minimal.ts` | 跑最小示例 |

---

## 6. 常见问题

**Q：跑 `npm test` 或 `node examples/minimal.ts` 报 `node:sqlite` 找不到 / 跑不了 .ts？**
A：Node 版本太低。升级到 **≥ 22.6**（作者实测 v24）。`node:sqlite` 与原生 `.ts` 执行都需要新版本 Node。

**Q：回话报“回话失败”/ LLM 请求失败？**
A：`.env` 里对话模型没配对。检查 `MEMOWEFT_LLM_BASE_URL/_API_KEY/_MODEL`（或旧名 `DLA_LLM_*`）三项齐全、端点是 OpenAI 兼容的 `/v1`。

**Q：画像更新很慢（几十秒）？**
A：是模型生成慢，不是代码 bug。配一个**写路径小快模型**（`MEMOWEFT_WRITE_LLM_*`）能明显提速；`updateProfile` 返回的 `timings` 能定位慢在哪步（多半是归因 `attributeMs`）。

**Q：召回不注入画像 / 回话没带上我说过的偏好？**
A：多半是没配嵌入器（`MEMOWEFT_EMBED_*`），召回降级为空。配上嵌入端点再试。另外召回有门槛：失效认知、或有效置信 < 80 的不注入（避免把淡了的情绪硬塞）。

**Q：会不会污染我已有的数据？**
A：不会。测试台用 `testbench/testbench-evidence.db`、示例用 `./example.db`，都和正式的 `./dla.db` 分开。默认库名 `./dla.db` 沿用未改（改名会脱离已有数据文件）。
