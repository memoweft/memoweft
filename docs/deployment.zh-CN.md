# 模型部署、隐私与生产运行 · MemoWeft

[English](./deployment.md) | **简体中文**

> MemoWeft 可接入云端或本地模型。证据级标记只控制哪些内容能进入内建写模型提示词，不是端到端披露控制。

**想先本地验证？** 运行[离线演示](./demo-script.zh-CN.md)：依赖安装后，无端点、无 key、无网络，也不会创建持久数据库。仓库自带的[参考宿主](./reference-host.zh-CN.md)只是本地单用户演示，不是生产模板。本页说明真实宿主上线前必须自行完成的工作。

## 模型与召回配置

最小云端聊天配置如下；写路径模型和 embedder 都是可选项。

```ini
MEMOWEFT_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_LLM_API_KEY=sk-xxxx
MEMOWEFT_LLM_MODEL=your-chat-model

# 可选：独立的小型、快速写路径模型
MEMOWEFT_WRITE_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_WRITE_LLM_API_KEY=sk-xxxx
MEMOWEFT_WRITE_LLM_MODEL=your-small-fast-model
MEMOWEFT_WRITE_LLM_TIER=cloud

# 可选：语义/向量召回；未配时使用本地 FTS5 关键词召回
MEMOWEFT_EMBED_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_EMBED_API_KEY=sk-xxxx
MEMOWEFT_EMBED_MODEL=your-embedding-model
```

所有 `MEMOWEFT_*` 模型变量都兼容旧的 `DLA_*` 名称；新部署请使用 `MEMOWEFT_*`。没有 embedder 不会让写路径停摆：通常会降级到本地 FTS5 关键词召回；只有当前 SQLite 不含 FTS5 时才会进一步降级为空召回。

`MEMOWEFT_WRITE_LLM_TIER=cloud|local` 声明写路径模型可读取的证据授权层级：`cloud` 只读取 `allowCloudRead=true` 的证据；`local` 读取 `allowLocalRead=true` 的证据。它只是声明，不会探测端点——把云端 URL 标成 `local` 仍会把数据发往云端。

## 隐私边界

- `allowCloudRead` 只约束 MemoWeft 内建云端写模型提示词能使用哪些证据；它不限制召回、list/read API、MCP 工具、适配器提示词注入、派生 cognition/event/graph、宿主自写代码、导出或日志，也不是访问控制。
- `observed` 和工具结果默认不允许上云；宿主仍须提供清晰的同意、查看、修改授权和删除流程。
- SQLite 数据库本身未加密。`allowCloudRead` 与磁盘加密无关；请由操作系统、卷或托管平台提供静态加密。
- Core 是库，不提供认证、权限、租户隔离、密钥托管或合规政策。它们全部属于宿主。

## 生产部署清单

MemoWeft 是嵌入式 SQLite 库，不是托管服务。下面每项都应在集成它的应用中实施、测试和留档；参考宿主不会替你完成。

### 进程、网络与单租户边界

- [ ] 使用受监管的进程与重启策略、有限日志和优雅退出；等待在途工作结束后调用 `core.close()`。
- [ ] 仅绑定威胁模型允许的网络接口；在应用或边缘层提供 TLS、认证、授权、限流和请求体大小限制。
- [ ] 先定义租户边界再写数据。不要在用户之间共享同一个 SQLite 库、默认 `subjectId`、会话缓存或导出的记忆包。每次内存操作前都应认证请求、校验权限，并传入租户范围的 `subjectId`。
- [ ] 参考宿主只监听 `127.0.0.1`，没有认证也没有多租户隔离；不得原样对外发布。

### 持久卷、加密与恢复

- [ ] 将 SQLite 数据库和宿主的会话文件放到持久、受访问控制的卷上。不要依赖容器临时文件系统；挂载并实际测试持久卷。
- [ ] 使用 BitLocker、FileVault、LUKS 或托管卷等方式加密磁盘/卷。MemoWeft 的 SQLite 文件不会因 `allowCloudRead` 或任何 Core 设置而加密。
- [ ] 按既定频率备份数据库和宿主状态；备份也应加密、限制访问、设置保留期，并记录存放位置。
- [ ] 在隔离环境实际恢复备份，并验证能读取和召回一位代表性用户。没有演练过恢复的备份不是恢复方案。
- [ ] 用户导出/导入应走 portable bundle API。先用 `dryRun` 校验，再 `merge`；向量索引是派生数据，导入后可能需要运行 `updateProfile()` 重建。

### 密钥与模型隐私

- [ ] 将模型 API key 放在密钥管理器或受保护的运行环境；绝不提交 `.env`、数据库、导出包，或含用户记忆的请求/响应日志。
- [ ] 最小权限分配密钥、定期轮换，并确保诊断输出会脱敏。
- [ ] 明确并记录 `MEMOWEFT_WRITE_LLM_TIER=cloud|local`。它不验证端点；将云端端点标为 `local` 不会让数据留在本地。
- [ ] 复核证据授权默认值。`allowCloudRead` 仅控制 Core 内建云端提示词过滤，不是访问控制，也不能保护宿主或插件自行发送的数据。

### Schema 升级与发布

- [ ] 固定并分阶段升级 MemoWeft 版本。发布前阅读更新日志，并在生产数据副本上跑迁移/启动验证。
- [ ] 每次可能影响 schema 的升级前先备份；一次只部署一个兼容版本，并保留已测试的回滚路径。除非已验证兼容性，不要让新旧应用版本并发访问同一 SQLite 文件。
- [ ] 发布后检查数据库的归属与文件权限；避免因挂载或路径错误悄悄新建一个空库。

### `updateProfile` / `expire` 调度、health 与 usage

- [ ] 不要把 `core.updateProfile()` 放在回复延迟关键路径上。应按消息攒批、空闲后、定时任务或用户手动刷新触发；同一 subject 必须防止并发运行。
- [ ] 把 `core.expire()` 作为周期性维护任务调度（如每日，或在计划中的 `core.updateProfile()` **之前**）。它让超过 `expireAfterDays` 时限的临时类认知（`state`/`hypothesis`/`trend`）标失效、不再被召回；幂等、纯规则（不走 LLM/embedder）、不删除（标 `invalidAt`、保留可溯源），并刻意与 `updateProfile` 解耦——不调度就永不过期。注意 `expire` 不重建召回索引，因此应在画像更新【之前】跑（随后的重建会把刚失效的条目清出索引），而非更新【之后】：上一次重建之后才失效的条目会一直留在索引里，直到下一次 `updateProfile`。它们在召回时始终被过滤掉（绝不会返回），但滞留索引期间会挤占召回的超取候选池，压低一次查询能浮现的有效记忆数。
- [ ] 用 `core.health()` 监测配置状态（`llmReady`、`embedReady`）。`embedReady: false` 仅代表语义/向量召回不可用；Core 通常会使用本地 FTS5 关键词召回，FTS5 不可用时才为空召回。
- [ ] 如需按操作归因模型成本，在宿主操作前后读取 `core.usage()` 的增量。它只统计 Core 自持客户端且端点实际返回的 usage，不是通用计费表。
- [ ] 对写路径任务失败、反复模型超时、数据库打开错误、备份失败、恢复演练失败和意外空数据库告警；不要为了告警方便而记录原始证据。

### 上线验收演练

上线前，至少演练一次：部署到空的持久卷；重启进程后确认数据仍在；移除运行实例后把备份恢复到隔离环境；验证租户 A 不能读取、导出或召回租户 B；运行一次画像更新；检查 health 与 usage；并轮换一个非生产模型密钥。记录每项结果和负责人。
