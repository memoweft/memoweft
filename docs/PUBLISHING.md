# 发布与打包 · MemoWeft

> 面向**维护者 / 作者**：如何把 MemoWeft 打包发到 npm。日常安装 / 体验见 [INSTALL.md](INSTALL.md)。
> ⚠️ 项目在转成 git 仓库前**无法回滚**：发布前的每一步都要跑绿护栏、`--dry-run` 核对，别一把梭。

---

## 1. 包的现状（`package.json` 已基本就绪）

```jsonc
{
  "name": "memoweft",
  "version": "0.0.0",
  "type": "module",
  "main": "dist/index.js",       // 运行时入口（编译后）
  "types": "dist/index.d.ts",    // 类型入口
  "files": ["dist/**/*.js", "dist/**/*.d.ts", "README.md", "LICENSE"],
  "dependencies": {}             // 零运行时依赖
}
```

关键点：
- **`main` / `types` 指向 `dist/`**：消费者装完 `import` 的是**编译后的 `.js` + `.d.ts`**，不是源码。所以**发布前必须先 `npm run build`**。
- **`files` 只挑 `.js` + `.d.ts`**（外加 README / LICENSE）：不把 `.js.map` / `.d.ts.map` 打进包。原因见 §2。
- **`type: "module"`**：纯 ESM 包。消费者用 `import`，不支持 `require`。
- **零 `dependencies`**：存储 / HTTP / 向量全用 Node 内置（`node:sqlite` / `node:http` / `node:fs`）。这也意味着**消费者的 Node 必须 ≥ 22.6**（内置 `node:sqlite` 的要求）——建议在 README 和 `package.json` 里用 `engines` 声明（见 §6 可选加固）。

---

## 2. 为什么 `files` 不打 source map（本次唯一的打包调整）

原来 `files: ["dist"]` 会把整个 `dist/` 打进包，包含 **64 个 `.map` 文件**（`.js.map` + `.d.ts.map`）。这些 map 的 `sources` 指向 `../src/*.ts`，而**源码 `src/` 并不在包里**——所以发出去的 map 是**指向不存在文件的死引用**，对消费者无用、只增体积。

改成只挑 `.js` + `.d.ts` 后：

| | 改前 | 改后 |
| --- | --- | --- |
| 打包文件数 | 129 | **67** |
| 打包体积 | 66.1 kB | **55.7 kB** |
| 解压体积 | 235.4 kB | **162.7 kB** |
| 死 map 引用 | 有 | **无** |

> 这是**唯一**动到的打包相关字段，且**只改 `package.json` 的 `files`**——`tsconfig.build.json` 照旧仍产出 map（本地开发 / 调试有用），只是不把它们**发布**出去。源码逻辑、构建配置零改动。
>
> 如果将来希望消费者能 source-map 回溯，正解是**把 `src/` 也一起发**（`files` 加 `"src"`）让 map 有落点，而不是发一堆死 map。首版不做，保持包小。

---

## 3. 发布前护栏（三绿，缺一不发）

```bash
npm run typecheck   # 类型全绿
npm test            # 54 个测试全过
npm run build       # 重新产出 dist/（务必重跑，覆盖旧产物！）
```

> ⚠️ **`dist/` 里可能残留改名前的旧产物**。发布前**必须重新 `npm run build`**，否则会把陈旧构建发出去。三绿是硬门槛，写进 [AGENTS.md](../AGENTS.md) / CONTRIBUTING。

---

## 4. 发布步骤

### 4.1 查包名是否被占

```bash
npm view memoweft        # 报 404 = 没被占，可用；有内容 = 已被占
```

若 `memoweft` 被占或想用 scope，改成 `@<你的用户名>/memoweft`（scope 包首发要加 `--access public`，见 §4.4）。**只改 `package.json` 的 `name` 这一处**——`src` 内全是相对路径导入、无 `from 'memoweft'` 自引用，改 `name` 对内部 import 零影响。

### 4.2 定版本号

- 当前是 `0.0.0`（占位，不发布）。
- **首个 alpha 建议 `0.1.0`**（在 `package.json` 改）。
- 注意：`package.json` 的 `version` 与代码里的 `MEMOWEFT_VERSION`（`'0.0.0-rebuild'`，`src/index.ts`）**不是同一处**，别混。要不要同步由作者定。

### 4.3 补 LICENSE（作者拍板）

发布前包里应有 `LICENSE`。**许可证类型是价值判断，须作者定**（常见选 MIT 或 Apache-2.0，后者带专利条款更稳）。定了之后：

1. 在根目录建 `LICENSE` 文件。
2. 在 `package.json` 加 `"license": "MIT"`（或所选类型）。
3. README 的 License 段从 “TBD” 改成实际类型。

### 4.4 dry-run 核对包内容

```bash
npm pack --dry-run
```

确认输出里：
- `total files` 只含 `dist/**` 的 `.js` + `.d.ts`（**无 `.map`、无 `src/`、无 `.env`、无 `testbench/`、无 `*.db`**）。
- 有 `README.md`、`LICENSE`、`package.json`。
- `name` / `version` 是你要发的值。

### 4.5 登录并发布

```bash
npm login
npm publish                 # 普通包
# 或 scope 包首发：
npm publish --access public # @<用户名>/memoweft 首次发布必须加，否则默认私有报错
```

### 4.6 打 tag（转 git 仓后）

发布成功后打对应版本 tag，方便追溯：

```bash
git tag v0.1.0 && git push --tags
```

---

## 5. 发布后自检

```bash
# 在一个临时空目录里装一下，确认能装能 import
mkdir /tmp/mw-smoke && cd /tmp/mw-smoke && npm init -y
npm install memoweft            # 或 @<用户名>/memoweft
node --input-type=module -e "import { MEMOWEFT_VERSION } from 'memoweft'; console.log(MEMOWEFT_VERSION)"
```

能打印版本号 = 包结构 / 入口 / 类型都对。

---

## 6. 可选加固（发布更稳，建议做但非必须）

这些**不改源码逻辑**，只补元数据 / 工程护栏，作者按意愿加：

- **`engines` 字段**：在 `package.json` 声明 `"engines": { "node": ">=22.6" }`，让装在旧 Node 上的人早收到警告（因为用了 `node:sqlite` + 原生 ESM）。
- **`.gitignore` / `.npmignore`**：转 git 前务必忽略 `.env`、`*.db`、`dist/`、`logs/`、`node_modules/`。`files` 白名单已兜住 npm 侧，但 git 侧要单独防。
- **`repository` / `homepage` / `bugs` 字段**：填 GitHub 地址，npm 页面会显示仓库链接。
- **`keywords` 字段**：如 `["memory", "cognition", "llm", "agent", "user-model"]`，利于 npm 搜索。
- **CI（GitHub Actions）**：push / PR 触发 `typecheck + test + build` 三绿作为合并门。这样 README 的 “tests 54 passing” 徽章才名副其实（首版无 CI 前先用 shields.io 静态徽章，别挂假动态徽章）。
- **`bin` 字段**：**目前不需要**——MemoWeft 是库、无 CLI 命令。`testbench` 是 `npm run` 脚本、不是对外可执行入口，无需 `bin`。将来若要出 CLI 再加。

---

## 7. 一页流程图

```
改 name/version（可选）        docs: INSTALL / PUBLISHING
        │                              │
        ▼                              ▼
npm view memoweft ── 被占? ──→ 用 @scope/memoweft
        │ 没占
        ▼
补 LICENSE（作者拍板 MIT/Apache-2.0）+ README License 段
        │
        ▼
npm run typecheck && npm test && npm run build   ← 三绿（build 必重跑，覆盖旧 dist）
        │
        ▼
npm pack --dry-run   ← 核对：只 dist 的 .js/.d.ts + README + LICENSE
        │
        ▼
npm login → npm publish（scope 包加 --access public）
        │
        ▼
git tag v0.1.0（转 git 仓后）→ 临时目录 install 自检
```
