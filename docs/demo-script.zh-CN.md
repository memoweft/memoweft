# MemoWeft 四幕离线演示

[English](./demo-script.md) | **简体中文**

这是验证 MemoWeft 核心规则最快的方式：**说过的话会被记住、纠正保留历史、矛盾不会被悄悄抹平、短暂状态会淡出而持久事实会留下**。演示是确定性且离线的：无需 API key、无需网络，也不会创建持久数据库。

## 约 30 秒跑起来

需要 Node.js 24+、仓库已检出并已安装依赖。

```bash
npm run build
node examples/no-key-demo.ts
```

脚本使用内存 SQLite 数据库和源码内置的 stub LLM，不会写数据库文件。输出会包含一条用户陈述的事实、一个可见冲突和一条低置信推测；这是最短的完整写路径演示。

要观看下面的四幕完整版，请运行：

```bash
npm run demo
```

`npm run demo -- --act 4` 只运行第 4 幕（会先准备第 1 幕基础事实）。`npm run demo -- --fast-forward 30d` 可改第 4 幕的快进时长（默认 `7d`）。

## 为什么结果可复现

四幕脚本注入固定、可推进的 clock（`CreateCoreOptions.clock`），使用输出固定的离线 stub LLM，并注入简单关键词召回器。生产宿主应替换为自己的模型与召回器。常规 Core 未配置 embedder 时，MemoWeft 使用本地 FTS5 关键词召回；语义/向量召回是可选能力。

## 四幕内容

### 1. 记住——陈述变成带置信度的事实

- 输入：`I own a red bicycle.`
- 动作：`ingest → updateProfile（distill → consolidate）` 形成 `fact`。
- 结果：`recall("red bicycle")` 能召回它。置信度由 MemoWeft 自行计算，不采信模型自报分数。

### 2. 纠正——历史不会消失

- 输入：`Actually it isn't mine — my sister owns the red bicycle.`
- 动作：`consolidate.correct` 为旧的「用户拥有自行车」认知标上 `invalidAt`，并采纳新的「妹妹拥有自行车」。
- 结果：旧认知仍可检查，只是失效，不会被静默覆盖。

### 3. 矛盾——不会让其中一方悄悄获胜

- 输入：`I love americano.`，随后是 `ordered milk tea again`。
- 动作：`consolidate.conflict` 将美式咖啡偏好标成 `conflicted`。
- 结果：两种说法都会保留；MemoWeft 不会替用户作无根据的裁决。

### 4. 时间——状态会淡，事实和偏好会留

- 输入：`I have been really stressed and in a low mood this week.`，然后 `--fast-forward 7d`。
- 动作：注入的 clock 前进，短暂 `state` 的有效置信度衰减到召回阈值以下。
- 结果：快进后情绪不再被召回；妹妹的自行车事实和偏好仍保留。

## 验证确定性输出

```bash
npm run build
node examples/demo.ts > /tmp/run1.txt
node examples/demo.ts > /tmp/run2.txt
diff /tmp/run1.txt /tmp/run2.txt
```

`diff` 无输出即为预期结果。
