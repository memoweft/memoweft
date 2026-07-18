# 安装与快速开始 · MemoWeft

[English](./INSTALL.md) | **简体中文**

> MemoWeft 是套在大模型 / Agent **外部**的“用户认知层”。它是一个被宿主 `import` 的**库**，自己不做聊天 / 角色 / UI。
>
> 本文带你：**装上 → 配好 env → 15 分钟看到它工作**。默认推荐云端 OpenAI-compatible 端点快速跑通；本地 / 混合模型作为高级选项。

---

## 0. 前置条件

| 要求                                                    | 说明                                                                                                                                          |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| **Node ≥ 24（推荐），或 Node 20/22 + `better-sqlite3`** | 存储底层是 SQLite。MemoWeft 优先使用内置 `node:sqlite`，不可用时回退可选 peer 驱动。为保证 Node 20/22 环境一致，执行 `npm i better-sqlite3`。 |
| **一个 OpenAI-compatible 对话模型端点**                 | 默认推荐云端端点：最省事、最容易让开发者跑起来。只要兼容 `/chat/completions` 即可。                                                           |
| **可选：写路径小快模型端点**                            | 用于 `distill → consolidate → attribute`，缺配会回退对话模型。                                                                                |
| **可选：嵌入端点**                                      | 用于语义召回。缺配时召回降级为关键词检索（FTS5），画像照写；丢的只是*语义*召回、不是召回本身。                                                |
| **小依赖边界**                                          | Node 24 上 runtime `dependencies` 为空；Node 20/22 使用可选的 `better-sqlite3` peer 驱动。                                                    |

> ⚙️ `better-sqlite3` 是原生模块，可能需要对应平台的预编译包或本地编译工具链。Node 24 使用稳定的内置 `node:sqlite`，是最省事的路径。运行本仓库的 `.ts` 示例也需要 Node 24；包使用者加载的是编译后的 JavaScript。

> ℹ️ **云端优先，不是无脑上云。** MemoWeft 推荐开发者用云端端点以降低接入成本，但每条 evidence 记录都有 `allowCloudRead` 等授权位。MemoWeft 在选择写路径 prompt 的记录时使用这些标记；它们不是访问控制或加密。隐私策略、同意 UI、存储安全和其他数据流仍归宿主负责。完整模式见 [`deployment.md`](./deployment.md)。

---

## 1. 安装

### 1.1 当库用（`npm install`）

MemoWeft 已发布到 npm。宿主开发者直接装：

```bash
npm install memoweft
```

然后 `import { createMemoWeftCore } from 'memoweft'`（用法见 README 与 [`integration.zh-CN.md`](./integration.zh-CN.md)）。Node 24 使用内置 `node:sqlite`；Node 20/22 还需安装下方的可选 peer 驱动。

### 1.2 Node 20/22：装可选驱动 `better-sqlite3`

Node ≥24 到此为止就够了。**Node 20/22** 上内置 `node:sqlite` 不可用，需额外装可选驱动：

```bash
npm i better-sqlite3
```

装上后 MemoWeft 会在开库时自动选它当底层，其余用法完全一致。几点说明：

- `better-sqlite3` 是**原生模块**，常会使用 prebuilt 二进制；若你的平台 / Node 版本没有匹配的 prebuilt，会回落到 `node-gyp` 现编译（需要 Python + C++ 工具链）。安装失败时可把 Node 升到 ≥24，改用内置驱动并避开这个可选依赖。
- 没装它、又不在 Node ≥24 上时，`import 'memoweft'` 会直接报一句人话错误，列出两条出路（升 Node ≥24 / 装 `better-sqlite3`）。

### 1.3 从源码运行（开发库本身 / 参考宿主与可选诊断台）

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run typecheck && npm test && npm run build   # 三绿 = 环境就绪
```

参考宿主 `npm start -w @memoweft/host`（:7788）、可选诊断台 `npm run testbench`（:7888）都从源码跑。发布流程见 [`PUBLISHING.md`](./PUBLISHING.md)。

---

## 2. 配 `.env`

在仓库根目录创建 `.env`，与 `package.json` 同级。也可以复制 `.env.example`：

```bash
cp .env.example .env
```

### 2.1 env 命名：新名主推、旧名兼容

代码先读 `MEMOWEFT_*` 主名，读不到再回退旧名 `DLA_*`：

- **新装的人**：使用 `MEMOWEFT_*`。
- **老用户**：已有 `DLA_*` 的 `.env` 可继续用。
- 两个前缀都没配：真调用模型时会报错。

---

## 3. 推荐配置：Cloud-first

这是最推荐的新手 / 开发者接入方式：全部使用云端 OpenAI-compatible endpoint，先把链路跑通。

```ini
# ── 对话模型（必填）：读路径 / 对话质量优先 ────────────────
MEMOWEFT_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_LLM_API_KEY=sk-xxxx
MEMOWEFT_LLM_MODEL=your-chat-model

# ── 写路径模型（可选）：整理事件 / 画像 / 归因 ─────────────
# 缺配会回退对话模型；建议用小快模型，更新画像更省时。
MEMOWEFT_WRITE_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_WRITE_LLM_API_KEY=sk-xxxx
MEMOWEFT_WRITE_LLM_MODEL=your-small-fast-model

# ── 嵌入器（可选）：语义召回 ─────────────────────────────
# 缺配则召回降级为关键词检索（FTS5），不影响证据和画像写入。
MEMOWEFT_EMBED_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_EMBED_API_KEY=sk-xxxx
MEMOWEFT_EMBED_MODEL=your-embedding-model
```

这种模式最省事，适合：

- 快速体验诊断台；
- 让其他开发者最小成本接入；
- 先验证 `聊天 → 记住 → 召回 → 注入上下文` 主链路。

---

## 4. 隐私基线：Cloud-guarded

真实应用建议在 Cloud-first 的基础上进入 Cloud-guarded：模型仍可用云端，但 evidence 级别控制哪些内容能进云端 prompt。

建议默认：

| 证据来源                     | 默认云端策略                     | 理由                                 |
| ---------------------------- | -------------------------------- | ------------------------------------ |
| 用户聊天 / 明确输入的记忆    | 宿主可默认 `allowCloudRead=true` | 用户本来就在和 AI 宿主交互。         |
| 用户手动批准的观察           | 宿主决定                         | 需要清晰同意开关。                   |
| 桌面窗口 / 设备观察          | 默认 `allowCloudRead=false`      | 可能包含应用名、窗口标题、文件路径。 |
| 屏幕 OCR / 剪贴板 / 文件内容 | 默认 `allowCloudRead=false`      | 高风险隐私内容。                     |
| 睡眠 / 心率 / 健康数据       | 默认 `allowCloudRead=false`      | 敏感个人数据。                       |

> MemoWeft 只提供授权位和过滤能力；宿主必须把用户同意、撤销、查看和纠正做成明确体验。

> ⚠️ **数据当前明文落盘，磁盘加密属宿主 / 系统责任。** MemoWeft 把三层记忆存进标准 SQLite 库（`./dla.db` 之类），库文件本身**不加密**。如果宿主要防「拿到磁盘 = 拿到记忆」，请依靠宿主 / 操作系统层的磁盘加密（如 BitLocker、FileVault、LUKS）。`allowCloudRead` 管的是「哪些内容能进云端 prompt」，不等于本地静态加密。

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
MEMOWEFT_EMBED_BASE_URL=http://localhost:11434/v1
MEMOWEFT_EMBED_API_KEY=ollama
MEMOWEFT_EMBED_MODEL=bge-m3
```

本地 / 混合适合：桌面助手、敏感行为观察、长期个人数据。代价是安装和排错成本更高，不建议作为默认新手路径。

---

## 6. 九个 env 键一览

| 用途       | 主名                                          | 兼容旧名          | 缺配时                   |
| ---------- | --------------------------------------------- | ----------------- | ------------------------ |
| 对话模型   | `MEMOWEFT_LLM_{BASE_URL,API_KEY,MODEL}`       | `DLA_LLM_*`       | 真调用时报错             |
| 写路径模型 | `MEMOWEFT_WRITE_LLM_{BASE_URL,API_KEY,MODEL}` | `DLA_WRITE_LLM_*` | 回退对话模型             |
| 嵌入召回   | `MEMOWEFT_EMBED_{BASE_URL,API_KEY,MODEL}`     | `DLA_EMBED_*`     | 召回降级为关键词（FTS5） |

> ⚠️ 不要提交 `.env`。它包含密钥，应被 `.gitignore` 忽略。

---

## 7. 运行可选诊断台

诊断台是本地网页：左边是正常聊天，右边是 MemoWeft 的诊断视图。你可以观察证据如何落库、如何整理成事件、如何沉淀成画像，以及系统想主动问什么。它是可选的本地诊断工具，不是部署运行所必需的组件。

```bash
npm run testbench
# 打开 http://localhost:7888
```

诊断台特性：

- 使用独立的 `testbench/testbench-evidence.db`，而非安装示例中使用的数据库路径。
- 配了 `MEMOWEFT_EMBED_*` 就用向量召回；诊断台在没配置时明确使用空召回。公共 Core 工厂本身会回退到 FTS5 关键词召回。
- 每轮内幕落盘到 `logs/run-*.jsonl`，方便诊断。
- 支持聊天、看证据 / 事件 / 画像、手动更新画像、归因、主动询问、注入活动窗口观察。

> 看不到画像更新？诊断台服务器有自己的调度器：攒够 5 条新对话或空闲 30 分钟后会排队运行 `updateProfile`。这是诊断台行为，不是 Core 的自动调度；可点“立即更新画像”请求一次运行。

---

## 8. 跑最小代码示例

仓库带了一个可运行最小示例：[`examples/minimal.ts`](../examples/minimal.ts)。示例以**包名**入口（`import { createMemoWeftCore } from 'memoweft'`），所以先 `npm run build` 出 `dist/`，再跑：

```bash
npm run build
node examples/minimal.ts
```

它演示（全走统一入口 `createMemoWeftCore`）：

1. 一行装配 `createMemoWeftCore({ dbPath })`：三层 store + 召回器 + 模型池一次到位，环境配置从 `.env` 读取。未配置模型时仍可构造用于存储和管理；调用需要该模型的操作时会报告错误。
2. `core.ingestUserMessage()` 写入一条用户亲口证据。
3. `core.updateProfile()` 跑完整写路径（distill → consolidate → attribute → 重建索引）生成画像。
4. `core.handleConversationTurn()` 处理下一轮消息：召回相关画像并注入上下文。
5. `core.close()` 收口连接。

还有两个进阶示例可参考：[`examples/memory-management.ts`](../examples/memory-management.ts)（受控记忆管理）、[`examples/portable-bundle.ts`](../examples/portable-bundle.ts)（便携记忆包导入 / 导出）。

---

## 9. 核心 API 速览

推荐经统一入口 `createMemoWeftCore` 调 Core，而非散装 `new Sqlite*Store` 手工拼底层：

```ts
import { createMemoWeftCore, MEMOWEFT_VERSION } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });
core.close();
```

| 你想做的事                                | 用什么                                                     |
| ----------------------------------------- | ---------------------------------------------------------- |
| 一行装配 Core（三层 store + 召回 + 模型） | `createMemoWeftCore({ dbPath })`                           |
| 自检能否聊天 / 语义召回                   | `core.health()` → `{ llmReady, embedReady }`               |
| 写入用户亲口证据                          | `core.ingestUserMessage({ content, subjectId?, hostId? })` |
| 摄入行为观察                              | `core.ingestObservation({ observations })`                 |
| 写路径（更新画像）                        | `core.updateProfile({ subjectId? })`                       |
| 读路径（处理一轮对话）                    | `core.handleConversationTurn({ message, subjectId? })`     |
| 召回相关认知                              | `core.recall({ query, subjectId? })`                       |
| 受控记忆管理                              | `core.memory.*`                                            |
| 便携记忆包                                | `core.portable.*`                                          |

完整导出清单见 [`src/index.ts`](../src/index.ts) 和 [`docs/integration.zh-CN.md`](./integration.zh-CN.md)。

---

## 10. 命令速查

| 命令                 | 作用           |
| -------------------- | -------------- |
| `npm run typecheck`  | 类型检查       |
| `npm test`           | 跑测试         |
| `npm run build`      | 产出 `dist/`   |
| `npm run testbench`  | 启动可选诊断台 |
| `npm run experience` | 诊断台别名     |

---

## 11. 常见问题

### 只配云端模型可以吗？

可以，而且这是推荐默认路径。先跑起来，再按数据敏感度决定哪些 evidence 不允许上云。

### 不配嵌入器可以吗？

可以。召回会降级为关键词检索（FTS5），证据仍会存、画像仍可写。丢的是语义召回、不是召回本身。

### observed 行为数据会默认上云吗？

不应该。桌面 / 设备 / 健康 / 屏幕类观察应默认 `allowCloudRead=false`，除非宿主明确征得用户同意。

### 记忆库文件加密吗？

不。当前明文落盘：三层记忆存进标准 SQLite 库、库文件不加密。磁盘加密属宿主 / 系统责任。

### MemoWeft 替我处理隐私合规吗？

不。MemoWeft 是库，只提供授权位和过滤机制。宿主负责隐私政策、用户同意 UI、数据导出 / 删除等最终体验。
