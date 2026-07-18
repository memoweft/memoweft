# 参考宿主 Demo（Reference Host Demo）

[English](./reference-host.md) | **简体中文**

`apps/memoweft-host` 下自带的宿主是一个用于**本地单用户演示**的参考实现。

它的存在是为了演示宿主如何使用 MemoWeft Core。它并不是本仓库的主要产品，**也不是生产部署模板**。

MemoWeft 本身是由下面这行导出的库：

```ts
import { createMemoWeftCore } from 'memoweft';
```

## Demo 展示了什么

参考宿主演示了：

- 带记忆召回的对话；
- 可见的记忆形成过程；
- 证据与认知的检视；
- 记忆管理；
- 导出与导入；
- 插件与观察流程。

## 宿主负责什么

宿主负责：

- UI；
- 对话体验；
- 人设与语气；
- 隐私提示；
- 何时触发 `updateProfile()`；
- 如何展示召回的上下文；
- 用户如何管理记忆。

认证、租户隔离、静态数据加密、备份与恢复、可观测性和进程生命周期同样由宿主负责；这些职责刻意不放进 Core。

## MemoWeft Core 负责什么

MemoWeft Core 负责：

- 证据存储；
- 事件蒸馏；
- 认知形成；
- 置信度计算；
- 冲突处理；
- 召回；
- 受控的记忆管理 API；
- 可移植的记忆包。

## 运行 Demo

参考宿主需要 Node.js 24 或更新版本。

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
npm start -w @memoweft/host
```

打开：

```text
http://localhost:7788
```

首次运行时，配置界面会把模型配置写入 `apps/memoweft-host/.env`。默认 SQLite 数据库为 `apps/memoweft-host/data/host.db`；宿主自己的对话历史 JSONL 文件放在同目录的 `apps/memoweft-host/data/sessions/`。两者都是本地私密用户数据，应按此保护。启动前设置 `MEMOWEFT_HOST_DB` 可改用其他数据库路径；会话文件会随该路径的目录存放。

服务只监听 `127.0.0.1`，默认端口为 `7788`（可用 `PORT` 覆盖）。会改变状态的请求还会校验回环 Host、同源 JSON、每进程会话令牌，并限制请求体为 5 MiB；这些只是本地 Demo 防护，不是用户认证或租户隔离。服务默认不对外部网络开放；不要原样通过反向代理暴露它，也不要把它绑定到公网接口。

## 演示路径与生产的区别

参考宿主适合本地查看宿主边界和演示流程。要先快速、离线地验证 MemoWeft 本身，请运行[30 秒离线演示](./demo-script.zh-CN.md)；它不会启动这个服务，也不需要模型端点。

上线真实宿主前，请逐项完成[生产部署清单](./deployment.zh-CN.md#生产部署清单)。尤其要用自己的认证租户边界、数据库位置、运维流程和面向用户的同意策略，替代该演示壳的单本地进程和共享默认 subject。
