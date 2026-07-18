# MemoWeft 边界：Core、Host 与 Plugin

[English](./boundaries.md) | **简体中文**

MemoWeft 将记忆库职责与应用、扩展职责分开。本文描述内建 Core API 和 `PluginContext` 的公开边界；它不是任意 Host 或 Plugin 代码的安全隔离边界。

另见[架构总览](./architecture.md)、[插件契约](../plugin-contract.zh-CN.md)和[记忆面契约](../reference/memory-surface-contract.zh-CN.md)。

## 概览

| 层     | 主要职责                             |
| ------ | ------------------------------------ |
| Core   | 记忆数据、处理流程和公开记忆 API。   |
| Host   | 产品行为、运维、同意流程和安全控制。 |
| Plugin | 通过受支持扩展接口提供可选能力。     |

## Core

Core 是由宿主应用导入的记忆库。它负责 evidence、event、cognition 数据模型；摄入、召回和记忆管理 API；溯源与置信度处理；迁移、完整性检查与便携记忆包；以及模型和检索抽象。

Core 是无头库。它不提供聊天产品或 UI，不定义宿主的隐私政策，不采集操作系统数据，不动态加载插件，也不会自动调度 `updateProfile()`。宿主决定何时以及如何调用这个一次性操作。

内建 Core 路径会维持库的数据与处理规则。使用自定义存储、摄入或数据转发路径的宿主，应在需要处自行提供相应的保护措施。

## Host

Host 是配置并使用 Core 的应用。`apps/memoweft-host` 是本仓库中的参考实现。

Host 负责 Core 生命周期和存储配置；产品 UI 与对话行为；同意流程和面向用户的隐私提示；认证、授权、传输、存储和租户隔离；画像更新调度；记忆管理流程；以及插件注册和策略。

宿主应通过 Core 的公开 API 执行记忆操作，并校验经由自身集成进入的数据。它也负责判断是否适合安装某个插件，以及向插件授予哪些权限或产品能力。

## Plugin

Plugin 提供可选的体验、工具或观察采集能力。受支持的插件可以观察生命周期 hook，并使用 `PluginContext` 授予的能力，例如提交 observation 或请求召回记忆。它们也可以提供由宿主接入产品的能力。

`PluginContext` 暴露的是选定的 Core 能力，而不是 store API。这会限制内建扩展面，但不会沙箱化任意插件代码。宿主仍需负责执行环境、插件信任决策、同意流程，以及 Plugin 或 Host 代码所需的安全控制。

## 授权标记与安全控制

`allowLocalRead`、`allowCloudRead`、`allowInference` 等 evidence 标记，只会影响 MemoWeft 内建路径中的 prompt 选择和推理行为。它们不是访问控制、加密或通用数据安全机制。对于存储、传输、访问、租户隔离，以及转发到这些路径之外的任何数据，宿主必须实施自己的控制。

## 常见流程

```text
用户 / UI
   ↓
Host ── public API ──→ Core
 ↑                       │
 └──── result / recall ──┘
```

```text
采集 Plugin
   ↓ observation 请求
Host 策略与同意检查
   ↓ core.ingestObservation()
Core evidence 管线
```

```text
用户发起记忆管理操作
   ↓ 确认与理由
Host
   ↓ core.memory.*
Core 校验、变更与审计元数据
```

这些流程展示了常见集成入口。自定义集成可能还需要额外的宿主侧校验、授权和运维控制。
