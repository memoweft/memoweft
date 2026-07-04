# T2 · 发布保险丝：prepublishOnly + PUBLISHING.md 重写

**对应五关**：信任关。全程不碰业务逻辑，零风险，建议本批第一个做。

## 背景（审计结论）

- 发布纯手动（`npm login` + `npm publish`），无任何自动化关卡；`docs/internal/MAINTENANCE.md` 的发版清单自己警告"dist/ 可能存着旧产物，发布前务必重跑 build"——发陈旧构建是已知真实风险。
- `docs/PUBLISHING.md` 整体停在 0.1.0 首发前的旧快照：版本写 0.1.0（实际 npm latest=0.2.0）、files 清单写 `README.en.md`（实际是 `README.zh-CN.md`，见 package.json:25）、打包数字写 67 文件/55.7kB（实测 0.2.0 是 111 文件/115.6kB）、"只差 npm login + publish""转 git 仓后打 tag"等段落全部过期（PUBLISHING.md:13,17,22,40-42,101-103）。照着走会得出错误预期。

## 改哪里

1. **`package.json` scripts 加一行**：
   ```json
   "prepublishOnly": "npm run typecheck && npm test && npm run build"
   ```
   npm 在 `npm publish` 前自动跑它，任何一步红就中止发布——同时解决"忘跑测试"和"陈旧 dist"两个坑。注意 scripts 现状在 package.json:27-36，别动其他行。
2. **重写 `docs/PUBLISHING.md`**：以 0.2.0 已发布为现实基线。保留仍然正确的部分（files 白名单原则、双 README、npm pack --dry-run 核对步骤），更新：当前版本口径、真实包内容数字（重新跑 `npm pack --dry-run` 取数）、发布步骤加入"prepublishOnly 会自动三绿+构建"的说明、删掉"转 git 仓后"等已完成事项。版本号两处同步（package.json + src/version.ts）的提醒保留——这仍是人工纪律。
3. **`docs/internal/MAINTENANCE.md` 发版流程整段**（约 132-148 行）按 0.2.0 已发布基线一并同步——不只改"务必手动重跑 build"那一句（改为"prepublishOnly 已自动做，此步为复核"），同段还有几处同样过期的要一起清：134 行"首版建议 alpha 0.1.0"、136 行 `files=["dist"]`（实际早已是白名单）、142 行"补 LICENSE……README 标 TBD"（LICENSE 早已定 MIT）、145 行"转成 git 仓后打 tag"（早已是 git 仓）。范围就到这一段为止，别扩到文件其他部分。

## 不许动

- 不加 publish 的 CI 工作流（自动发布涉及 npm token 入库，是作者单独决策，本批不做）。
- 不动 files 白名单、exports、engines（engines 在 T6 步 2 才动）。

## 验收

- [ ] `npm publish --dry-run` 能看到 prepublishOnly 被触发且三绿后才走到打包（dry-run 不真发）。
- [ ] PUBLISHING.md 里不再出现 0.1.0 时代的过期表述（0.1.0 作为历史记录提及不算）；文中包内容数字与本机 `npm pack --dry-run` 实测一致。
- [ ] 三绿。

## 说明：为什么"Node 版本人话报错"不在本任务

原方案想在入口加运行时 Node 版本检查。查证后不可行：`node:sqlite` 在**七个文件**顶部是**静态 import**（evidence/event/cognition 三个 store、managementLog、openStores、migrations、retrieval/vectorRetriever），Node 20/22 上 ESM 模块图在**链接阶段**就报 `ERR_UNKNOWN_BUILTIN_MODULE`，任何检查代码都来不及执行。真正的解法是把 `node:sqlite` 的加载收敛成 `createRequire` 的同步动态加载——那正是 T6 驱动抽缝的一部分，人话报错随 T6 落地（见 T6 验收第 3 条）。本任务只在 README/INSTALL 的 Node 要求处补一句"版本不够时的报错样子"，帮老 Node 用户认错。
