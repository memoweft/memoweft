# 0.3.0 补漏加固批次 · 任务书总览

> 依据：2026-07-04 全库审计（8 路只读调查，结论均带文件行号）+ 作者五项拍板。
> 执行者：任何 AI 会话。开工前必读 `AGENTS.md`，然后只读本目录里自己领的那份任务书和它点名的源码文件。

## 批次目标

修掉审计抓到的红灯，让"隐私三红线"回到全绿、发布有保险丝、多进程不裸奔。这批全是小刀（T6 除外），**不做新功能**。

## 任务清单与顺序

| 序 | 任务书 | 一句话 | 大小 | 依赖 |
|---|---|---|---|---|
| 1 | [T2-publish-safety.md](./T2-publish-safety.md) | prepublishOnly 保险丝 + PUBLISHING.md 重写 | 小 | 无 |
| 2 | [T1-observed-privacy-sink.md](./T1-observed-privacy-sink.md) | 隐私红线 B 下沉 core：observed 默认不上云绑到 put 层 | 中 | 无 |
| 3 | [T3-json-repair-unify.md](./T3-json-repair-unify.md) | attribute/trends 换加固版 JSON 解析 | 小 | 无 |
| 4 | [T4-sqlite-busy-timeout.md](./T4-sqlite-busy-timeout.md) | SQLite busy_timeout（WAL 本批明确不做） | 小 | 无 |
| 5 | [T5-repo-cleanup.md](./T5-repo-cleanup.md) | 扫尾：残留/文档漂移/静默失败 | 小 | 无 |
| 6 | [T6-sqlite-driver-seam.md](./T6-sqlite-driver-seam.md) | 数据库驱动抽缝 + better-sqlite3 可选适配（Node 20/22 触达）| 大 | 建议最后做 |
| 7 | [T7-open-source-pledge.md](./T7-open-source-pledge.md) | "永远全开源"公开承诺（纯文档，措辞需作者过目） | 微 | 无 |

T1–T5、T7 互不依赖，但建议按上表顺序串行、每个任务独立提交、每次提交前三绿。T6 分两步，步 1 合完才做步 2。

## 五项拍板落点（2026-07-04 作者定案的去向，防"漏排了"的误会）

1. **库+生态获客** —— 方向性决策，无本批工程动作；生态动作（MCP 服务器、适配器）在总纲第 8 步（0.5.0）。
2. **隐私 B 下沉 core** —— 本批 [T1](./T1-observed-privacy-sink.md)。
3. **进英文市场** —— 总纲第 4 步（0.4.0：提示词语言可配 + 英文 INSTALL/integration + 包名入口 examples）。
4. **better-sqlite3 可选层** —— 本批 [T6](./T6-sqlite-driver-seam.md)。
5. **永远全开源承诺** —— 本批 [T7](./T7-open-source-pledge.md)。

## 全局规矩（每份任务书都默认包含）

1. **三绿**：`npm run typecheck && npm test && npm run build` 全过才算完成（AGENTS.md 铁律）。
2. **不扩范围**：只做任务书写明的事。顺手发现的问题记进任务书末尾"发现待办"，别顺手修。
3. **防偏移三问**（动手前自问）：这对应商用五关哪一关？是给库加固还是给宿主加戏？动没动灵魂（认知纪律 / 隐私三红线 / 零运行时依赖）？——动灵魂的改动只有任务书明确写了的那些，任务书没写的不许动。
4. **提交口径**：一个任务一个提交，说明写短（干净仓库·里程碑级），CHANGELOG 有行为变化才记。
5. **兼容红线**：`DLA_*` 环境变量回退和 `'./dla.db'` 默认路径按 CONTRIBUTING.md（§兼容）保留，本批任何任务都不许动。

## 批次完成的验收（全批合完后跑一遍）

- [ ] 隐私三红线回归：`tests/privacy.test.ts` + T1 新增测试全绿，且 testbench `/api/observe` 注入的观察证据 `allowCloudRead=false`。
- [ ] `npm pack --dry-run` 内容与 PUBLISHING.md 描述一致。
- [ ] 两个 `parseOut` 裸解析在 src/ 里 grep 不到了。
- [ ] openStores 打开的连接上 `prepare('PRAGMA busy_timeout').get()` 读回 `{ timeout: 5000 }`（结果列名是 timeout）。
- [ ] （T6 后）Node 22 + better-sqlite3 全测试绿；Node 24 不装 better-sqlite3 全绿。

## 本批明确不做

- Memory Surface Contract v1（排下一主线，见 CURRENT.md）。
- WAL、加密落盘、投毒防护、成本观测、i18n、MCP、eval 套件——归属见 [`../后续批次总纲.md`](../后续批次总纲.md)（11 步大纲），别提前掺进来。
