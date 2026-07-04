# 发布与打包 · MemoWeft

> 面向**维护者 / 作者**：如何把 MemoWeft 打包发到 npm。日常安装 / 体验见 [INSTALL.md](INSTALL.md)。
> ⚠️ 发布是对外承诺、不可撤回：发布前每一步都跑绿护栏、`npm pack --dry-run` 核对包内容，别一把梭。

> **现状基线（2026-07）**：`memoweft` 已在 npm 上（`0.1.0` 首发、`0.2.0` 为 latest）。本文档以 **0.2.0 已发布** 为现实基线，讲**发下一版**怎么走。历史里的 `0.1.0` 只作为已发生的事实提及。

---

## 1. 包的现状（`package.json` 已就绪）

```jsonc
{
  "name": "memoweft",
  "version": "0.2.0",            // 当前 latest（与 src/version.ts 的 MEMOWEFT_VERSION 已同步）
  "type": "module",
  "main": "dist/index.js",       // 运行时入口（编译后）
  "types": "dist/index.d.ts",    // 类型入口
  "files": ["dist/**/*.js", "dist/**/*.d.ts", "README.md", "README.zh-CN.md", "CHANGELOG.md", "LICENSE"],
  "engines": { "node": ">=20" }, // Node ≥24 用内置 node:sqlite；20/22 需可选的 better-sqlite3
  "dependencies": {},            // 零运行时依赖（可选 peer 见下）
  "peerDependencies": { "better-sqlite3": ">=9" },
  "peerDependenciesMeta": { "better-sqlite3": { "optional": true } }
}
```

关键点：

- **`main` / `types` 指向 `dist/`**：消费者装完 `import` 的是**编译后的 `.js` + `.d.ts`**，不是源码。所以**发布前必须先 `npm run build`**（下节的 `prepublishOnly` 已把这步兜进自动化）。
- **`files` 只挑 `.js` + `.d.ts`**（外加双 README / CHANGELOG / LICENSE）：不把 `.js.map` / `.d.ts.map` 打进包。原因见 §2。
- **`type: "module"`**：纯 ESM 包。消费者用 `import`，不支持 `require`。
- **零 `dependencies`**：存储 / HTTP / 向量全用 Node 内置（`node:sqlite` / `node:http` / `node:fs`），`dependencies` 恒为空——「零运行时依赖」指的就是这个。**Node ≥ 24 开箱即用**（`node:sqlite` 到 24 转正）；**Node 20/22** 上内置模块不可用，消费者需装可选驱动 `better-sqlite3`（`npm i better-sqlite3`）。`better-sqlite3` 声明为**可选 peer 依赖**（`peerDependenciesMeta.optional`），装不装用户自己定，不进 `dependencies`——`npm install memoweft` 仍不拉任何 runtime 依赖 / 原生模块。`engines` 已相应放宽到 `>=20`。

---

## 2. 为什么 `files` 不打 source map

`tsconfig.build.json` 照旧仍产出 map（本地开发 / 调试有用），但**不把它们发布**出去——`files` 白名单只挑 `.js` + `.d.ts`。

原因：这些 map 的 `sources` 指向 `../src/*.ts`，而**源码 `src/` 并不在包里**——所以发出去的 map 是**指向不存在文件的死引用**，对消费者无用、只增体积。挑白名单后，包里既没有死 map、也没有 `src/`。

> 如果将来希望消费者能 source-map 回溯，正解是**把 `src/` 也一起发**（`files` 加 `"src"`）让 map 有落点，而不是发一堆死 map。当前保持包小，不做。

---

## 3. 发布保险丝：`prepublishOnly`（自动三绿 + 构建）

`package.json` 的 scripts 里有一条 npm 生命周期钩子：

```jsonc
"prepublishOnly": "npm run typecheck && npm test && npm run build"
```

npm 在 `npm publish` **打包之前**会自动跑它。任何一步红（类型不过 / 测试不过 / 构建报错）就**中止发布**，包根本不会生成。这一条同时解决两个老坑：

- **忘跑测试就发**——现在测试红了发不出去。
- **发陈旧 `dist/`**——每次发布前都强制重跑 `npm run build`，覆盖旧产物，杜绝把改名前 / 上一版的构建混着发出去。

所以你**不必手动**再敲一遍三绿再发；直接 `npm publish` 就会先过这道闸。手动想先看一眼绿不绿也行：

```bash
npm run typecheck   # 类型全绿
npm test            # Core 测试全过
npm run build       # 重新产出 dist/
```

> `prepublishOnly` **只在 `npm publish` 时触发**；`npm pack` 和 `npm install` 都不会跑它。所以本地 `npm pack --dry-run` 核对包内容时不会自动重建——要核对最新产物，先手动 `npm run build`。

---

## 4. 发布步骤（发下一版）

### 4.1 定版本号（两处一起改，别只改一处）

- `package.json` 的 `version` 与 `src/version.ts` 的 `MEMOWEFT_VERSION` **必须同步**。这是**人工纪律**——没有自动化替你对齐，改版本时手动改两处。
- 版本语义：1.0 之前，minor 也可能带破坏性改动（见 CHANGELOG 顶部声明），按实际影响决定进位。
- 顺手更新 `CHANGELOG.md`：把 `[Unreleased]` 段落的内容归到新版本号下。

### 4.2 dry-run 核对包内容

```bash
npm pack --dry-run
```

> 注意：`npm pack` **不触发** `prepublishOnly`（那只在 `npm publish` 时跑）。所以这步核对的是**当前的 `dist/`**——想核对最新产物，先手动 `npm run build` 再 `npm pack --dry-run`。

确认输出里：

- 文件只含 `dist/**` 的 `.js` + `.d.ts`（**无 `.map`、无 `src/`、无 `.env`、无 `testbench/`、无 `*.db`**）。
- 有 `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`LICENSE`、`package.json`。
- `name` / `version` 是你要发的值。

**当前 0.2.0 的实测包内容**：约 **111 个文件、115.6 kB 打包体积**（全是 `dist/` 的 `.js` + `.d.ts` 加双 README / CHANGELOG / LICENSE，无死 map、无源码）。发新版时以你本机 `npm pack --dry-run` 的当次输出为准——文件数会随 `dist/` 内容增减浮动。

### 4.3 登录并发布

```bash
npm login      # 作者账号，一次登录后可复用
npm publish    # 发布前 npm 自动跑 prepublishOnly（三绿 + 构建）；任一红则中止
```

> `memoweft` 是**无 scope 的普通包**，直接 `npm publish` 即可，**不需要** `--access public`（那是 `@scope/xxx` 首发才要的）。

### 4.4 打 tag

发布成功后打对应版本 tag，方便追溯：

```bash
git tag v0.x.0 && git push --tags
```

（仓库已是 git 仓、已配 GitHub remote，直接打 tag 即可。）

---

## 5. 发布后自检

```bash
# 在一个临时空目录里装一下，确认能装能 import
mkdir /tmp/mw-smoke && cd /tmp/mw-smoke && npm init -y
npm install memoweft
node --input-type=module -e "import { MEMOWEFT_VERSION } from 'memoweft'; console.log(MEMOWEFT_VERSION)"
```

能打印你刚发的版本号 = 包结构 / 入口 / 类型都对。

---

## 6. 已就绪的工程护栏（无需再补）

以下在早期版本里曾是"待办"，现已落地，列在这里是为了说明**不用再做**：

- **`engines` 字段**：已声明 `"node": ">=20"`（Node ≥24 走内置 `node:sqlite`；20/22 需可选的 `better-sqlite3`）。
- **`repository` / `homepage` / `bugs` / `keywords`**：均已填 GitHub 地址与关键词，npm 页面会显示仓库链接、利于搜索。
- **LICENSE**：已定 **MIT**（根目录 `LICENSE` + `package.json` `"license"` + README License 段一致），随 `files` 白名单打包。
- **CI（GitHub Actions）**：`.github/workflows/ci.yml` 在 push（`main`）/ PR 上跑 Node 24 下 `typecheck + test + build` 三绿作为合并门，另加触达矩阵（Node 22.18+ 强制 `better-sqlite3` 跑全测试、Node 20 用 dist 冒烟脚本验）。
- **`bin` 字段**：**不需要**——MemoWeft 是库、无 CLI 命令。`testbench` 是 `npm run` 脚本、非对外可执行入口。将来若出 CLI 再加。

> 尚未做、属作者单独决策的：**自动发布的 CI 工作流**（把 npm token 入库触发自动 `npm publish`）。本项目当前是手动 `npm publish` + `prepublishOnly` 兜底，自动发布暂不引入。

---

## 7. 一页流程图

```
定版本号（package.json + src/version.ts 两处同步）+ 更新 CHANGELOG
        │
        ▼
npm run build → npm pack --dry-run   ← 核对只含 dist 的 .js/.d.ts + 双README + CHANGELOG + LICENSE（pack 不触发 prepublishOnly，故先手动 build 出最新产物）
        │
        ▼
npm login → npm publish   ← 发布前 npm 再次自动跑 prepublishOnly，任一红则中止（不会发出陈旧构建）
        │
        ▼
git tag v0.x.0 && git push --tags → 临时目录 install 自检
```
