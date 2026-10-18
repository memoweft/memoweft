# MemoWeft

MemoWeft 是面向 AI 应用的 local-first（本地优先）记忆引擎。它把 Evidence（原始证据）、派生 World 对象、provenance（来源链）、revision（修订）、纠正、撤回、权限状态和可迁移数据分别保存，让宿主能够解释“记住了什么、为什么记住”。

[English](./README.md)

## 包含哪些软件表面

根目录 npm package（npm 包）是 TypeScript 库；`py/` 是 Python MemoWeft 2.0 工程，包含生产 Hermes provider（Hermes 提供器）、Trust services（信任服务）、Portable v4（可迁移格式 v4）和 DSH RPC v2。源码树还包含 Memory Experience（记忆体验应用），但该应用不会被塞进根 npm tarball（npm 压缩包）。

Python distribution（Python 发行包）通过 `hermes_agent.memory_providers` entry-point group（入口点组）暴露 `memoweft` provider。包元数据、源码清单和安装后行为属于不同证据层，必须分别验证。

## 安装 TypeScript 包

正式发布后可使用 `npm install memoweft` 安装 npm 版本。Node 24 可使用内置 SQLite 路径；Node 20 与 Node 22 使用可选的 `better-sqlite3` 驱动。

仓库中的候选源码可能比正式 registry（包注册表）版本更新。本地构建成功或获得 tarball hash（压缩包哈希），都不等于已经发布到 registry。

## 记忆合同

MemoWeft 明确区分这些边界：

- 用户原文保持为带来源和权限的 exact Evidence（精确证据）。
- 通过 `confirmed`（用户确认）记住助手提议时，用户的选定片段和完整短回复都须为明示肯定，或肯定短语加提议中的完整逐字值；不能凭两字符重合，或只裁出“是的”丢掉后面的否认。其他明确重述用 `stated`（用户陈述）保留原话；[规则与逐题诊断](docs/distraction-sources.md)列出有限语法和已知限制。
  “好 / 就这么做 / Yes, do that / go ahead”这类动作确认只能确认选中的行动提议，不能确认旁边的事实猜测；确认说明只引用选中的提议，来源链仍保留完整助手原话。
  简短中文确认可带“刚才/之前你说的”指回前缀，但完整主题和动作须逐字匹配所选提议，完整回复的否定、问句与长度检查仍然生效；具体边界见上述规则。
- Entity、Relationship、Event、Cognition 和 Evaluation 是正式 World 对象。
- 同一 revision 与查询输入得到 deterministic Recall（确定性召回），并且 Recall 不写 World。
  Python 召回会用当前有来源的自我命名和已接受别名，关联原文明确对齐的第一人称出生/偏好陈述；缺省目标、主题标签或本人视角本身不证明事实属于本人。其他表达沿既有召回路径，不猜新的身份关联；同名歧义保持未解析，姓名来源和事实来源分别遵守读取权限。
  纯英文召回先走原有 main 候选路径；仅当共有词在原始大小写和撇号语境下均为功能词时，才可去掉原候选。显式集合缩至 79 词，保留非句首大写词、全大写词和独立缩写字母；只要共有实义词，就不受新分数和门槛淘汰。已有英文改进仅使用剩余名额，排序和字符预算不能挤掉应保留的 main 条目；[不变量及回归集合](bench/recall-quality/README.md)有完整说明，包含小写符号等已知限制。
  较早的中性名称成为规范名时，仍可通过当前、对读取目的地可用的别名来源识别有效自我命名；不直接信任别名显示字段或已撤回的来源。
  `I am Martin Mark.`、`I'm Martin Mark.`、`我是林晓。` 这类完整肯定的自我介绍也可建立关联；否定、疑问、整句引述和“我是该人的朋友/同事”不作为本人身份依据。
- correction、retract、forget、archive、mute 和 permission change 都产生 durable receipt（持久回执）。
- Portable bundle（可迁移包）带版本，先校验和 dry-run plan（试运行计划），再做冲突检查与 apply。
- Hermes host event（宿主事件）和用户界面呈现与 Core business terminal（核心业务终态）分别保存和核对。

## 信任边界

本地优先不代表数据绝不离开设备。模型路线、存储路径、同意流程、认证、授权、加密、备份和日志策略都由宿主负责。MemoWeft 的权限字段约束正式产品路径，但不会把任意宿主代码自动变成安全边界。

MemoWeft 使用 [MIT License](./LICENSE)。
