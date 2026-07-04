# T3 · JSON 解析统一：attribute / trends 换加固版

**对应五关**：证据关（写路径稳定性）。小任务，纯一致性对齐，无行为设计决策。

## 背景（审计结论）

写路径三处调云端 LLM 要 JSON 输出。`consolidate` 已用加固版 `parseJsonObjectWithRepair`（去代码围栏 + 失败落日志 + 带上下文重试一次，见 `src/consolidation/consolidate.ts` 约 171-174 行的用法），但另外两处还是各自的旧式裸解析——`indexOf('{')..lastIndexOf('}')`，失败静默返回空、无日志无重试：

- `src/attribution/attribute.ts:81-90` 的 `parseOut`
- `src/background/trends.ts:61-70` 的 `parseOut`

`src/llm/jsonRepair.ts` 文件头自己写着旧办法"接不同模型后不稳"。换小模型后这两条链会静默产不出结果且难排查。

## 改哪里

两个文件的 `parseOut` 调用点换成 `parseJsonObjectWithRepair`，**以 consolidate 的用法为样板照抄**（传入同一次调用用过的 messages 与 llm，让修复重试复用已过滤的上下文——隐私红线 C 依赖这一点：`src/llm/jsonRepair.ts:69-79` 只复用已过滤 messages + 追加提示，不会引入新证据文本）。删掉两处本地 `parseOut` 函数。

最终失败的降级语义**保持现状**：返回空结果、不抛错（attribute 产不出假设、trends 产不出趋势，都是可接受的静默降级）——但现在会留下日志，可排查。

## 不许动

- `src/llm/jsonRepair.ts` 本体（它已有 7 条测试罩着）。
- 两处的 prompt 内容、过滤逻辑、支撑集拦截逻辑（attribute.ts 的 `filterCloudReadable` 与硬引拦截、trends.ts 同理）——只换解析，别顺手改别的。

## 测试

在归因与趋势的现有测试文件里各补（先 `ls tests/` 认准真实文件名，归因是 `tests/attribution.test.ts`，趋势按现有命名找）：

1. **容错解析**：stub LLM 一次返回带围栏/带前后废话的 JSON → 直接解出结果，**断言 llm 只被调 1 次**（注意：这种脏 JSON 会被 `parseJsonObject` 的容错处理一次解掉，走不到重试——这条测的就是容错，别误以为测了重试）。
2. **真重试**：stub 第一次返回完全没有花括号的纯文字（如"我不知道"），第二次返回合法 JSON → 产出结果，**断言 llm 被调 2 次**。
3. **降级**：stub 连坏两次 → 降级为空且不抛。

## 验收

- [ ] `grep -rn "parseOut" src/` 零命中（现状只在 attribute.ts 与 trends.ts 各两处出现，删干净即零；注意别用 `lastIndexOf('}')` 当验收——加固版 jsonRepair.ts 自己内部就有这个模式，那两处属实现本体，不动）。
- [ ] 新增测试 + 现有归因/趋势测试全绿；三绿。
- [ ] 不需要记 CHANGELOG（无对外行为变化，失败路径从"静默"变"有日志"属内部改善）。
