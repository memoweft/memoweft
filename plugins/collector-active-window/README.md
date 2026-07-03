# @memoweft/collector-active-window

MemoWeft **采集插件（Collector Plugin）** · Windows 前台活动窗口采集器 V1（仅 Windows · 零依赖）。

> 架构归位：真实采集属 **Plugin 层**，不属于 Core（`boundaries.md §4.1`）。本包从 `src/perception/collectors/` 迁出后独立成 workspace。

## 它是什么

每隔几秒采一次 Windows 当前前台窗口（应用名 + 标题），把连续停留合并成一段，够阈值的段映射成通用 `Observation`，交给 Host 落成 `observed` 证据。之后 MemoWeft 的画像/召回就能用上"用户在什么应用停留多久"这类被动信号。

## 数据流（架构归位路线 §3）

```
采集器（本包）采窗口
  → 映射成 generic Observation（activeWindowToObservation）
  → POST Host /api/observe
      → Host 审核（① 采集总开关 ② 强制剥掉 allowCloudRead ③ 调 core.ingestObservation）
      → Core 落 observed 证据
```

采集插件**绝不直穿 Core / Store**，一律经 Host `/api/observe` 这道审核门（路线 §7「插件只能请求，Host 审核，Core 执行」）。

## 隐私红线

- 采集器 POST 的 Observation **不带任何上云授权位**；
- Host `/api/observe` 再**强制剥一道** `allowCloudRead`；
- Core 对 `observed` 证据套保守默认：**本地可读 / 不上云 / 可推画像**。

想让某条观察上云，是记忆管理页的**人工动作**，不是采集默认。

## 怎么跑

先起 Host（另开一个终端）：

```bash
npm run build                      # 先出 dist（Host / 插件都经 import 'memoweft' 用 Core）
npm start -w @memoweft/host        # Host 起在 :7788
```

再起采集器：

```bash
npm run collector                                  # 缺省：5s 采一次，停留 ≥30s 才落
node plugins/collector-active-window/run.mjs 2 10  # 可选：采样间隔秒 + 产出阈值秒（冒烟调短用）
MEMOWEFT_HOST_URL=http://localhost:7788 npm run collector   # 可选：改 Host 地址
```

`Ctrl+C` 优雅退出（会冲刷最后一段再走）。

采集开关（Host 侧）：环境变量 `MEMOWEFT_HOST_COLLECTOR=off` 可让 Host 拒收采集（`/api/observe` 返回 403）。缺省 `on`。

## 测试

```bash
npm test -w @memoweft/collector-active-window
```

纯逻辑离线护栏：合并 / 阈值 / 切换 / pause / stop / 采不到截断 / 不带授权位 / onEmit 抛错不崩 + 样本→Observation 映射。不碰真 Win32、不起真定时器（sampler / 时钟 / 定时器全注入）。
