# MemoWeft 2.0 — Personal Memory World

Status: active product semantics

## Core thesis

> The user is the coordinate origin of the memory world, not the grammatical subject of every memory.

一条记忆属于某个人的世界，是因为它与这个人的生活相关；它的语义目标仍然可以是用户本人、第三方人物、动物、物体、地点、组织、项目、关系、事件、AI 参与者或其他持久对象。

## 1. World ownership、target、perspective、provenance 相互独立

- `world_id`：这是谁的个人世界。
- `target`：这条陈述在说谁或什么。
- `perspective`：谁持有这个认识、评价或解释。
- `provenance`：系统凭什么保存它，具体来自哪段 Evidence。

“我妈妈很温柔”的规范表达不是把整句压成“用户说妈妈温柔”，而是：

```text
world: Owner
target: Mother
statement: 温柔
perspective: Owner
evidence: 当前用户原话的精确 span
```

## 2. Entity 是开放类型的稳定对象

Entity 使用稳定 ID。常见类型包括但不限于：

- person；
- animal；
- object；
- place；
- organization；
- project；
- agent；
- activity；
- 其他可长期指代的对象。

自然语言 mention 不是 Entity ID。“这个项目”“她”“那里”“上次那家公司”都必须先解析到一个已接受 Entity，或触发澄清；不能把回指短语直接存成新实体名。

所有类型共享同一 mention → identity 协议。不得为“女生、男生、宠物、项目、公司”等分别维护产品级 resolver 补丁。

## 3. 五种陈述不能混装

### Naming / alias

说明对象叫什么或某个称呼确实指向它。命名只改变身份表面，不证明其他属性。

### Attribute

描述一个对象的相对稳定属性或状态，例如年龄、颜色、所在地、技术栈或负责人。Attribute 以既有 Entity 为 target，不创建同名新 Entity。

### Relationship

连接两个或更多稳定对象。Relationship 具有自己的 ID，不只是两个 Entity 之间的一条无历史边。

### Event

描述在时间中发生的经历，保存参与者、相关对象、时间、叙事摘要和以后查询所需的语义 facets。

### Evaluation

表达某个 perspective holder 对 target 的主观认识、判断或感受。

例如“那就是个无赖的人”应当：

- 先把“那”绑定到既有 Entity；
- 形成指向该 Entity 的 evaluation；
- perspective holder 是 Owner；
- provenance 是当前 user Evidence。

它不应成为：

- 一个叫“无赖的人”的新 Entity；
- 既有 Entity 的 alias；
- 无 perspective 的客观事实；
- 模型自行产生的推断。

## 4. Relationship 是一等记忆对象

Relationship 可以拥有：

- 稳定 ID 与端点；
- shared events；
- 当前状态及状态历史；
- recurring patterns；
- conflicts and repairs；
- relationship-targeted cognition；
- 不同主体的 perspective。

第三方不需要参加聊天 session 才能进入 Owner 的个人世界。参与会话和成为世界中的被观察对象是两回事。

## 5. Event 是重新进入经历的锚点

Event 保留叙事表达，同时留下可查询结构。以后询问“为什么我们当时吵起来”时，系统应能从 accepted world 恢复事件、参与者、关系、相关地点或项目、各自立场、原因和 provenance，而不是依赖原聊天 transcript 猜测。

## 6. AI 可以参与经历，但不能自封为 Evidence authority

AI 可以是 Entity、Event participant 或解释的提出者。assistant text 可以帮助理解当前 turn，但不能仅因 assistant 说过就成为用户世界事实。

只有得到用户 Evidence 支撑并经过 Owner 接受的陈述，才能进入 accepted world。

## 7. Profile 是派生视图

User Profile 从个人世界派生，不是所有记忆的目标桶，也不是事实 authority。

第三方事实、项目状态或地点经历可以改变世界中相应区域，而不必改变 User Profile。Profile 应可由 accepted world 重建。

## 8. 世界结构和信念依据保持分离

逻辑上保持两张图：

```text
World graph
  Entity
  Relationship
  Event
  semantic target links

Provenance graph
  Evidence
  support / contradict
  Cognition / Statement
  perspective
  derivation / correction history
```

UI 可以合并展示，但存储语义不能把“世界中有什么”与“为什么相信它”混为一谈。

## 9. 形成和写入合同

真实聊天中的完整形成顺序是：

```text
current user Evidence
→ model meaning proposal
→ program span/identity/statement/perspective validation
→ Owner-readable Candidate
→ explicit accept/reject
→ atomic accepted-world mutation
```

模型只提出结构化解释。程序拥有以下决定权：

- Evidence 是否合格；
- mention 是否能绑定；
- 是否 ambiguous/unresolved；
- statement kind；
- perspective；
- Candidate 边界；
- accepted-world mutation。

reject、failed、no-candidate 和 clarification-required 均不得改变 accepted world。

## 10. Query 与 clarification

- 纯查询是只读行为，不形成 Candidate。
- mixed turn 只允许 assertion span 支撑 Candidate。
- 多个 Entity 都合理时必须澄清，不得用最高模型分数代替确定身份。
- 明显是回指但没有安全候选时必须询问所指对象，不得自动新建 Entity。
- clarification answer 只解决 pending identity；它不替代原始 assertion Evidence，也不凭空增加事实。

## 11. 产品验收

组件测试证明局部合同，主链路集成证明代码接通，Owner dogfood 证明真实产品行为。三者分开报告。

每个能力块必须由 Owner 在原控制台使用开发者未知的自由措辞验收；固定样例、模型重复运行、工程候选、SQLite 行数或自动测试均不能替代 Owner 接受。
