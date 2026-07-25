# 参与 MemoWeft

感谢你帮助改进 MemoWeft。核心库、框架集成、示例、文档、兼容性测试和评测用例都欢迎贡献。

[English](./CONTRIBUTING.md) | **简体中文**

## 开始之前

- 使用 [GitHub Discussions](https://github.com/memoweft/memoweft/discussions) 讨论用法和早期方案。
- 提交问题前先搜索现有 [Issues](https://github.com/memoweft/memoweft/issues)。
- 安全漏洞请按照 [SECURITY.md](./.github/SECURITY.md) 私密报告。
- 较大的改动请先开 Issue，提前确认范围和兼容性影响。

## 开发环境

仓库开发推荐 Node 24。构建后的包通过可选的 `better-sqlite3` 驱动支持 Node 20 和 22，但源码测试依赖较新 Node 提供的原生 TypeScript 执行能力。

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run typecheck
npm test
npm run build
```

单元测试完全离线，不需要 `.env` 或模型凭据。真实模型测试和交互式测试台使用 [docs/INSTALL.zh-CN.md](./docs/INSTALL.zh-CN.md) 中记录的环境变量。

## 贡献流程

1. 从 `main` 创建范围明确的分支。
2. 一次改动只解决一个问题。
3. 行为变化必须新增或更新测试。
4. 用法或行为变化必须同步公开文档。
5. 在本地运行相关检查。
6. Pull Request 说明改了什么、为什么改以及如何验证。

推荐分支前缀为 `feat/`、`fix/`、`docs/` 和 `chore/`。不要强推共享分支，也不要绕过仓库检查。

## 必须通过的检查

代码改动应通过：

```bash
npm run format
npm run lint
npm run typecheck
npm test
npm run build
npm run api:check
```

运行 `npm run format:write` 可自动套用 Prettier 格式。CI 第一步就是 `npm run format`（仅检查），未格式化的改动会先于其它检查导致 CI 失败。

文档改动还应通过：

```bash
npm run docs:links
npm run docs:snippets
```

各 workspace 另有自己的 `typecheck`、`test` 和 `build` 脚本。CI 会运行受支持的 Node 矩阵、workspace 检查、API 表面验证和文档检查。

## 核心不变式

MemoWeft 明确区分记录与认知。修改记忆模型时必须保留以下约束：

- 助手回复只是上下文，本身永远不会成为证据；
- 置信度由确定性规则计算，不接受模型自报；
- 未解决的冲突保持可见，不被静默裁决；
- 派生认知只能引用获准的证据 ID；
- 纠正、失效、授权和删除始终保留可审计的数据路径。

修改公共 API、SQLite schema、便携包格式、授权行为、置信度规则或衰减策略前，请先用 Issue 说明兼容性和迁移影响。

## 依赖策略

核心包刻意保持零运行时依赖。Node 内置模块可以满足需求时，应优先使用内置能力。新增依赖需要在 Pull Request 中说明运行时、安全、维护和包体积影响。

`better-sqlite3` 是 Node 20/22 使用的可选 peer dependency，也是兼容性测试使用的开发依赖；Node 24 的零依赖路径不需要它。

## 文档与发布说明

- 用户可见行为写入对应 README 或 `docs/` 页面。
- 公共 API 变化必须同步 Memory Surface Contract 和 API 快照。
- 重要的用户可见变化写入 `CHANGELOG.md` 的 `[Unreleased]`。
- Roadmap 只描述结果目标，实施记录放在 Issue 和 Pull Request 中。

## Pull Request 检查表

- [ ] 改动范围明确，动机清楚。
- [ ] 相关测试已经新增或更新。
- [ ] lint、typecheck、测试、构建和 API 检查通过。
- [ ] 文档与示例和实际行为一致。
- [ ] 没有提交凭据、本地数据库、运行产物或用户数据。
- [ ] 必要时记录了兼容性与迁移影响。

提交贡献即表示你同意以仓库的 [MIT License](./LICENSE) 授权该贡献。参与项目空间时请遵守 [行为准则](./CODE_OF_CONDUCT.md)。
