# MemoWeft 2.0 产品纵向路线

Status: active product route

Supersedes: `docs/next/execution-plan-and-migration.md`. The former Stage/Gate
route and its migration estimates are retired and non-normative; Git history
retains them only as historical context.

这份文件是 MemoWeft 2.0 的唯一实施路线。开发按 Owner 能在原控制台直接感知的纵向行为推进，不按 extractor、resolver、SQLite、evolution、recall 等横向组件推进，也不使用 Gate 编号、固定语料运行次数或工程候选替代真实产品验收。

## 产品交付单位

每次只交付一个 Owner 能直接感知的行为。每个能力块分别报告三种证据：

1. **组件测试**：局部合同能工作。
2. **真实主链路集成**：原控制台、真实模型、结构化解释、候选、Owner 选择和 SQLite 已接通。
3. **Owner dogfood**：Owner 使用开发者未知的自由措辞验证并明确接受。

前两条全绿也不能宣布产品能力完成。Owner 未接受当前能力块时，不开始下一块。

## 全链共同不变量

- 当前 user turn 是唯一的新 Evidence；历史 user turn、assistant reply 和 recall 只可作为解释上下文。
- 模型只提出结构化语言解释，不能直接决定稳定身份、Evidence 权限、perspective、accepted mutation 或 SQLite 主键。
- 程序验证原文 span、世界归属、身份候选、claim kind、角色、perspective、provenance 和能力块边界。
- Candidate 必须让 Owner 看见焦点实体、所有待处理 claims、每条原话依据、陈述类型、看法持有者及不确定点。
- Owner 对每条 claim 独立选择接受或拒绝，也可以把不确定项标记为需要澄清；只有接受的 claims 进入 accepted world。reject、failed、no-candidate 和 clarification-required 均不得改变 accepted world。
- query 纯读取，不形成 Candidate。
- 无法唯一绑定时澄清，不依据模型分数静默猜测，也不把回指短语自动建成新实体。
- Entity 类型开放；person、animal、object、place、organization、project 等共享同一 mention/identity 协议，类型只作为约束。Event 是独立的一等世界对象，也可以成为 claim 的语义目标，但不因此变成 Entity subtype。
- `world owner`、`focus entity`、`claim subject/object/value`、`perspective holder` 和 `Evidence provenance` 始终分开。

## 模型与程序的边界

模型输出受限的 `TurnMeaningProposal`：

```text
act: query | assertion | mixed | correction | clarification_answer | other

mentions[]:
  mention_handle
  exact_span
  mode: introduce | refer
  optional_kind_hint
  proposed_accepted_handles[]

claims[]:
  claim_handle
  kind: naming | alias | attribute | relationship | event | evaluation
  subject_mention_handle
  optional_object_mention_handle
  optional_value_span
  exact_claim_span
  optional_time_span
  optional_correction_of_claim_handle
```

程序逐项验证：

- mention、claim、value、time span 必须在当前 user Evidence 中逐字匹配；索引错误时只允许原文唯一重建坐标，不做模糊匹配；
- 每条 claim 的 subject/object/value 角色必须非重叠且符合其 kind；属性值不能被当作焦点实体；
- 模型只能选择调用方提供的 opaque accepted handle，不能生成 Entity ID；
- refer mention 只能得到 `bound`、`ambiguous` 或 `unresolved`；introduce 只能形成新 Entity Candidate；
- perspective 由程序依据 Evidence 角色和产品合同确定，evaluation 默认是 Owner perspective；
- correction 必须明确指向既有 claim，且只改变 Owner 选择的 claim 结果；原始 Evidence 保留；
- 编译后的 WorldDelta 不含模型未被授权创建的对象；
- Candidate hash 覆盖 focus entity、mentions、claims、identity bindings、Evidence spans、Owner selections 和 base revision。

## 能力块 1：自然谈论一个焦点对象，并在实体详情看清系统理解

### Owner 感知行为

Owner 可以任选一个现实中的人、动物、物体、地点、组织、项目或其他持久对象，用自己的说法介绍、描述、评价或补充经历。系统应把本轮自然话语中的 mentions[] 和 claims[] 编译成一个以焦点对象为中心的可读详情页/候选卡：哪些名字和别名、哪些属性、与谁有什么关系、发生过什么事件、Owner 如何评价，以及每条理解依据哪段原话。Owner 可以逐条接受或拒绝；接受后，新会话能从 accepted world 读取已接受内容。

### 本块必须支持

- 通用 mentions[] 与 claims[]，不按人/动物/项目等词语打补丁；
- naming/alias、attribute、relationship、event、evaluation 五类陈述分别展示、分别验证、分别编译；
- Owner perspective，尤其是主观 evaluation；
- 明确 correction：纠正既有 claim 的理解，保留原 Evidence，不能把纠正误写成新实体或无视角事实；
- claim 级 Owner accept/reject，并能检测和显示 clarification-required；本块不恢复被暂停的原 assertion；
- accepted identity binding、Evidence、claims 和 world revision 一次原子提交；
- 关闭重开 SQLite 后焦点实体详情和已接受 claims 仍一致。

### 本块拒绝或暂停

- query：零候选、零写入；
- ambiguous/unresolved referent：返回 clarification-required，暂停当前 assertion，零候选、零写入；
- 未能验证角色、span、Evidence 或 correction target：对应 claim fail-closed，不降级成别的 kind；
- Owner 未接受的 claim：不得进入 accepted world；
- 新的召回排序、图遍历和 reconstruction：保持暂停。

### 必须复用

- `MemoryLoop` 的 review/result hash/base revision/accept/reject 与 SQLite transaction；
- accepted world、Evidence ledger 与 identity authority 的稳定 binding、重启恢复和 fail-closed 原则；
- `EntityReferenceResolver` 的 resolved/ambiguous/unresolved 结果语义；
- Node bridge 的 operation idempotency 与 current-user-only Evidence 边界；
- 现有实体详情/图投影可复用为展示底座，但不得把投影当 authority。

### 必须重做的生产接线

- 生产入口必须接收完整 mentions[]/claims[]，删除单 mention、单 statement、attribute-only 的产品边界；
- 不再把 `_third_party_reference_context()` 的 person/animal 分支当通用入口；
- 在 WorldDelta 前增加 claims 级程序 verifier/compiler；
- introduction、refer、alias、relationship、event、evaluation 和 correction 共用稳定身份绑定与 Evidence 约束；
- 实体详情必须逐 claim 展示 Owner 可选边界，不能只显示一个总候选；
- ambiguity 必须成为产品可见的 clarification-required，而不是提取异常；
- identity binding、claims、WorldDelta、Evidence 和 revision 必须同一次 Owner 决定原子提交。

### 完成标准

- 任意实体类型都能用同一路径形成 focus entity，不依赖词语补丁；
- 一轮包含多个 mentions/claims 时，每条 claim 都能看到正确 subject/object/value/time 角色；
- 五类陈述不会互相降级，evaluation 带 Owner perspective；
- correction 只修正明确选中的既有 claim，原始 Evidence 保留；
- Owner 可逐条接受/拒绝；不确定项明确显示 clarification-required，只有接受项写入；
- reject 后 revision/hash/bindings 不变；accept 后只增加一次 world revision，绑定、claims 与 Evidence 同时可见；
- 关闭重开 SQLite 后实体详情一致；
- query 不形成 Candidate；ambiguity 不自动选、不新建实体；
- 原控制台真实主链路通过；
- Owner 使用未知自由措辞明确接受。

### Owner 测试任务

开发完成后只向 Owner 提供任务，不提供固定复测原句：

> 在原控制台中任选一个你愿意谈的现实对象，用自己的方式连续聊几句：可以提到它叫什么、你怎么称呼它、它有什么属性、和谁有什么关系、发生过什么事，以及你怎么看它。观察实体详情是否把每条理解分开，并显示原话依据和 Owner perspective；逐条接受或拒绝其中一些，再纠正一条你认为理解错的内容。然后开启新会话，用另一种说法查询已接受内容。也可以故意制造两个都可能被指到的对象，观察系统是否说明不确定而不是乱选。

## 能力块 2：不确定时会问我

把能力块 1 已能检测和展示的 `clarification-required` 接成真正的聊天回合：展示少量可理解候选，Owner 选择或补充描述后，系统恢复原始 assertion；澄清回答只解决 identity，不替代原始 Evidence，也不自动形成新事实。只有能力块 1 Owner 接受后才开始。

## 能力块 3：关系和经历有自己的生命史

在能力块 1 已能形成和接受最小 Relationship/Event 记录后，扩展它们的生命史：关系具有稳定 ID、端点、事件史、状态、recurring patterns、冲突/修复与不同 perspective；第三方不需要亲自参加 session。

## 能力块 4：从个人世界重新进入记忆

在上游能力块均被 Owner 接受后，才继续 reconstruction/recall。回答必须来自 accepted world 的 Entity、Relationship、Event、Cognition、Perspective 与 provenance；Profile 只作为派生视图。届时再评估是否需要 Graphiti/Hindsight 一类底层基础设施，当前不引入双重 authority。

## 旧成果重新分类

- 原 Stage 0：产品语义来源。
- 原 Stage 1：Evidence/WorldDelta/Candidate 组件基础。
- 原 Stage 2：resolver/identity authority 组件基础。
- 原 Stage 3：identity SQLite checkpoint 组件基础。
- 原 Stage 4：evolution 组件基础，未获得真实产品验收。
- 原 Stage 5：reconstruction/recall 组件基础；旧 Gate 5 未接受，不继续扩建。

这些标签只说明可复用资产，不构成产品阶段、授权链或完成结论。

## 删除性规则

以下旧规则不再保留为 archive，也不得在后续窗口恢复：

- 强制读取 `PROJECT_ANCHOR.md`；
- 每次回复必须输出工作锚点；
- 以 Stage/Gate 编号组织当前开发；
- 固定南京语料、固定模型运行次数和不可变候选仪式；
- 单 mention、单 statement、attribute-only 作为能力块 1 的产品边界；
- 组件 Gate 接受自动授权下一个产品层；
- 把真实聊天接线推迟到后期集成阶段；
- 自动测试或工程候选替代 Owner dogfood；
- 在能力块 1 未被 Owner 接受前继续召回下游。
