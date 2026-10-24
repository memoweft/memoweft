# MemoWeft Core 维护入口

先读本仓库 [README](README.md)，按任务查看 `package.json`、`py/pyproject.toml` 与实际源码、测试。

## 规矩在哪

工作规矩只有一份：WeftMate 仓库的 [AGENTS.md](https://github.com/memoweft/weftmate/blob/main/AGENTS.md)。维护者是 WeftMate 的总代理（WeftMate D111）；产品决定在 WeftMate 的 [DECISIONS](https://github.com/memoweft/weftmate/blob/main/docs/DECISIONS.md)，本仓库不另抄一份。这里只记本仓库特有的技术事实，和 WeftMate AGENTS 冲突时以后者为准。

## 本仓库特有的

- 形成、召回、删除语义、修订链的问题在这里修；WeftMate 只负责调用、展示和换版本，不在宿主重复实现内核语义。
- 修 bug 先写一条会失败的行为测试。
- GitHub 规则集：只许压缩合并（squash）；必需检查 `CI Gate`；所有审查线程须已解决（自动代码审查会留线程，合并前先看）。
- Core 合进 `main` 后，在 WeftMate 开单独的 PR 换版本：`.github/workflows/ci.yml` 的 `observed-core` 和 `scripts/memory-runtime/lock.json`。
- 测试用隔离目录和合成数据；公开 PR、问题和日志不放密钥、密码、令牌和真实记忆内容。
