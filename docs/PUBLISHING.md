# 发布与打包 · MemoWeft

> 面向**维护者 / 作者**：如何把 MemoWeft 打包发到 npm。日常安装 / 体验见 [INSTALL.md](INSTALL.md)。
> ⚠️ 发布是对外承诺、不可撤回：发布前每一步都跑绿护栏、`npm pack --dry-run` 核对包内容，别一把梭。

> **现状基线**：`memoweft` 已在 npm 上；实际 `latest` 以 `npm view memoweft version --registry=https://registry.npmjs.org` 为准，待发布版本以当前 `package.json` 为准。本文档讲**发下一版**怎么走，不维护容易过期的固定 latest 文案。

---

## 1. 包的现状（`package.json` 已就绪）

```jsonc
{
  "name": "memoweft",
  "version": "0.5.1",            // 示例：须与 package-lock.json、src/version.ts 同步
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
- **`files` 只挑 `.js` + `.d.ts`**（外加双 README / CHANGELOG / LICENSE）：不把 `.js.map` / `.d.ts.map` 文件打进包。原因见 §2。
- **`type: "module"`**：纯 ESM 包。消费者用 `import`，不支持 `require`。
- **零 `dependencies`**：存储 / HTTP / 向量全用 Node 内置（`node:sqlite` / `node:http` / `node:fs`），`dependencies` 恒为空——「零运行时依赖」指的就是这个。**Node ≥ 24 开箱即用**（`node:sqlite` 到 24 转正）；**Node 20/22** 上内置模块不可用，消费者需装可选驱动 `better-sqlite3`（`npm i better-sqlite3`）。`better-sqlite3` 声明为**可选 peer 依赖**（`peerDependenciesMeta.optional`），装不装用户自己定，不进 `dependencies`——`npm install memoweft` 仍不拉任何 runtime 依赖 / 原生模块。`engines` 已相应放宽到 `>=20`。

---

## 2. 为什么 `files` 不打 source map

`tsconfig.build.json` 照旧仍产出 map（本地开发 / 调试有用），但**不把它们发布**出去——`files` 白名单只挑 `.js` + `.d.ts`。

原因：这些 map 的 `sources` 指向 `../src/*.ts`，而**源码 `src/` 并不在包里**——所以发出去的 map 是**指向不存在文件的死引用**，对消费者无用、只增体积。挑白名单后，包里既没有死 map、也没有 `src/`。

> 如果将来希望消费者能 source-map 回溯，需要同时设计源码与 map 的发布口径。当前保持包小，不发布 map 文件或 `src/`。

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

### 4.1 定版本号（三处一起改，别只改一处）

- `package.json`、`package-lock.json` 根包版本与 `src/version.ts` 的 `MEMOWEFT_VERSION` **必须同步**。改版本后逐项核对，避免发布元数据与运行时自报版本不一致。
- 版本语义：1.0 之前，minor 也可能带破坏性改动（见 CHANGELOG 顶部声明），按实际影响决定进位。
- 顺手更新 `CHANGELOG.md`：把 `[Unreleased]` 段落的内容归到新版本号下。

### 4.2 dry-run 核对包内容

```bash
npm pack --dry-run
```

> 注意：`npm pack` **不触发** `prepublishOnly`（那只在 `npm publish` 时跑）。所以这步核对的是**当前的 `dist/`**——想核对最新产物，先手动 `npm run build` 再 `npm pack --dry-run`。

确认输出里：

- 文件只含 `dist/**` 的 `.js` + `.d.ts`（**不包含 `.map` 文件、`src/`、`.env`、`testbench/`、`*.db`**）。
- 有 `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`LICENSE`、`package.json`。
- `name` / `version` 是你要发的值。

不维护固定的文件数或包体积基线；每次发布都以干净 worktree 中当次 `npm pack --dry-run` 的输出为准，文件数会随 `dist/` 演进而变化。

### 4.3 推送版本 tag，由 CI 发布

```bash
git tag -a v0.x.y -m "memoweft v0.x.y"
git push origin v0.x.y
```

`.github/workflows/ci.yml` 会先等待全部 guardrails、Node 20/22 触达和 SDK 版本矩阵通过，再执行根包 `npm publish --provenance --access public`。仓库必须预先配置具有 Publish 权限的 `NPM_TOKEN`；没有该 secret 时不要推版本 tag。

版本 tag 只发布根包 `memoweft`。公开 adapters / MCP server 有各自的 `0.1.x` 版本线，必须在它们各自升版和验收后单独发布；不要在根包 tag 上使用 `npm publish --workspaces`，否则已存在版本会失败并造成半发布。

若 CI 发布链暂不可用，可在同一干净 release commit 上走本机兜底，但必须先登录官方 registry，并保留完整门禁记录：

```bash
npm login --registry=https://registry.npmjs.org
npm publish --registry=https://registry.npmjs.org
```

> `memoweft` 是无 scope 的普通包；本机兜底不需要 `--access public`。发布版本不可覆盖，失败后的修复应升新版本，不能重写已发布版本。

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
- **CI（GitHub Actions）**：`.github/workflows/ci.yml` 在 push（`main`）/ PR 上跑 Node 24 下完整 guardrails，另加触达矩阵（Node 22.18+ 强制 `better-sqlite3` 跑全测试、Node 20 用 dist 冒烟脚本验）；版本 tag 在所有门禁通过后发布根包并生成 provenance。
- **`bin` 字段**：**不需要**——MemoWeft 是库、无 CLI 命令。`testbench` 是 `npm run` 脚本、非对外可执行入口。将来若出 CLI 再加。

## 7. 一页流程图

```
定版本号（package.json + package-lock.json + src/version.ts 三处同步）+ 更新 CHANGELOG
        │
        ▼
npm run build → npm pack --dry-run   ← 核对只含 dist 的 .js/.d.ts + 双README + CHANGELOG + LICENSE（pack 不触发 prepublishOnly，故先手动 build 出最新产物）
        │
        ▼
推送 v0.x.y tag → CI 等待全部门禁 → 根包 npm publish --provenance
        │
        ▼
临时目录 install 自检
```
