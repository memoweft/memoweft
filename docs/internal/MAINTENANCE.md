# MemoWeft · AI 维护策略

这份文档回答一个问题：**MemoWeft 主要交给 AI 维护，那一个任务是怎么从「有人提出」走到「合并进主干」的？**

它不重复 [`CONTRIBUTING.md`](../../CONTRIBUTING.md) 里的硬规矩（三绿护栏、分支约定、依赖最小化）——那些是每次改动都要守的底线。这里讲的是**流程和分工**：谁拍板、AI 做什么、人做什么、怎么发版。

---

## 为什么这个项目能被 AI 维护

关键在**分层文档 + 固定工作流**，让 AI 冷启动时不用通读全仓就能对齐：

| 文件 | 是什么 | AI 什么时候读 |
| --- | --- | --- |
| [`AGENTS.md`](../../AGENTS.md) | 极简开工说明（这是什么 + 只读哪些 + 几条硬规矩） | **接手先读**，每次都读 |
| [`README.md`](../../README.md) | 项目是什么、怎么用 | **接手先读**，每次都读 |
| [`CURRENT.md`](../../CURRENT.md) | 当前主线 + 允许做 / 不做 / 验收 | **接手先读**，每次都读 |
| [`STATE.md`](STATE.md) / [`项目地图.md`](项目地图.md) | 内部历史存档 + 设计 master（17 格） | **仅背景**：查旧接口 / 旧决策时才翻，别默认通读 |
| git 提交 / [`CHANGELOG.md`](../../CHANGELOG.md) | 历史与里程碑 | 追溯"当初为何"时看 |

对外读者另有 `README` 和 `docs/` 下的 architecture / integration / quickstart。内部设计以地图为准。

**一句话原则贯穿始终**：**方向和价值判断归人（作者/PM），执行归 AI。** AI 只摊开选项与权衡，不替人拍板。碰到地图没覆盖的、或要改既有方向/决策/数据结构 → **停下来问人**，绝不擅自决定。

---

## 核心流程：一个任务的一生

```
① issue 分诊     人或 AI 把需求写成 issue（背景 + 验收 + 涉及文件）
      ↓
② AI 出方案      AI 读 AGENTS+README+CURRENT（背景才查 STATE/地图），产「方案 + 影响面清单」，碰决策就停下问
      ↓
③ 人确认         作者(PM)审方案：方向对不对、要不要动核心、权衡能不能接受 → 确认 / 打回
      ↓（确认后才动代码）
④ AI 实现        开分支，按方案小步写，守认知层规则 + 依赖取向 + 部件可替换
      ↓
⑤ 测试（三绿）   npm run typecheck && npm test && npm run build 全绿；自查对照地图规则
      ↓
⑥ docs-sync      改写 CURRENT.md + 历史/决策记进提交说明 +（决策变了才）改地图 cell + 同步对外 docs
      ↓
⑦ PR → 合并      PR 正文写清 改了什么/为什么/怎么验的（贴三绿）；CI 复跑三绿；人 review → 合
```

这套 ①–⑦ 流程即本文件对维护流程的展开——精简后的 `AGENTS.md` 只留「开工三读 + 几条硬规矩」，工作流细节落在这里。下面逐步拆。

### ① issue 分诊

每个任务从一个 issue 开始。好的 issue 让 AI 能**冷启动**——它没有上次对话的记忆，issue 必须自包含：

- **背景**：要解决什么问题 / 想加什么能力，为什么。
- **验收标准**：怎么算做完（尽量可验证，比如「新增 X 接口，测试台 Y 面板能看到 Z」）。
- **涉及文件 / 范围**：已知会碰哪些文件，或至少指个入口。

分诊时先判类型：
- **纯品牌/文档/注释** → 低风险，AI 可直接进 ②，方案可以很轻。
- **改接口 / 加功能** → 中风险，方案要列影响面 + 是否碰公共 API。
- **动核心机制 / 数据结构 / 方向** → 高风险，**方案阶段必须显式标红「这动了核心，需作者拍板」**，不确认不动手。

`.github/ISSUE_TEMPLATE`（已建）固定这套结构，降低 AI 冷启动成本。

### ② AI 出方案（不写码）

AI 先 **读 `AGENTS.md` + `README.md` + `CURRENT.md`**（`STATE.md` / 项目地图仅在查旧接口、旧决策时翻，别默认通读），然后产出：

1. **方案**：打算怎么做，分几小步。
2. **影响面清单**：会碰哪些文件、是否触及公共 API / 核心机制 / env 读取 / 数据结构、有没有破坏性。
3. **权衡与前提**：如果有取舍，摊开讲，别藏。
4. **卡点**：碰到地图没覆盖的、或需要改既有方向的，**在这里停下问**，不带进实现。

方案阶段**不碰代码**。这是「先出方案 → 人确认 → 再写码」铁律的前半段。

### ③ 人确认

作者（PM）审方案，重点看：方向对不对、要不要动核心、权衡能不能接受。确认了 AI 才进实现；打回就回 ② 改方案。**低风险任务（纯文档/注释）作者可以一次性授权批量放行**，不必逐条确认——但风险等级由方案里 AI 自己标清楚，标错了是 AI 的问题。

### ④ AI 实现

- **开分支**（`feat/` `fix/` `docs/` `chore/`，见 CONTRIBUTING）。
- **小步写**，别闷头写一大块；每个小步能独立验。
- 守三条取向：**认知层规则**（记≠信 / 禁自证 / 冲突暴露 / 把握度自算 / 分型过期）、**依赖最小化**、**部件可替换**（Retriever/Embedder/LLMPool 是接口，别硬编死）。
- 破坏公共 API 时保留 deprecated 别名（参照 `DLA_VERSION` / `DlaConfig` 的做法）。

### ⑤ 测试（三绿）

`npm run typecheck && npm test && npm run build` 全绿才算通过。单元测试离线（用假 LLM），不需要 `.env`。跑不过就回 ④ 修，别提。自查环节对照地图规则查 MemoWeft 特有坑（比如有没有让助手输出/用户沉默被当成证据）。

真实写路径 / 召回质量的验证走**测试台 dogfood**：`npm run testbench`（:7888），每轮内幕落盘 `logs/run-*.jsonl` + `dla.db`，AI **直接读这些文件**诊断调整，不靠人复述。这属于「主观验收」——机制对不对靠三绿，好不好用靠 dogfood。

### ⑥ docs-sync（收尾，别漏）

代码绿了不等于做完。按改动范围同步文档：

- 当前主线进展 / 边界变 → 改写 `CURRENT.md`（`STATE.md` 已降为历史存档，不再逐步维护）。
- 值得记的历史/决策 → 写进提交说明（对外里程碑补 `CHANGELOG.md`）。
- 设计/数据结构变 → 改地图对应 cell。
- 对外能力/用法变 → 同步 `README` 与 `docs/` 对外文档。

### ⑦ PR → 合并

PR 正文写清 **改了什么 / 为什么 / 怎么验的（贴三绿输出，比如 `pass 71 fail 0`）**。CI 复跑三绿作为合并门；人 review 后合入。**禁止 `--no-verify`、禁止跳过测试。**

---

## 怎么用 AI 处理 issue / PR

上了 GitHub 之后，把 AI 当成一个守规矩的贡献者来派活：

- **派 issue 给 AI**：issue 描述遵循「背景 + 验收 + 涉及文件」，AI 在自己的分支上按上面 ①–⑦ 走完，最后开 PR。issue 写得越自包含，AI 冷启动越顺。
- **AI 出的 PR 怎么审**：先看 CI 三绿是否过（过不了直接打回）；再看 PR 正文有没有说清改了什么/为什么/怎么验的；重点盯**有没有偷偷动核心机制或破坏公共 API**——这两条是 review 的红线。
- **AI review 别人的 PR**：可以让 AI 做初筛（跑护栏、对照地图规则找 MemoWeft 特有坑、检查 docs 有没有同步），但**是否合并仍是人的决定**，尤其涉及方向的。
- **冷启动提醒**：spawn 出来的 AI 会话没有上一轮记忆。任何交给它的任务（issue / PR 描述 / 注释里的 TODO）都得自包含，带上文件路径和足够上下文，别指望它「记得我们之前聊过」。

`.github/ISSUE_TEMPLATE` 与 `PULL_REQUEST_TEMPLATE`（已建）把这套结构固定下来，是降低 AI 出错率最省事的杠杆。

---

## 维护纪律（哪些能改、哪些先问）

| 类别 | 处理方式 |
| --- | --- |
| 品牌名 / 文档 / 注释 / 构建配置 | AI 可直接改，三绿兜底 |
| 新增接口 / 加功能（不破坏现有） | 走完整 ①–⑦，方案列影响面 |
| 公共 API（`src/index.ts` 导出） | 破坏性改动必须保留 deprecated 别名，方案阶段标明 |
| **核心机制**（三层模型 / 认知纪律 / 置信度算法 / 衰减半衰期 / 读写解耦） | **先在 issue 摊开权衡，作者拍板再动**，不许顺手改 |
| 方向 / 价值判断（License 选型、要不要发布、隐私策略） | **纯属作者决定**，AI 只列选项不替选 |
| `.env` / `DLA_*` 旧键 / `./dla.db` / 物理目录名 | **不碰**（硬约束） |

判断标准：**拿不准算不算「核心」，就当它是，先问。**

---

## 发版流程

`memoweft` 已在 npm 上（`0.1.0` 首发、`0.2.0` 为 latest），是**无 scope 的普通包**。下面是**发下一版**的流程；完整版见 [`docs/PUBLISHING.md`](../PUBLISHING.md)。

发布形态已就绪：`package.json` 已配 `main=dist/index.js`、`types=dist/index.d.ts`、`files` 白名单（只挑 `dist` 的 `.js`/`.d.ts` + 双 README + CHANGELOG + LICENSE）、`type=module`、`engines.node>=20`（Node ≥24 用内置 `node:sqlite`；20/22 需可选的 `better-sqlite3`）、零 runtime 依赖（`better-sqlite3` 是可选 peer、不进 `dependencies`）。

步骤：

1. **定版本号**：`package.json` 的 `version` 与 `src/version.ts` 的 `MEMOWEFT_VERSION` **两处一起改**——没有自动化替你对齐，这是人工纪律。1.0 前 minor 也可能带破坏性改动，按实际影响进位。
2. **发布保险丝已就位**：scripts 里的 `prepublishOnly`（`typecheck && test && build`）在 **`npm publish`** 打包前**自动**跑三绿 + 重新构建，任一步红则中止发布——`npm publish` 时的陈旧 `dist/` 由这道闸兜住，不必手动重跑。（`npm pack` **不**触发它，见下条。）
3. **核对打包内容**：`npm pack --dry-run` 确认只含 `dist` 的 `.js`/`.d.ts` + 双 README + CHANGELOG + LICENSE，无 `.map`/`src`/`.env`/`*.db`。**`npm pack` 不触发 `prepublishOnly`**（只有 `npm publish` 会），它核对的是**当前的 `dist/`**——想核对最新产物，先手动 `npm run build`。
4. **打 tag**：`git tag v0.x.0 && git push --tags`（仓库已是 git 仓、已配 GitHub remote）。
5. **发布**：`npm login`（作者账号）→ `npm publish`（无 scope，**不需要** `--access public`）。
6. **记里程碑**：更新 `CHANGELOG.md`（对外可读的里程碑）；细节留在 git 提交历史里。

> LICENSE 已定 **MIT**（根目录 `LICENSE` + `package.json` `"license"` + README 一致），随 `files` 白名单自动打包，无需再补。

---

## CI

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) 在 push（`main`）和 PR 上跑 **Node 24 下 typecheck + test + build 三步**作为合并门，另加**触达矩阵**：Node 22.18+ 用 `MEMOWEFT_TEST_DRIVER=better-sqlite3` 强制走第二驱动跑全套 `.ts` 测试；Node 20 因无原生剥类型能力、改用 `npm run smoke:dist`（build 后的纯 JS 冒烟脚本）验 `better-sqlite3` 驱动把开库→写→读→迁移→关库跑通。

- 主护栏用 Node 24 是因为跑 `.ts` 测试 / 构建以它为准（Node 22 需 22.18+ 才默认剥类型，Node 20 不支持）；存储驱动本身已抽缝——Node 24 走内置 `node:sqlite`（零依赖），20/22 走可选的 `better-sqlite3`。Node 24 job 会先 `rm -rf node_modules/better-sqlite3` 再测，验「零依赖路径」名副其实。
- CI 用的就是项目真实命令（`npm run typecheck` / `npm test` / `npm run build`），跟本地一致，不搞第二套。
- 有了 CI，README 里「tests 71 passing」的徽章才名副其实。首版若还没挂 CI，先用 shields.io 静态徽章手写数字，**别挂假的动态徽章**。
