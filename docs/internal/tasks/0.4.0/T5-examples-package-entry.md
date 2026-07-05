# T5 · examples 扩到 3 个 + 以包名为入口

> 让英文开发者从 npm 首页复制即用。**波3。软依赖 T4（示例里的 hostId）与最终 API。**

## 背景

`examples/` 现只有 `examples/minimal.ts`（`find` 确认，无其它）。它已是**最新范式**：走统一入口 `createMemoWeftCore`，演示读写闭环（建 core → `ingestUserMessage` → `updateProfile` → `memory.listCognitions` → `handleConversationTurn` → `close`）。但入口是**相对路径** `from '../src/index.ts'`（`minimal.ts:25`），**不是以包名 `memoweft` 为入口**——「以包名为入口」这条现在一条不满足。`minimal.ts:44` 用了 `hostId:'example'`。

公共 API 门面（`src/index.ts`）能力可选做示例：`createMemoWeftCore`、`memory.*`（受控记忆管理 7 操作 + 审计）、`portable.*`（导入/导出/备份）、`graph.buildMemoryGraph`、多轮 `handleConversationTurn`。

## 作者已拍板（本卡相关）

- **B10 = 新增 2 个选题 = ②记忆管理 `memory.*` + ③便携包 `portable`**（最能展示差异化卖点）。
- **B11 = 入口全改包名 `'memoweft'`** + 头注「先 `npm run build` 再跑」（贴真实宿主用法）。

## 改哪里

1. `examples/minimal.ts`：
   - 入口 `from '../src/index.ts'` → `from 'memoweft'`。
   - 头注补一句运行前提：**包名入口需先 `npm run build`**（包名解析到 `dist` 的 `.js`，不能直接 `node examples/minimal.ts` 跑 `.ts`）。
   - `hostId` 取值与 T4 新默认对齐（可显式 `hostId:'example'` 保留，或省略让它落 `'local'`——**别与 T4 新默认相矛盾**）。
2. 新增 `examples/memory-management.ts`（B10 ②）：演示 `core.memory.*`——列记忆、`invalidateCognition` / `mergeCognition` / `removeCognitionSafely` 之一 + 审计留痕（`reason`）。以包名 import、用独立示例库（如 `./example-memory.db`）。
3. 新增 `examples/portable-bundle.ts`（B10 ③）：演示 `core.portable.exportBundle` → `validateBundle` → `importBundle`（导出一份、校验、导入到另一库）。展示「记忆可迁移资产」卖点。以包名 import、独立示例库。
4. 三个 example 都：走 `createMemoWeftCore` 门面、以包名 `import`、用**独立示例库**（不碰 `./dla.db`）、跑得通、`close()` 收尾。

## 不许动

- `./dla.db` 默认（示例用独立库，如 `./example*.db`）。
- 不引第三方依赖（examples 只 import 本包）。
- 不改任何 `src/` 算法。

## 验收（可执行核对）

- [ ] 三绿。
- [ ] `examples/` 下有 3 个 `.ts`，**每个都 `from 'memoweft'`**（`grep -L "from 'memoweft'" examples/*.ts` 为空）。
- [ ] `npm run build` 后，三个 example 各自 `node examples/xxx.js`（或按头注方式）**跑通、无报错**、退出码 0。
- [ ] 示例里 `hostId` 取值与 T4 新默认不矛盾。
- [ ] 未新增 runtime 依赖。

## 与其它卡的关系

- 软依赖 **T4**（示例 hostId 别与新默认打架）与最终 API（跑通即证 API 未过时）。
- T6 的英文文档会引用/对齐这些示例——**T5 先，T6 跟**。

## 发现待办

- B11 取舍留档：全改包名后「直接 `node examples/minimal.ts` 跑 `.ts`」的便利会断（需先 build）——已按作者拍板选此（最贴真实宿主用法）。
