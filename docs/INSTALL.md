# 安装与快速开始 · MemoWeft

> MemoWeft 是套在大模型 / Agent **外部**的“用户认知层”。它是一个被宿主 `import` 的**库**，自己不做聊天 / 角色 / UI。
>
> 本文带你：**装上 → 配好 env → 15 分钟看到它工作**。默认推荐云端 OpenAI-compatible 端点快速跑通；本地 / 混合模型作为高级选项。

---

## 0. 前置条件

| 要求 | 说明 |
| --- | --- |
| **Node ≥ 24（开箱即用）或 Node 20/22 + `better-sqlite3`（后备）** | 存储底层是 SQLite。驱动**优先用内置 `node:sqlite`**、加载不到才回退可选的 `better-sqlite3`。Node ≥24 上 `node:sqlite` 转正稳定、零额外依赖；Node 20 上没有它、必须装 `better-sqlite3`（`npm i better-sqlite3`，见下方 §1.2）；Node 22 视你的版本 / flag 是否已提供 `node:sqlite` 而定，用不上时才需装 `better-sqlite3`。 |
| **一个 OpenAI-compatible 对话模型端点** | 默认推荐云端端点：最省事、最容易让开发者跑起来。只要兼容 `/chat/completions` 即可。 |
| **可选：写路径小快模型端点** | 用于 `distill → consolidate → attribute`，缺配会回退对话模型。 |
| **可选：嵌入端点** | 用于语义召回。缺配时召回降级为空，画像照写，只是不注入长期认知。 |
| **零运行时依赖** | runtime `dependencies` 为空，存储 / HTTP / 向量计算均用 Node 内置。`better-sqlite3` 只是**可选 peer 依赖**，Node ≥24 用户根本不需要装它。 |

> ⚙️ **`node:sqlite` 加载不到时，装可选驱动 `better-sqlite3` 兜底。** MemoWeft **优先用内置 `node:sqlite`**、加载不到才回退 `better-sqlite3`（原生模块，见 §1.2）。`node:sqlite` 到 Node 24 才转正稳定；Node 20 上没有它，**必须**装 `better-sqlite3`；Node 22 是否可用取决于你的 Node 版本 / flag——用不上时才需装 `better-sqlite3`（装了也只在 `node:sqlite` 加载不到时才会被选中，不会顶替已可用的内置驱动）。开发库本身（跑仓库里的 `.ts` 示例 / 测试台）另有门槛：Node 22 需 22.18+ 才默认支持原生剥 `.ts` 类型，Node 20 没有此能力——想跑 `.ts` 请用 Node ≥24；只是**当库用**（`import 'memoweft'` 吃编译后的 `.js`）则装好可用驱动即可。

> ℹ️ **云端优先，不是无脑上云。** MemoWeft 推荐开发者用云端端点快速开始，但每条证据仍有 `allowCloudRead` 等授权位。宿主负责隐私政策和同意 UI；MemoWeft 负责保留模型切换和过滤钩子。完整模式见 [`deployment.md`](./deployment.md)。

---

## 1. 安装

### 1.1 当库用（`npm install`）

MemoWeft 已发布到 npm。宿主开发者直接装：

```bash
npm install memoweft
```

然后 `import { createMemoWeftCore } from 'memoweft'`（用法见 README「当库用」/ [`integration.md`](./integration.md)）。装出来**零 runtime 依赖**（Node ≥24 用内置 `node:sqlite`）。TypeScript 项目按常规装 `@types/node` 即可。

### 1.2 Node 20/22：装可选驱动 `better-sqlite3`

Node ≥24 到此为止就够了。**Node 20/22** 上内置 `node:sqlite` 不可用，需额外装可选驱动：

```bash
npm i better-sqlite3
```

装上后 MemoWeft 会在开库时自动选它当底层，其余用法完全一致。几点说明：

- `better-sqlite3` 是**原生模块**，一般走 prebuilt 二进制、秒装；若你的平台 / Node 版本没有匹配的 prebuilt，会回落到 `node-gyp` 现编译（需要 Python + C++ 工具链）。所以**不承诺一定装得上**——装不上时最稳的出路是把 Node 升到 ≥24（内置驱动、零依赖）。
- 没装它、又不在 Node ≥24 上时，`import 'memoweft'` 会直接报一句人话错误，列出两条出路（升 Node ≥24 / 装 `better-sqlite3`）。

### 1.3 从源码跑（开发库本身 / 跑参考宿主与测试台）

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm install        # 只装 devDependencies，无运行时依赖
npm run typecheck && npm test && npm run build   # 三绿 = 环境就绪
```

参考宿主 `npm start -w @memoweft/host`（:7788）、测试台 `npm run testbench`（:7888）都从源码跑。发布流程见 [`PUBLISHING.md`](./PUBLISHING.md)。

---

## 2. 配 `.env`

在仓库根目录创建 `.env`，与 `package.json` 同级。也可以复制 `.env.example`：

```bash
cp .env.example .env
```

### 2.1 env 命名：新名主推、旧名兼容

代码先读 `MEMOWEFT_*` 主名，读不到再回退旧名 `DLA_*`：

- **新装的人**：一律用 `MEMOWEFT_*`。
- **老用户**：已有 `DLA_*` 的 `.env` 可继续用。
- 两个前缀都没配：真调用模型时会报错。

---

## 3. 推荐配置：Cloud-first

这是最推荐的新手 / 开发者接入方式：全部使用云端 OpenAI-compatible endpoint，先把链路跑通。

```ini
# ── 对话模型（必填）：读路径 / 回话质量优先 ────────────────
MEMOWEFT_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_LLM_API_KEY=sk-xxxx
MEMOWEFT_LLM_MODEL=your-chat-model

# ── 写路径模型（可选）：整理事件 / 画像 / 归因 ─────────────
# 缺配会回退对话模型；建议用小快模型，更新画像更省时。
MEMOWEFT_WRITE_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_WRITE_LLM_API_KEY=sk-xxxx
MEMOWEFT_WRITE_LLM_MODEL=your-small-fast-model

# ── 嵌入器（可选）：语义召回 ─────────────────────────────
# 缺配则召回降级为空，不影响证据和画像写入。
MEMOWEFT_EMBED_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_EMBED_API_KEY=sk-xxxx
MEMOWEFT_EMBED_MODEL=your-embedding-model
```

这种模式最省事，适合：

- 快速体验测试台；
- 让其他开发者最小成本接入；
- 先验证 `聊天 → 记住 → 召回 → 注入回话` 主链路。

---

## 4. 隐私基线：Cloud-guarded

真实应用建议在 Cloud-first 的基础上进入 Cloud-guarded：模型仍可用云端，但 evidence 级别控制哪些内容能进云端 prompt。

建议默认：

| 证据来源 | 默认云端策略 | 理由 |
| --- | --- | --- |
| 用户聊天 / 明确输入的记忆 | 宿主可默认 `allowCloudRead=true` | 用户本来就在和 AI 宿主交互。 |
| 用户手动批准的观察 | 宿主决定 | 需要清晰同意开关。 |
| 桌面窗口 / 设备观察 | 默认 `allowCloudRead=false` | 可能包含应用名、窗口标题、文件路径。 |
| 屏幕 OCR / 剪贴板 / 文件内容 | 默认 `allowCloudRead=false` | 高风险隐私内容。 |
| 睡眠 / 心率 / 健康数据 | 默认 `allowCloudRead=false` | 敏感个人数据。 |

> MemoWeft 只提供授权位和过滤能力；宿主必须把用户同意、撤销、查看和纠正做成明确体验。

---

## 5. 高级配置：Hybrid / Local-sensitive

如果宿主更重视隐私，可以混合路由：

```ini
# 对话仍可用云端
MEMOWEFT_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_LLM_API_KEY=sk-xxxx
MEMOWEFT_LLM_MODEL=your-chat-model

# 写路径也可以改成本地 OpenAI-compatible endpoint
MEMOWEFT_WRITE_LLM_BASE_URL=http://localhost:1234/v1
MEMOWEFT_WRITE_LLM_API_KEY=local
MEMOWEFT_WRITE_LLM_MODEL=your-local-model

# 嵌入器可用本地 Ollama / LM Studio / 其他兼容服务
MEMOWEFT_EMBED_BASE_URL=http://localhost:11435/v1
MEMOWEFT_EMBED_API_KEY=ollama
MEMOWEFT_EMBED_MODEL=bge-m3
```

本地 / 混合适合：桌面助手、敏感行为观察、长期个人数据。代价是安装和排错成本更高，不建议作为默认新手路径。

---

## 6. 九个 env 键一览

| 用途 | 主名 | 兼容旧名 | 缺配时 |
| --- | --- | --- | --- |
| 对话模型 | `MEMOWEFT_LLM_{BASE_URL,API_KEY,MODEL}` | `DLA_LLM_*` | 真调用时报错 |
| 写路径模型 | `MEMOWEFT_WRITE_LLM_{BASE_URL,API_KEY,MODEL}` | `DLA_WRITE_LLM_*` | 回退对话模型 |
| 嵌入召回 | `MEMOWEFT_EMBED_{BASE_URL,API_KEY,MODEL}` | `DLA_EMBED_*` | 召回降级为空 |

> ⚠️ 不要提交 `.env`。它包含密钥，应被 `.gitignore` 忽略。

---

## 7. 跑测试台

测试台是本地网页：左边像正常聊天，右边是 MemoWeft 的“透视区”。你可以看到证据如何落库、如何整理成事件、如何沉淀成画像，以及系统想主动问什么。

```bash
npm run testbench
# 打开 http://localhost:7888
```

测试台特性：

- 使用独立的 `testbench/testbench-evidence.db`，不污染正式数据库。
- 配了 `MEMOWEFT_EMBED_*` 就用向量召回；没配则召回为空。
- 每轮内幕落盘到 `logs/run-*.jsonl`，方便诊断。
- 支持聊天、看证据 / 事件 / 画像、手动更新画像、归因、主动询问、注入活动窗口观察。

> 看不到画像更新？测试台默认攒批：攒够 5 条新对话或空闲 30 分钟才自动 `updateProfile`。想立刻看效果，点“立即更新画像”。

---

## 8. 跑最小代码示例

仓库带了一个可运行最小示例：[`examples/minimal.ts`](../examples/minimal.ts)。

```bash
node examples/minimal.ts
```

它演示：

1. 创建 `SqliteEvidenceStore` / `SqliteEventStore` / `SqliteCognitionStore`。
2. 使用 `loadLLMPool()` 读取对话 / 写路径模型。
3. 使用 `loadEmbedConfig()` 装配嵌入器，缺配则降级。
4. 写入一条亲口证据。
5. 调用 `updateProfile()` 生成画像。
6. 调用 `Conversation.handle()` 召回相关画像并注入回话。

---

## 9. 核心 API 速览

```ts
import {
  SqliteEvidenceStore,
  SqliteEventStore,
  SqliteCognitionStore,
  VectorRetriever,
  NullRetriever,
  OpenAICompatEmbedder,
  loadEmbedConfig,
  loadLLMPool,
  updateProfile,
  Conversation,
  ingestObservations,
  MEMOWEFT_VERSION,
} from 'memoweft';
```

| 你想做的事 | 用什么 |
| --- | --- |
| 存三层数据 | `SqliteEvidenceStore` / `SqliteEventStore` / `SqliteCognitionStore` |
| 装模型 | `loadLLMPool()` → `.for('chat' \| 'write')` |
| 装嵌入器 | `loadEmbedConfig()` + `OpenAICompatEmbedder` |
| 写路径 | `updateProfile(subjectId, deps)` |
| 读路径 | `new Conversation(deps).handle(msg, opts)` |
| 摄入行为观察 | `ingestObservations(subjectId, observations, deps)` |

完整导出清单见 [`src/index.ts`](../src/index.ts) 和 [`docs/integration.md`](./integration.md)。

---

## 10. 命令速查

| 命令 | 作用 |
| --- | --- |
| `npm run typecheck` | 类型检查 |
| `npm test` | 跑测试 |
| `npm run build` | 产出 `dist/` |
| `npm run testbench` | 启动测试台 |
| `npm run experience` | 测试台别名 |

---

## 11. 常见问题

### 只配云端模型可以吗？

可以，而且这是推荐默认路径。先跑起来，再按数据敏感度决定哪些 evidence 不允许上云。

### 不配嵌入器可以吗？

可以。召回会降级为空，但证据仍会存，画像仍可写。只是回话里不会注入长期认知。

### observed 行为数据会默认上云吗？

不应该。桌面 / 设备 / 健康 / 屏幕类观察应默认 `allowCloudRead=false`，除非宿主明确征得用户同意。

### MemoWeft 替我处理隐私合规吗？

不。MemoWeft 是库，只提供授权位和过滤机制。宿主负责隐私政策、用户同意 UI、数据导出 / 删除等最终体验。
