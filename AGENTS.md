# MemoWeft Core 维护入口

先读本仓库 [README](README.md)，按任务查看 `package.json`、`py/pyproject.toml` 与实际源码、测试。技术入口以当前文件为准，当前任务决定修改范围；过去的清理授权不能用于新的任务。

## 维护归属

维护者是 WeftMate 的总代理。跨仓库安排见 [WeftMate 项目地图](https://github.com/memoweft/weftmate/blob/main/PROJECT-MAP.md) 和 [现行指令 #332](https://github.com/memoweft/weftmate/issues/332)；产品决定在 WeftMate 维护，本仓库不另抄一份。

每个记忆问题先判断根因归属，并写进修复 PR：形成、召回、删除语义、修订链的问题在 Core 修；WeftMate 负责调用、展示和版本更新，不能在宿主重复实现内核语义。

## 验证与合并

- Core 改动在本仓库开独立 PR。修 bug 先用实际行为测试复现，再修复；验证受影响路径，写清命令、结果、证据和未覆盖行为。
- 合并前合入最新 `main`，在最终提交上取得本仓库 CI（持续集成）首次全绿、作者以外的审查通过。失败先查根因，不以跳过用例、放宽断言或重复运行代替修复。
- 确定性 CI 与 Nightly Live（夜间真实模型验证）分开报告；一个通过不能代替另一个通过，也不代表安装版或本人日用验收。

## 版本如何进入 WeftMate

Core 合入 `main` 后，再在 WeftMate 开独立 PR：更新 `.github/workflows/ci.yml` 中 `observed-core` 的 `ref`，同步现存发布组件的版本固定，并复验真实 Core。Core 源码测试、WeftMate 集成测试和候选安装包场景验收分别记录，不能相互替代。

## 数据与公开证据

对日用数据操作前先备份；遵守当前任务对数据、安装版和原源码目录的边界。测试使用隔离目录和合成数据。公开 PR、问题和日志不放凭据、本机用户名、机器名、本机路径、私人截图或真实记忆内容。
