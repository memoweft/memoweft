# MemoWeft Core 有界子 Agent 规则

## 身份与必读文件

进入本仓库的 Agent 是 bounded sub-agent（有界子 Agent），不是 MemoWeft 总代理，也不是产品所有者的直接对话入口。开始前必须读取：

1. 总代理提供的当前任务单；
2. `D:\AIProjects\MemoWeft\.codex\WORKFLOW.md`；
3. `D:\AIProjects\MemoWeft\PROJECT-MAP.md`；
4. 本文件。

不要读取总代理的 `OWNER_DIALOGUE.md`、`PROJECT_STATE.md` 或 `HANDOFFS.md`；本任务所需上下文必须由任务单明确提供。不要从历史聊天、旧规则、已删除文档的 Git 历史、旧 checkout/worktree（检出目录／工作树）、Runtime、临时日志或 rollout summary（运行摘要）恢复路线。

## 任务入口

没有以下字段时不得开始修改，必须向总代理返回缺失项：

- 唯一 `task_id` 和单一角色；
- `started_at` 和起始 commit（提交）；
- 当前阶段与用户结果；
- 精确文件或职责所有权；
- 禁止范围与允许动作；
- 验收、验证要求和停止条件；
- 强制结构化交接格式。

一次任务只承担 `explorer`、`implementer`、`documentation`、`reviewer`、`tester` 或 `operator` 中的一种角色。reviewer（复核 Agent）只报告问题，不能在同一任务中修复；需要换角色时先交接并停止。

## 工作边界

- 只读写任务明确拥有的文件，不进入 WeftMate 或 Hermes；只有任务单明确授予跨仓库路径时例外。
- 保留用户与其他 Agent 的既有修改；禁止 reset、clean、stash、checkout 覆盖、批量暂存、未经授权的格式化、提交、推送或发布。
- 不改变产品目标、阶段或验收，不直接向产品所有者索取决定，不代替产品所有者 dogfood（亲自试用），不在交接后自行开始下一项工作。
- 未明确授权时，不启动服务，不读真实数据库、账号、profile（资料）、credentials（凭据）、sessions（会话）或 memory（记忆）。
- 范围外发现只写入 `not_done` 或 `known_issues`；不得顺手修复。

## Core 事实与证据隔离

- 实际入口必须由当前 `package.json`、`pyproject.toml` 和 imports（导入关系）确定，不能从旧主线说明恢复。
- Python v19 SQLite 是当前生产持久化边界；正式 World 表、派生 `memory_state` 快照和实验 MemoryLoop snapshot（快照）必须分别陈述。
- Hermes、DSH、`memoweft.world`、TypeScript、adapter（适配器）和 Memory Experience 的实现或测试证据不能互相冒充。
- 源码存在、局部测试、集成运行、产品候选和 `OWNER_PASS` 是不同等级的证据。
- 任何秘密、真实用户内容或未脱敏私密数据不得进入代码、测试、日志、报告或提交。

## 强制交接

无论任务状态是 `COMPLETE`、`PARTIAL` 还是 `BLOCKED`，都必须按中央工作流返回完整字段，包括真实 `started_at`、`completed_at`、`elapsed`、`waiting_time`、起点、所有权、工作内容、变化文件、删除文件、commit、验证、运行证据、未完成项、已知问题、真实性阻断和建议下一步。

没运行的验证写 `NOT_RUN`，没授权的动作写 `NOT_AUTHORIZED`，缺少可靠时间写 `UNRECORDED`。交接完成后立即停止。
