# Contributing to MemoWeft

这份文件讲**每次改代码都要守的硬规矩**。人和 AI 都适用。

刚接手先读仓库根的 [`AGENTS.md`](AGENTS.md)（极简开工说明）和 [`CURRENT.md`](CURRENT.md)（现在在做什么）。`docs/internal/` 是可选的历史背景，不是必读。

---

## 一句话规矩

> 任何代码改动，交付前 **`npm run typecheck && npm test && npm run build` 三个必须全绿**。跑不过就没完成，不许提。

这不是建议，是门槛。CI（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）会在 PR 上跑同样三步，红的合不进来。

---

## 环境要求

- **Node ≥ 24**。不是随便写的：代码直接 `import 'node:sqlite'`（`DatabaseSync`），这个模块到 Node 24 才转正；测试和构建也直接跑 `.ts` 文件，靠 Node 24 原生解析 TypeScript。22/23 上这些一般能跑、但只打实验警告、不保证稳定，锁 24 是为稳定。
- **零 runtime 依赖**。装依赖只会装 `typescript` 和 `@types/node` 两个 devDependency。
- 想跑测试台 / 真实写路径，需要在 `.env` 配模型与嵌入器（见下方「配置」）。**但单元测试不需要任何 .env**——测试用假 LLM，纯离线，以 `npm test` 各 workspace 实际输出为准、`fail` 必须为 0。

```bash
npm ci            # 或 npm install
npm run typecheck # 类型
npm test          # Core 单元测试（离线，以实际输出为准、fail 必须为 0）
npm run build     # 出 dist/
```

Host 与采集插件各有独立测试：`npm test -w @memoweft/host`、`npm test -w @memoweft/collector-active-window`——同样以各 workspace 实际输出为准、`fail` 必须为 0。

---

## 三绿护栏（提交前必跑）

| 命令 | 干什么 | 绿的标准 |
| --- | --- | --- |
| `npm run typecheck` | `tsc` 全量类型检查（`src` + `tests`） | 无报错 |
| `npm test` | `node --test tests/**/*.test.ts`，纯离线 | 以实际输出为准，`fail` 必须为 0（pass 数字随测试增减） |
| `npm run build` | `tsc` 出 `dist/`（含 `.d.ts`） | 无报错、`dist/` 更新 |

三条要**按顺序都过**才算完成。别只跑 typecheck 就交。

---

## 分支与提交约定

- **别直接在默认分支（`main`）上改。** 开分支：
  - `feat/<简述>` 新功能
  - `fix/<简述>` 修 bug
  - `docs/<简述>` 只改文档
  - `chore/<简述>` 杂项（构建、配置、改名）
- **小步提交，一个提交一件事。** 提交信息写清楚「改了什么 + 为什么」，别写 "update"、"fix" 这种没信息量的。
- **禁止 `git commit --no-verify`、禁止跳过测试。** 护栏是给整个项目兜底的，绕过等于把风险塞给下一个接手的人。
- PR 正文至少说清 **改了什么 / 为什么 / 怎么验的（贴三绿结果）**。

---

## 依赖最小化（默认拒绝新依赖）

这是刻在 `package.json` 里的原则（见地图 cell 11）：

- 存储用 `node:sqlite`、HTTP 用 `node:http`、日志/文件用 `node:fs`——**能用 Node 内置就绝不加包**。
- 想加任何新依赖（包括 dev 依赖），先在 issue 里说清**为什么内置的搞不定**，由作者拍板。默认答案是「不加」。
- runtime `dependencies` 目标永远是空的。宿主 `npm install memoweft` 时不该被拖进一堆传递依赖。

---

## 认知层是核心，别顺手动

品牌名、文档、注释、构建配置——这些随便改，改错了三绿能兜住。但下面这些属于**核心机制**，动它们之前必须先在 issue 里摊开权衡、由作者（PM）确认，不许「顺手优化」：

- 三层数据模型（evidence → event → cognition）
- 认知纪律：记≠信（LLM 推的先当低置信候选）、禁止系统自证（助手输出/用户沉默不算证据）、冲突先暴露不自动消解、把握度 MemoWeft 自算不听 LLM 自报、分型过期（情绪快忘/明确偏好不忘）
- 置信度算法、衰减半衰期、读写解耦逻辑
- 公共 API 签名（`src/index.ts` 导出的东西）——宿主可能已经在用，破坏性改名要保留 deprecated 别名（例如 `DLA_VERSION` / `DlaConfig` 就是这么留的）

判断标准很简单：**拿不准这算不算「核心」，就当它是，先问。**

---

## 文档同步（改完必做）

代码和文档一起动，别只绿了代码：

- 改动了**当前主线的进展 / 边界** → 更新 [`CURRENT.md`](CURRENT.md)。
- 有值得记的**历史 / 决策** → 写进提交说明；对外里程碑补进 [`CHANGELOG.md`](CHANGELOG.md)。
- 改了**对外能力 / 用法** → 同步对外文档（`README`、`docs/` 下的 architecture / integration 等）。

「代码绿了但文档没跟上」= 没做完。

---

## 环境变量 / 配置

- 改名后**双认前缀**：代码读每个 env 键都先读 `MEMOWEFT_*` 主名、读不到再回退旧名 `DLA_*`。文档一律写 `MEMOWEFT_*`，并注明 `DLA_*` 仍向后兼容。
- **别碰 `.env`**（含用户真实密钥），也别删 / 改 `DLA_*` 旧键——只新增 `MEMOWEFT_*`，让代码同时认两套。
- 涉及的键：`MEMOWEFT_LLM_*`、`MEMOWEFT_WRITE_LLM_*`、`MEMOWEFT_EMBED_*`（各自兼容对应 `DLA_*`）。
- 默认 SQLite 文件名 `./dla.db` 不改（改了会脱离根目录已有数据文件）；物理目录名 `DLA_rebuild` 不改。

---

## 提交前自查清单

- [ ] `npm run typecheck` 绿
- [ ] `npm test` 绿（`fail 0`）
- [ ] `npm run build` 绿，`dist/` 是新的
- [ ] 没加新依赖（或已在 issue 里获批）
- [ ] 没擅自动核心机制 / 破坏公共 API
- [ ] 相关文档已同步（CURRENT.md / 对外 docs / 里程碑进 CHANGELOG）
- [ ] 没碰 `.env`、没删 `DLA_*` 旧键、没改物理目录名与 `./dla.db`
- [ ] PR 正文写清了「改了什么 / 为什么 / 怎么验的（贴三绿）」

License：MIT（见仓库根 [`LICENSE`](LICENSE)）。提交贡献即表示同意以 MIT 许可你的改动。
