# T3 · temperature 可配 + reasoning 解析兼容

> 接更多模型：放开生成温度、让响应解析扛得住 reasoning 模型的思考段。**波2，可与 T2 并行（注意 `client.ts` 分区）。**

## 背景

- **temperature 写死**：`client.ts:90` body 里 `temperature: 0.3` 是唯一处；`LLMConfig`（:20-24）只有 `baseUrl/apiKey/model`，无 temperature 位；env 也没这键。想配得先开位。用途池 `pool.ts:14` 已分 `'chat' | 'write'`，且 write 有独立 env 前缀 `MEMOWEFT_WRITE_LLM_*`（:24）——**天然可按用途分别配**。
- **响应只取 content**：`client.ts:104-107` 只读 `choices[0].message.content`，不理 `reasoning_content`、不剥 `<think>`。
- **解析脆弱**：`jsonRepair.ts:24-30` `extractJsonObject` 贪婪取 `indexOf('{')`..`lastIndexOf('}')`。若 reasoning 模型把 `<think>…</think>` 混进 `content` 且思考里含花括号（常见：思考里写伪代码 `{type:...}`），会截出「思考里的半个花括号 + 真 JSON」→ `JSON.parse` 失败 → 写路径当「本轮无产出」。写路径 `consolidate/attribute/trends` 全靠这条出 JSON。
- **隔离已亲验**：`confidence.ts:13-18` 的 `ConfidenceInputs` 只吃 `{contentType, formedBy, supportCount, contradictCount}`，**无 LLM 自报、无 temperature 入口**。放开 temperature 碰不到置信度。

## 作者已拍板（本卡相关）

- **B8 = temperature 落地**：`LLMConfig` 加 `temperature?`，`loadLLMConfig` 从 env 读，body 用 `?? 0.3` 保缺省——**不配 = 全 0.3，零行为变更**。按 `LLMPurpose` 可分别配；**write 不设更低缺省**（保持 0.3）。键名 `MEMOWEFT_LLM_TEMPERATURE` / `MEMOWEFT_WRITE_LLM_TEMPERATURE`，沿双前缀。
- **B9 = reasoning 兼容**：client 取 content 后剥 `<think>…</think>` 为主守（**只剥有闭合 `</think>` 的成对标签；无闭合不动**）；`extractJsonObject` 改「括号配平扫描」为兜底；`reasoning_content` 字段忽略即可。

## 改哪里

**(1) temperature 可配**

1. `client.ts`：`LLMConfig`（:20-24）加 `temperature?: number`；`loadLLMConfig`（:48-61）用 `readEnvWithFallback` 读 `*_TEMPERATURE`（双前缀兼容）；body（:90）改 `temperature: this.config.temperature ?? 0.3`。
2. `pool.ts`：`loadLLMPool` 给 `chat`（`LLM` 前缀）/ `write`（`WRITE_LLM` 前缀）各自 `loadLLMConfig` 时带各自 temperature——write 已有独立前缀，自然分。
3. **红线**：temperature 只塞进 client 请求体，**不作为参数传入任何 `consolidation/cognition` 函数**。
4. 契约：temperature 落 `LLMConfig`（`index.ts:118` 标 **[experimental]**），加字段无契约负担；**不进 `MemoWeftConfig`**（故不碰 [stable] 契约、不碰 `config.ts`）。（日后要「内存热调」再 additive 加到 `MemoWeftConfig`，那时走契约同步。）

**(2) reasoning 解析兼容**

5. `client.ts`：`chat()` 取 `content` 后，剥 `<think>…</think>`——**正则只匹配成对闭合标签**（如 `/<think>[\s\S]*?<\/think>/gi`）；**无闭合 `</think>` 时不剥**（防把真答案剥掉）。可选：若模型把思考放独立 `reasoning_content` 字段，忽略即可（对 JSON 无害）。
6. `jsonRepair.ts`：`extractJsonObject`（:24-30）改「括号配平扫描」——从首个 `{` 起做 depth 计数，取**第一个平衡闭合**的对象（比贪婪 `lastIndexOf('}')` 更抗污染）作兜底。**`parseJsonObject`「只认对象」纪律（:33-42）不动。**
7. 新增单测（放 `tests/`）：带 `<think>`（含花括号）前缀的响应能正确抠出 JSON；`parseJsonObjectWithRepair` 重试路径也剥干净；**无闭合 `</think>` 的畸形响应不被误剥**（真答案保住）。

## 不许动

- `confidence.ts` 及任何置信度 / 认知判定；temperature 不流入 `consolidation`。
- `parseJsonObject`「只认对象、数组/标量/null 判非法」纪律——加固是「抠得更准」不是「抠得更松」。
- `DLA_*` 回退与 `./dla.db`；不引任何库（正则剥离 + 字符串扫描即可）。

## 验收（可执行核对）

- [ ] 三绿；离线 eval 全绿。
- [ ] 不配 temperature 时行为 = 旧（body 仍 `temperature: 0.3`）。
- [ ] `MEMOWEFT_LLM_TEMPERATURE` / `MEMOWEFT_WRITE_LLM_TEMPERATURE`（及 `DLA_*` 旧名）能分别改 chat/write 温度。
- [ ] `temperature` 不出现在 `confidence`/`consolidation` 任何入参（`grep -rn temperature src/consolidation src/cognition` 零命中）。
- [ ] 新增 `<think>` 单测全过（含**无闭合不误剥**）。
- [ ] reasoning 真兼容标注为**在线验证**（需目标模型真实响应样本，剥离正则覆盖其变体）——不赌离线断言。
- [ ] runtime `dependencies` 仍 `{}`。

## 与其它卡的关系

- 与 T1 争 `jsonRepair.ts`（T1 改 `JSON_ONLY_NUDGE`、本卡改 `extractJsonObject`，不同区块）。
- 与 T2 争 `client.ts`（T2 改 `throw` 报错、本卡改 temperature + 剥 think，不同区块）。
- **不碰 `config.ts`**（temperature 走 `LLMConfig`+env）——与 T1/T4 在 config.ts 上无冲突。

## 发现待办

- B 探测备注：`reasoning_content` 与 `<think>` 的真实返回形态取决于要接的目标模型，剥离正则要覆盖哪些变体需拿真响应样本定（属在线验证环节）。
