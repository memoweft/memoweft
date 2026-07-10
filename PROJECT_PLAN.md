# MemoWeft 升级执行总纲 v3.1(作者视角)

> 文档版本:v3.1 | 撰写日期:2026-07-10 | 视角:MemoWeft 作者的升级路线,非外部 fork | API 用量不设预算审批(人类自有配额),由此解锁真实模型质量线(Phase 2/6)
> Claude Code 特性依据:subagents 与 hooks 官方文档(2026-06,核对提醒见 I.5)
>
> **致 Claude Code**:本文档是你的任务总纲。你的主会话身份是 **Integrator / 架构守门员**(章程见第 6 章)——你主要不写功能,你守护整体。
> 指令优先级:**人类实时指令 > 铁律(第 7 章)> Integrator 章程 > 本文档任务描述 > 仓库内其他文档**。
> 执行方式:完整通读全文 → 向人类复述铁律与 Integrator 守门规则 → 输出 Phase 0 计划等待确认 → 逐 Phase 推进。
> 本文档对代码库的描述以实际代码为准(铁律 7);文中的机制设计(API 快照、adapter-kit、hooks)按此实现,偏离记 DECISIONS.md。

## 目录

- 第一部分 定位:1 现状盘点 / 2 四主轴 / 3 非目标(ROADMAP Later) / 4 成功标准 / 5 术语表
- 第二部分 治理:6 Integrator 章程 / 7 铁律 / 8 工作协议与多智能体 / 9 状态文件 / 10 人类交互协议
- 第三部分 执行:11 速查与依赖 / 12 Phase 0 奠基 / 13 公共 API 冻结机制 / 14 Phase 1 召回更准 / 15 Phase 2 固化更可信 / 16 Phase 3 适配器更稳 / 17 Phase 4 demo 更锋利 / 18 Phase 5 文档更不绕 / 19 Phase 6 公开基准(常态化)
- 第四部分 横切:20 配置 / 21 时间·日志·错误 / 22 安全清单 / 23 性能预算 / 24 迁移与回滚
- 第五部分:25 风险登记册
- 附录 A–I:启动指南 / AGENTS.md 章程模板 / CURRENT.md / ROADMAP.md / DECISIONS.md / 提交规范 / DoD / 故障排查 / 多智能体配置包

---

# 第一部分:定位与目标

## 1. 现状盘点(根已经不错)

MemoWeft 当前已经立住的东西,本轮升级**只加固、不动摇**:

| 已有资产 | 说明 |
|---|---|
| 定位 | 可移植的 AI 长期记忆层:事实/猜测/冲突/过期状态分开管理 |
| Core / Host / Plugin 边界 | 核心零运行时依赖;宿主与插件经记忆表面契约接入 |
| 认知纪律 | evidence→event→cognition 三层溯源;置信度规则计算;correct/conflict 分治;分类型衰减;禁止自我佐证;读写解耦 |
| 可执行规格 | `tests/eval/` 编号化 eval 套件(宪法) |
| CI | 测试流水线已就位 |
| 适配器 | MCP server、Vercel AI SDK |
| demo | 参考宿主,含无 key 模式 |
| 工程纪律 | AGENTS.md 已强调:不扩大任务范围;改 API/schema/权限模型前先说明影响面;认知纪律不能顺手优化掉 |

## 2. 本轮升级四主轴

接下来最该补的不是宏大架构,而是:

1. **召回更准**(Phase 1):BM25+向量混合召回、RRF 融合、增量索引、双臂(确定性/真实)黄金集评测
2. **适配器更稳**(Phase 3):契约测试套件化、故障注入与降级语义、peer 版本矩阵 CI、注入格式锁定
3. **demo 更锋利**(Phase 4):四幕叙事直击认知纪律的价值,clone 一条命令、无 key、确定性复现
4. **文档更不绕**(Phase 5):README 60 秒电梯稿、每页一个任务、片段可执行验证、死链清零

**解锁线 · 固化更可信**(Phase 2):API 不设预算约束后新增的第五条线——用真实模型持续度量最脆弱的写路径:场景语料库、两级固化质量评测(结构断言 + LLM-as-judge)、提示词回归、nightly live。公开基准(Phase 6)同理由「可选」转为常态化。

## 3. 非目标(本轮明确不做;全部收进 ROADMAP.md 的 Later 区,想法不丢弃)

- Python 移植与跨语言 parity
- REST server、多租户、pgvector / Postgres 后端
- 托管 SaaS、Web 管理界面、多模态证据、CRDT 同步
- 大规模新增适配器(本轮只加固现有两个,至多新增一个作为契约套件的试金石)

判据:任何触及以上范围的想法 → 一行字进 ROADMAP Later,不动手(铁律 4)。

## 4. 成功标准(全部满足才算本轮完成)

- [ ] 全量 eval 100% 绿贯穿始终;公共 API 快照零未批准变更
- [ ] 黄金检索集 Recall@5 相对基线 +10%;中文用例组单独达标;P95 延迟劣化 ≤20%;增量索引更新耗时与变更量成正比(1 万条验证)
- [ ] 现有 2 个适配器通过完整 AD-1…AD-6 契约套件;peer 版本矩阵 CI 绿;故障注入下降级路径可证
- [ ] demo:干净环境 clone → 一条命令 → 无 key → <2 分钟走完四幕;两次运行输出 diff 为空(确定性)
- [ ] README 电梯稿就位;文档代码片段抽取验证进 CI 且绿;死链 0;每篇文档通过「单页单任务」自查
- [ ] 固化质量线就位:语料库 ≥30 场景、基线分数报告入库、提示词版本化、nightly live 绿(~~fixtures:refresh 可用~~ → 作废,见 D-0010)
- [ ] 两套公开基准各 ≥1 次完整矩阵成绩,runs 可复现,BENCHMARKS.md 就位

## 5. 术语表

| 术语 | 定义 |
|---|---|
| evidence / event / cognition | 原始证据(只增不改)/ 情境化事件 / 带置信度与状态的判断 |
| conflict / correct | 矛盾并存暴露、不裁决 / 用户明确纠正,旧认知失效不删除 |
| consolidation / recall | 异步固化写路径 / 同步召回读路径 |
| 记忆表面(memory surface) | 宿主与插件可调用的公共接口,由 memory-surface-contract.md 唯一定义 |
| API 快照 | 公共导出面的机读存档,测试逐字比对,防止无意破坏(第 13 章) |
| golden set | 人工固定的「查询→期望认知」检索评测集 |
| RRF | Reciprocal Rank Fusion 多通道排名融合 |
| adapter-kit | 可复用的适配器契约测试工具包(AD-1…AD-6),Phase 3 产物 |
| LLM-as-judge | 用固定提示词、温度 0、三次多数投票的模型判分,保证可复现(15.2) |
| Integrator | 总控 Agent = Claude Code 主会话的角色:守门、分配、审查、合并(第 6 章) |
| subagent / hook | Claude Code 子代理(独立上下文与权限)/ 生命周期钩子(exit 2 拦截),铁律的机器强制层 |

---

# 第二部分:治理

## 6. Integrator 章程(总控 Agent / 架构守门员)

**Integrator 就是 Claude Code 主会话本身**,不是一个 subagent 文件——因为它需要三样 subagent 没有的东西:向人类请求批准的能力、合并与打 tag 的执行权、全量测试的最终裁决权。它主要不写功能,它负责让多智能体不互相踩。没有它,并行就是混乱。

**六项职责**:

1. **维护总计划与状态**:每个工作段落开始读 CURRENT.md,结束时更新 CURRENT.md;Phase 边界更新 ROADMAP.md;每个取舍记 DECISIONS.md。
2. **分配任务**:按第 8.6 章委派规则把任务派给 6 个 subagent,任务书必须写明——目标、文件所有权(白名单)、验收标准、对应本文档章节号。
3. **审查每一份 diff**(亲自或委派 reviewer 后复核结论),按守门清单(见下)逐条过,不过不合。
4. **决定合并顺序**:低风险先合;触碰共享文件的改动独占窗口;一次只合一个,合一个全量验一个。
5. **跑全量测试**:任何合并、任何 tag 之前,eval + 单测 + lint + typecheck + API 快照测试全绿是唯一放行条件。
6. **守护不变量**:公共 API、schema、权限模型、认知纪律——四样东西的任何变更都必须先有影响面说明并经人类批准。

**守门清单(每份 diff 逐条过,任一不过即退回)**:
- [ ] diff 只含任务书声明的文件(范围未扩大)
- [ ] 全量 eval 绿;API 快照测试绿(或附已批准的 D-xxxx 与影响面说明)
- [ ] 认知纪律四点未被触碰:助手输出不入证据 / 置信度只由规则算 / 冲突只暴露不裁决 / 证据 ID 白名单——**"顺手优化"这四点 = 直接退回**
- [ ] 若触及 schema:迁移脚本齐备且幂等(24 章)
- [ ] 提示词 diff 附 15.2 前后分数对比(Phase 2 起生效)
- [ ] DoD(附录 G)全项通过;commit message 合规

**Integrator 的自我约束**:自己动手写代码仅限于——集成胶水、冲突解决、≤20 行的小修;成规模的实现一律委派。这不是效率问题,是守门员不能同时当射手。

## 7. 铁律(违反任何一条:立即停止,记入 CURRENT.md 阻塞区并报告人类)

1. **eval 即宪法**。`tests/eval/` 既有断言语义只增不改;测试不过 = 实现有错。不得修改、跳过、删除断言使其变绿;疑似规格本身有误 → 停止,写明理由,报人类裁决。
2. **API / schema / 权限模型冻结**。三者的任何变更必须:影响面说明(动机 / 破坏性 / 调用方迁移路径)→ 人类批准 → 记 D-xxxx → 才可刷新 API 快照或提交迁移。机器强制见第 13 章。
3. **认知纪律不可顺手优化掉**,对所有改动生效:(a) 助手输出永不成为证据;(b) 置信度只由规则计算,不采信 LLM 自报;(c) conflict 只暴露不裁决(用户显式裁决除外);(d) 证据 ID 白名单校验。任何新代码路径必须有覆盖这四点的测试。
4. **不扩大任务范围**。严格按任务书的文件白名单与目标执行;临时想法一律进 ROADMAP.md 的 Later/Ideas 区,不动手。
5. **先计划后动手**。每个 Phase 开工前计划经人类确认;Phase 内子任务自主。
6. **绿色提交**。commit 前全量测试通过(`SKIP_LIVE_LLM=1` 标记的用例在无 key 环境跳过);小步提交,一个子任务 1–3 个 commit。
7. **代码为准**。本文档与实际代码不符时:以代码为准 → 更新 CURRENT.md 备注 → 若影响任务设计,更新计划并告知人类 → 继续。
8. **发布、删除、许可证变更必须人类批准并由人类执行**:npm publish、对外发帖、删除性/不可逆操作、LICENSE 改动。API 调用不设费用审批(人类已确认自有配额);评测脚本记录实际用量仅供参考。
9. **不越权**。不创建账号;密钥只经环境变量,严禁出现在代码、日志、commit、夹具中;不修改系统级配置。

## 8. 工作协议与多智能体

**8.1 分支与标签**:trunk-based,main 小步提交;Phase 边界打 tag `phase-N-start` / `phase-N-done`;实验性大改开短命分支,完成即由 Integrator 合回。

**8.2 子任务标准循环**:Integrator 写任务书 → 委派(或自做小修)→ 先测试(红)→ 实现(绿)→ reviewer 审查 → Integrator 过守门清单 → 全量测试 → 更新 CURRENT.md → commit。

**8.3 质量门禁**:全量测试、lint、typecheck、API 快照测试全绿;新增逻辑有测试;公共行为变更同步文档与 CHANGELOG;无密钥/调试残留;message 合规(附录 F)。

**8.4 卡壳协议**:同一问题尝试 3 次未解 → CURRENT.md 阻塞区记录(现象/已尝试/假设/需要什么)→ 切换到不依赖它的任务或请示人类。

**8.5 Phase 收尾**:逐条核对验收 → CURRENT.md 记证据 → 打 tag → 用第 10 章话术请人类验收 → 更新 ROADMAP.md。

**8.6 多智能体执行模式(配置见附录 I)**

固定 **6 个项目级 subagent**:scout / test-author / implementer / reviewer / doc-writer / bench-runner,最小权限 + 角色级写入限制(hooks 按 agent_type 强制,I.2)。规则:

1. **委派原则**:重探索、重输出、可独立验收的任务下放;紧耦合多轮改动、以及一切铁律 8 事项留在 Integrator——**subagent 运行中无法向人类请求批准**,这是产品限制。
2. **职责分离即防线**:写实现的(implementer)不写测试断言;写测试的(test-author)不碰 src/;写文档的(doc-writer)不碰代码;审查的(reviewer)只读。铁律 1/3 由此获得组织级保障,hooks 再做机器级兜底。
3. **并行三规则**:并行写任务同时 ≤3;任务书显式声明文件所有权且互不相交;同文件耦合改动永不并行。
4. **提醒与保证分离**:本文档与 AGENTS.md 是提醒,hooks 是保证——对主会话与全部 subagent 的每次工具调用一视同仁。
5. **Phase 级大并行**(互不相交目录)用 git worktree 多实例(I.4);Agent Teams 实验特性 token 成本数倍,默认不用(I.5)。

## 9. 状态文件(仓库根目录,Integrator 维护)

| 文件 | 用途 | 更新时机 | 模板 |
|---|---|---|---|
| CURRENT.md | 当前状态:在做什么 / 刚完成 / 阻塞 / 下一步 | 每个工作段落结束、每次合并后 | 附录 C |
| ROADMAP.md | Now / Next / Later 三段;Later 收纳被砍的宏大架构与一切新想法 | Phase 边界、每次砍需求 | 附录 D |
| DECISIONS.md | ADR-lite:每个有争议取舍一条 | 每次决策 | 附录 E |
| CHANGELOG.md | Keep-a-Changelog,面向使用者 | 每个用户可见变更 | — |

必须记 DECISIONS 的时机:偏离本文档设计;两个合理方案取舍;修改默认参数;任何 API/schema/权限模型变更(附影响面说明)。

## 10. 人类交互协议

Integrator 主动找人类的情形:铁律 8 事项;铁律 1/2 的规格疑义;Phase 计划确认与验收;CURRENT.md 阻塞区积压 ≥3 条。
提问格式:一句话背景 + 2–3 个选项(各一行利弊)+ 推荐与理由。
人类验收话术(直接粘贴):
> 对照 PROJECT_PLAN.md Phase N 验收标准逐条自检,输出核对表,每条附证据(命令 + 输出摘要或文件路径)。任一不满足,先说明原因与补救计划,不要进入下一 Phase。

---

# 第三部分:执行计划

## 11. Phase 速查表与依赖关系

| Phase | 主轴 | 预计 | 前置 | 关键产出 |
|---|---|---|---|---|
| 0 | 奠基:基线 / API 快照 / 配置包 / 章程 | 0.5–1 天 | — | 快照测试、.claude/、CURRENT/ROADMAP、AGENTS.md 升级 |
| 1 | 召回更准 | 3–5 天 | 0 | hybrid recall、增量索引、双臂基准报告 |
| 2 | 固化更可信(真实模型质量线) | 3–5 天 | 0(建议 1 之后,夹具一次刷新到位) | 场景语料库、固化评测器、提示词回归、nightly live |
| 3 | 适配器更稳 | 3–5 天 | 1(注入格式依赖新召回输出) | adapter-kit、矩阵 CI、故障注入 |
| 4 | demo 更锋利 | 2–3 天 | 1、2(召回与夹具就绪) | 四幕 demo、确定性运行、录屏脚本 |
| 5 | 文档更不绕 | 2–4 天 | 与 4 并行(文件不相交) | README 电梯稿、docs 重排、片段验证 CI |
| 6 | 公开基准(常态化) | 首轮 3–4 天 | 1、2 | 两套基准 runs、BENCHMARKS.md |

推荐节奏:**P0 → P1 → P2 → P3 → (P4 ∥ P5) → P6,此后 P6 随每个 Phase 收尾常态运行**。P4 与 P5 是最佳并行点;P2 内部 15.1 ∥ 15.2 亦可并行。

---

## 12. Phase 0:奠基

**目标**:基线可复现、公共 API 有机器防线、多智能体配置就位、治理文件落地。作者视角下不需要考古式侦察,但 Claude Code 仍需**校准**——用代码核实本文档引用的每个事实。

### 12.1 基线验证(命令级,输出证据记入 CURRENT.md)

```bash
cd <memoweft 仓库>
git tag phase-0-start
node --version                              # 记录;确认与 package.json engines 一致
npm install && npm test 2>&1 | tee /tmp/baseline-test.log   # 必须全绿;失败先修环境不改代码
# 验证 FTS5(Phase 1 依赖):
node -e "const{DatabaseSync}=require('node:sqlite');const db=new DatabaseSync(':memory:');db.exec(\"CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')\");console.log('FTS5+trigram OK')"
test -n "$ANTHROPIC_API_KEY" && echo "LLM key OK"   # live 套件与固化评测(Phase 2/6)直接使用
# 跑无 key demo(脚本名以 package.json 为准),确认可运行
```
FTS5 或 node:sqlite 不满足 → 降级链(better-sqlite3 → 纯 TS BM25),结论记 DECISIONS.md(D-0001)。

### 12.2 校准清单(产出:核对报告,追加进 docs/ 下现有架构文档或 CURRENT.md 备注)
用 scout 三路并行核实并记录 文件:行号:
1. 置信度规则实际值:各来源底分、佐证/反证加减、各类型半衰期
2. 读/写路径时序与现有检索实现(向量存储位置、相似度计算、索引重建触发点)
3. 记忆表面现状:两个适配器实际调用的公共 API 清单(为第 13 章快照与 Phase 2 做输入)
与本文档描述不符处单独成节(铁律 7)。

### 12.3 建立公共 API 快照(第 13 章机制,本 Phase 落地)
- 实现 `scripts/api-snapshot.mjs` 与 `tests/api/api-freeze.test.*`;生成首版 `tests/api/api-surface.snapshot`。
- 快照建立后立即演练一次变更流程:临时加一个导出 → 测试红 → 回滚 → 记录演练结果。

### 12.4 落地多智能体配置包(.claude/,内容见附录 I)
- 创建 6 个子代理定义、settings.json、hooks/protect.py;全部提交入仓库。
- 提醒人类**重启一次会话**(subagent 定义会话启动时加载)。
- **实测验证 hooks(通过前禁止进入 Phase 1)**:(a) Edit 一个 tests/eval/ 既有文件 → 被拦截;(b) 运行含 `npm publish` 字样的命令 → 被拦截;两条拦截原文记 CURRENT.md。

### 12.5 治理文件落地
- 将 AGENTS.md 升级为 Integrator 章程(附录 B 模板;保留其原有三条纪律并扩展,git diff 供人类过目)。
- CLAUDE.md 精简为入口:常用命令 + 指向 AGENTS.md 与本文档。
- 初始化 CURRENT.md / ROADMAP.md(把第 3 章非目标逐条搬进 Later)/ DECISIONS.md / CHANGELOG.md(若缺)。

### 12.6 CI 补强
现有 CI 上追加:API 快照测试、lint/typecheck(若缺)、`SKIP_LIVE_LLM=1` 环境变量;需真实 key 的用例统一标记跳过;另建 nightly 工作流骨架(schedule 触发,Phase 2 的 test:live 接入后启用)。

**Phase 0 验收**:
- [ ] 基线全绿日志入 CURRENT.md;FTS5 结论记 D-0001
- [ ] 校准报告完成,数值有 文件:行号 出处;与文档不符处已列明
- [ ] API 快照 + 冻结测试就位,变更流程演练记录在案
- [ ] .claude/ 配置包就位,两条 hook 拦截实测记录在案
- [ ] AGENTS.md 升级版经人类过目;四个状态文件就位;CI 补强绿
- [ ] 打 tag `phase-0-done`

---

## 13. 公共 API 冻结机制(铁律 2 的机器强制)

**对象**:核心包的公共导出面。`docs/memory-surface-contract.md` 是人读的唯一事实源;API 快照是机器读的比对基准。两者必须同步变更。

**机制(零新增依赖,≤100 行)**:
- `scripts/api-snapshot.mjs`:运行 `tsc --emitDeclarationOnly` 于临时目录 → 取公共出口的 `.d.ts` → 规范化(剥离注释、统一空白、按符号名排序)→ 写入 `tests/api/api-surface.snapshot`。
- `tests/api/api-freeze.test`:重新生成并与快照**逐字比对**,不一致即红,错误信息打印 diff 与本章变更流程提示。
- `npm run api:update`:刷新快照,并向终端打印警示:「公共 API 变更——需要影响面说明 + 人类批准 + D-xxxx,三者缺一不可」。

**变更流程**:影响面说明(动机 / 是否破坏 / 调用方与两个适配器的迁移路径)→ 人类批准 → 记 D-xxxx → 同一 commit 内:代码 + 刷新的快照 + 更新的 memory-surface-contract.md + CHANGELOG 条目。守门清单据此放行。

**注意**:本机制只覆盖 TypeScript 类型面;运行时行为契约由 eval、Phase 2 的固化质量评测(15.2)与 Phase 3 的注入格式快照(16.4)共同覆盖,合起来才是完整防线。

---

## 14. Phase 1:召回更准

**目标**:把「SQLite 存向量 + JS 余弦全扫 + 全量重建索引」升级为「BM25 + 向量混合召回 + RRF 融合 + 增量索引」,且提升可量化。
**原则:先测量后优化;没有基线数字,任何优化不许合入。**

### 14.1 确定性测试 embedder(先于一切)
embedder 保持注入式接口。新增测试专用 `HashEmbedder`:对文本分词后按 token 哈希映射到固定维度向量并归一化——**完全确定、零网络、零成本**。golden set、demo、CI 全部用它;真实 embedder 作为第二测量臂(14.2)在本地与 nightly 使用。

### 14.2 黄金检索集与基线
- `tests/retrieval/golden.json`:50–100 条,结构:
```json
{ "cases": [ { "id": "G-001", "query": "用户的饮食限制", "expect": ["cog_012"], "kind": "direct" } ] }
```
- 三类查询按约 4:4:2 配比:direct(直指关键词)/ paraphrase(同义改写,考验向量通道)/ multihop(需组合两条认知);每条目标认知至少被 1 条查询覆盖;含 ≥10 条中文用例(CJK 检索验证)。
- **构建方法**:从 demo 种子数据 + eval 场景手工派生;**禁止**用现有向量检索输出反向生成(自我偏置)。
- **双臂测量**:同一 golden set 以 HashEmbedder(确定性臂,CI 回归门槛)与真实 embedder(真实性臂,本地/nightly)各跑一遍,两组数字并列入报告。
- 评测脚本 `bench/eval-retrieval`:Recall@5、MRR@10、P50/P95 延迟、全量索引耗时 → `bench/retrieval-baseline.md`(含环境信息与 commit hash)。基线报告先入库,才允许动优化。

### 14.3 关键词通道(BM25 via FTS5)
```sql
CREATE VIRTUAL TABLE cognition_fts USING fts5(
  cognition_id UNINDEXED,
  text,
  tokenize = 'trigram'
);
```
- tokenizer 默认 `trigram`(SQLite ≥3.34,对中文/CJK 与拼写变体稳健,代价是索引体积);纯英文场景可配 `unicode61`。决策与体积实测记 DECISIONS.md。
- 失效(invalid)与过期认知:查询层过滤或索引删除,选定策略记 DECISIONS.md。
- 暴露 `keywordSearch(query, k)`,与向量检索同签名。降级链:node:sqlite 无 FTS5 → better-sqlite3 → 纯 TS BM25(±200 行,零依赖)。

### 14.4 RRF 融合
```
K_CANDIDATE = 50; RRF_K = 60           // 均可配置
hybrid(query, k):
  vec = vectorSearch(query, K_CANDIDATE)
  kw  = keywordSearch(query, K_CANDIDATE)
  score(d) = Σ_channel 1 / (RRF_K + rank_channel(d))   // 未出现在某通道则该通道不贡献
  return topK(score, k)
```
- recall 主路径切换为 hybrid;保留 `mode: 'vector' | 'keyword' | 'hybrid'` 开关(向后兼容 + 消融)。
- 融合结果仍走既有置信度/隐私过滤,顺序:通道召回 → RRF → 置信度阈值与隐私位过滤 → 截断 k。
- **注意**:mode 开关若进入公共 API,走第 13 章变更流程(这是本轮第一个合法 API 变更,正好演练)。

### 14.5 增量索引
- **方案 A(默认)**:固化事务内同步 upsert 向量表与 FTS 表——SQLite 单写者模型下事务保证一致。
- **方案 B(仅当 A 实测拖慢固化 >20%)**:`index_queue` 脏队列,固化后批量消费。
- 失效/过期 → 索引删除或标记;保留手动 `reindex` 全量重建以自愈。
- 合成 10,000 条认知验证:单条变更的索引更新耗时与变更量成正比;全量 reindex 仍可用,耗时入报告。

### 14.6 消融与收尾
- 同一 golden set 跑 vector-only / keyword-only / hybrid 三臂(双 embedder 臂各一份)→ `bench/retrieval-after.md` 与基线同格式对比。
- 重排序钩子(可选):`Reranker` 接口默认 no-op,不引入依赖;时间紧直接进 ROADMAP Next。

**Phase 1 验收**:
- [ ] 全量 eval 绿;HashEmbedder 下全套检索测试确定可复现
- [ ] Recall@5 ≥ 基线且目标 +10%;中文用例组单独达标;P95 延迟劣化 ≤20%
- [ ] 确定性臂与真实臂两组数字并列入报告;两臂结论分歧(若有)记 DECISIONS.md
- [ ] 1 万条增量索引验证通过,数字入报告;三臂消融入库
- [ ] tokenizer、失效过滤、方案 A/B、mode 开关 API 变更均记 DECISIONS.md;打 tag `phase-1-done`

---

## 15. Phase 2:固化更可信(真实模型驱动的写路径质量线)

**背景**:召回(读路径)在 Phase 1 已被黄金集量化;而项目最脆弱的一环——LLM 驱动的蒸馏与固化(写路径)——此前只被录制夹具「保护」,从未被度量。API 不设预算约束后,这条质量线从奢侈品变成标配。

### 15.1 场景语料库(评测的地基)
- `tests/consolidation-corpus/`:30–50 个对话场景 JSON,每个含:多轮 user/tool 消息;期望结果——应形成的认知(类型 + 要点)、**不应**形成的认知(过度推断清单)、应触发的 correct/conflict、期望被丢弃的虚构证据 ID。
- 覆盖矩阵:六条认知纪律各 ≥4 场景;中文场景 ≥1/3;含「闲聊无信息」负样本(期望零新增认知)。
- 期望值人工撰写(scout 可起草,人类抽审 ≥20%);**禁止**用被测模型自生成期望值。

### 15.2 固化质量评测器
- `bench/eval-consolidation`:逐场景跑真实固化,两级比对:
  - **结构性断言(程序判,先跑)**:认知数量与类型;correct/conflict 是否按期望触发;证据链完整;虚构 ID 被白名单丢弃;置信度落在该类型合理区间。
  - **要点语义匹配(LLM-as-judge,后跑)**:judge 模型可与被测模型不同;judge 提示词入库版本化;温度 0、三次多数投票,判分可复现。
- 产出 `bench/consolidation-baseline.md`:逐场景 pass/fail + 分项得分 + 总分。**先入库基线,再谈优化**(与第 14 章同一原则)。

### 15.3 提示词回归
- 蒸馏/固化提示词集中版本化(位置随现有代码组织,加版本标识与变更注释)。
- 任何提示词改动:必跑 15.2 全量,前后分数对比写进 commit 正文;分数下降需在 DECISIONS.md 说明取舍。轻量流程,不需人类批准——但守门清单相应增补(见第 6 章)。

### 15.4 live 双轨与夹具再生成
- `npm run test:live`:真实模型全量端到端(固化 + 检索真实臂);本地随跑,CI 以 **nightly job** 常态运行(main 分支;失败记入 CURRENT.md)。
- ~~`npm run fixtures:refresh`:由 live 运行一键再生成全部录制夹具;再生成后确定性套件(CI 主干)必须仍绿~~ → **作废,见 D-0010**。本仓库没有 LLM 录制夹具:确定性来自 48 处意图清晰的内联 fake;唯一的 `.db` 夹具明确要求永不重新生成。防漂移由三道已有闸门接管——模型漂移→42 场景固化评测;提示词漂移→`tests/prompts/prompt-hashes.snapshot` 哈希闸门;schema 漂移→冻结的 `memoweft-0.1.0.db`。
- 分工定死:**CI 主干 = 确定性(内联 fake + HashEmbedder,不注入 secrets);nightly + 本地 = 真实模型 = 真实性**。走确定性不是为了省钱,是为了可复现。

### 15.5 多模型健壮性(强化项)
- 固化在 2–3 个模型上各跑 15.2,分差矩阵入报告——度量对特定模型的依赖度,也是 LLM pool 抽象的活体验证;结论(推荐默认模型、已知弱项)记 DECISIONS.md。

**Phase 2 验收**:
- [x] 语料库 ≥30 场景、覆盖矩阵达标、人类抽审通过(42 场景,6 纪律各 7)
- [x] 评测器两级比对可跑,judge 三次判分一致;基线报告入库
- [x] 提示词版本化(8 条收敛到 `src/prompts/registry.ts` + 哈希闸门);`test:live` 可用(~~fixtures:refresh~~ → D-0010)
- [x] nightly live job 上线并首晚绿(2026-07-10 run 29116360383,test:live 通过:腿1 e2e + 腿2 固化 errored=0 + 腿3 大声跳过。首跑曾暴露既有 e2e bug 已修 `800adde`);(强化项)多模型分差矩阵未做 → 进 ROADMAP
- [ ] 打 tag `phase-2-done`(待人类点头)

---

## 16. Phase 3:适配器更稳

**目标**:现有 MCP 与 Vercel AI SDK 两个适配器从「能用」到「可依赖」。本 Phase 以加固为主,不以扩张为主。

### 16.1 契约测试套件化(adapter-kit)
把散落的适配器断言收敛为可复用工具包 `tests/adapter-kit/`,任何适配器(现在两个、将来任意)接入即得完整套件。断言编号写进测试名:
- **AD-1** 助手消息流经适配器后,evidence 表零新增(铁律 3a)
- **AD-2** 用户消息 → 恰好一条 evidence,role=user
- **AD-3** 工具结果 → evidence,source 标记为 tool
- **AD-4** recall 注入内容包含置信度与冲突提示,格式与 16.4 快照一致
- **AD-5** mock LLM 返回虚构 evidenceId 时被白名单丢弃(铁律 3d)
- **AD-6** 记忆层抛错/超时,适配器降级为「无记忆但对话不中断」,以注入 logger 记录
两个现有适配器全部接入 adapter-kit,补齐缺失断言。

### 16.2 故障注入与降级语义
- 测试夹具提供三种故障模式:抛错、超时(可配阈值)、慢响应。
- 在 memory-surface-contract.md 中**明文定义**降级语义:recall 超时上限(建议默认 200ms,可配)、失败后是否重试(建议:读路径不重试直接降级,写路径一次重试)、降级时注入什么(空上下文 + 日志)。语义定义属于契约变更 → 走第 13 章流程。

### 16.3 peer 版本矩阵 CI
- 对 MCP SDK 与 Vercel AI SDK 各取「声明的最低支持版」与「当前最新版」组成矩阵 job;锁定文件分开缓存。
- 矩阵红 = 兼容性破裂,处理结论(升门槛或适配)记 DECISIONS.md。

### 16.4 注入格式快照
- recall 注入宿主上下文的文本格式(含置信度呈现、冲突提示措辞)做 golden 快照测试——防止后续任何人(包括 Phase 3/4 的改动)悄悄改掉宿主可见格式。
- 快照更新流程同第 13 章(它是运行时契约的一部分)。

### 16.5(可选 stretch)新增一个适配器作为 adapter-kit 试金石
- 候选:Claude Agent SDK(TS)。动手前查当前官方文档,接入点记 DECISIONS.md;交付标准 = 接入 adapter-kit 全绿 + 可运行示例 + README。
- 更多适配器(OpenAI Agents / LangChain / LlamaIndex)进 ROADMAP Next,等 adapter-kit 被证明后批量做才划算。

**并行选项**:16.1 完成后,两个现有适配器的接入与加固文件不相交,可 worktree 双路(I.4);单会话则逐个 test-author → implementer → reviewer 流水。

**Phase 3 验收**:
- [ ] adapter-kit 就位;两个适配器 AD-1…AD-6 全绿
- [ ] 故障注入三模式测试绿;降级语义已进 contract(含 D-xxxx 与影响面说明)
- [ ] 版本矩阵 CI 绿;注入格式快照建立
- [ ] (若做)新适配器达标;打 tag `phase-3-done`

---

## 17. Phase 4:demo 更锋利

**目标**:demo 不是功能陈列,是论证——90 秒让人看懂「认知纪律」为什么值钱。clone → 一条命令 → 无 key → 确定性复现。

### 17.1 四幕叙事(每幕 ≤30 秒,一句话点题)
1. **记住**:用户说「我对花生过敏」→ 固化为高置信度事实 → 下轮召回注入,点题:*说过的话会被记住,且带置信度*。
2. **纠正**:用户说「其实我不过敏,是我妹妹」→ 旧认知标记失效但不删除,新认知取代,点题:*纠正有痕,历史可溯*。
3. **矛盾**:用户行为与既有偏好冲突(说过爱喝美式,却连续点了三次奶茶)→ 系统不覆盖任何一方,暴露 conflict 等用户裁决,点题:*矛盾不是被谁悄悄赢了,而是被看见*。
4. **时间**:`--fast-forward 3d` 快进 → 上周的坏心情衰减消失,花生过敏与咖啡偏好留存,点题:*情绪会过去,事实会留下*。
(可选加映:export → import bundle,记忆随行,点题便携性。)

### 17.2 确定性无 key
- 全程 HashEmbedder + 固化用录制夹具;clone → `npm run demo` 零网络零 key。
- **确定性验收**:同一环境连续跑两次,输出 diff 为空(时间戳经注入 now 固定)。

### 17.3 终端可读性(约束:不为好看引入运行时依赖)
- 每幕输出三段式纯文本:用户输入 → 记忆层动作(证据入库/固化结果/召回注入)→ 认知状态表(文本对齐:认知 / 类型 / 置信度 / 状态 / 支撑证据 ID)。
- conflict 用显眼但朴素的标记(如 `!! CONFLICT`)呈现双方与各自证据链。

### 17.4 一条命令与快进时钟
- `npm run demo` 顺序演完四幕;`npm run demo -- --act 3` 跳幕;`--fast-forward <dur>` 驱动注入 now。
- demo 代码只经公共 API 调用核心(它同时是 API 的活体验收)。

### 17.5 录屏脚本
- `docs/demo-script.md`:逐幕的讲稿 + 终端命令 + 预期画面;README 顶部预留 GIF 位与生成说明。实际录制由人类执行。

**Phase 4 验收**:
- [ ] 干净环境 clone 一条命令 <2 分钟走完四幕;两次运行 diff 为空
- [ ] 四幕各自的点题输出与认知状态表可读;conflict 幕经人类观感确认
- [ ] demo 仅使用公共 API;录屏脚本入库;打 tag `phase-4-done`

---

## 18. Phase 5:文档更不绕(与 Phase 4 并行,文件不相交)

### 18.0 写作宪法(先立规,doc-writer 的系统提示词与此一致)
每页解决一个任务;先给可运行示例、后解释;短句、主动语态;一个概念只在一处正式定义、其余交叉链接;删除营销腔与「我们计划/未来将」;读者假设:第一次见到本项目的工程师。

### 18.1 README 重构(60 秒电梯稿)
顶部结构固定为:一句话定位 → demo GIF 位 → 「为什么不是又一个记忆库」三句话(事实/猜测分离、冲突暴露不覆盖、置信度由规则不由 LLM)→ 60 秒安装与首次调用 → 指向 docs/ 的三个入口(上手 / 概念 / 适配器)。其余长内容全部外移。

### 18.2 docs/ 信息架构定案
```
docs/
  getting-started.md        # 5 分钟:装、喂一条证据、召回一次
  concepts/                 # 认知纪律六条,每条一屏,配一段可运行片段
  recipes/                  # 每个适配器一篇「5 分钟接入」
  reference/
    memory-surface-contract.md   # 唯一 API 事实源(与 API 快照同步,见 13 章)
  internals/
    architecture.md         # 概念→文件映射,瘦身后保留
  demo-script.md
  glossary.md
```
现有文档内容迁移映射表先行(旧路径 → 新路径 → 保留/改写/删除),经 Integrator 过目再动手;旧路径留 301 式跳转小文件一个版本周期。

### 18.3 片段可执行验证
- `scripts/doc-snippets.mjs`:抽取 docs 与 README 中标记为可运行的 ts 围栏代码块 → 逐个编译并冒烟运行(HashEmbedder 环境)→ 进 CI。
- 不可运行的示意性片段显式标注 `<!-- snippet:skip -->`,默认全验。

### 18.4 链接与术语
- 死链检查(内链 + 锚点)进 CI;glossary.md 定稿后全文扫不一致叫法并统一。

### 18.5 收尾巡检
- scout 以「新人第一次读」视角全读一遍,产出「仍然绕」清单(每条:哪页/哪段/为什么绕/建议),doc-writer 逐条处理;人类抽读验收。

**Phase 5 验收**:
- [ ] README 电梯稿就位且 ≤ 一屏半;迁移映射表执行完毕
- [ ] snippets 验证 CI 绿;死链 0;glossary 定稿且全文一致
- [ ] 「仍然绕」清单全部关闭或降级进 ROADMAP;打 tag `phase-5-done`

---

## 19. Phase 6:公开基准(常态化)

预算不是约束,纪律仍然是:**每一次运行都可复现**。

### 19.0 运行纪律
- 每次运行产出 `bench/runs/<日期>-<简述>.md`:命令、commit hash、模型与版本、配置、实际 token 用量与费用(仅供参考,不设审批)、逐指标成绩。
- bench-runner 可直接执行真实运行;分数解读、参数采纳、对外结论一律交回 Integrator 守门。

### 19.1 基准接入
- LongMemEval 与 LoCoMo 都接(不再二选一):loader / runner / 评分 / 记录器置于 bench/;注明数据许可与来源。

### 19.2 三臂 × 双 embedder 矩阵
- 同模型同配置:vector-only / keyword-only / hybrid × HashEmbedder / 真实 embedder,矩阵成绩入 runs。

### 19.3 置信度参数敏感性
- 网格:底分 ±20%、半衰期 ×0.5/×1/×2;发现更优默认参数 → 单独 commit,依据写 D-xxxx;**若因此需更新某条 eval 断言数值,属规格修订,走铁律 1 流程报人类批准。**

### 19.4 常态化节奏与产出
- 每个 Phase 收尾跑一轮完整矩阵,runs 追加;BENCHMARKS.md 维护最新成绩 + 历史趋势 + 复现命令;与其他记忆库公开数字对照时注明条件差异,不做不对等比较。

**Phase 6 验收**:两套基准接入且各 ≥1 次完整矩阵;runs 可复现;敏感性报告入库;BENCHMARKS.md 就位;打 tag `phase-6-done`。

---

# 第四部分:横切关注点(20–24,全 Phase 生效)

### 20. 配置与环境变量
核心库**不读环境变量**,一切经构造时注入的 config;只有 demo/CI 层读 env 组装。

| 变量 | 层 | 用途 | 必需 |
|---|---|---|---|
| `MEMORY_DB_PATH` | demo | SQLite 文件路径 | 否(默认 `./memory.db`) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | demo/本地/nightly | LLM pool:固化、live 套件、judge、真实 embedder、基准 | live 相关任务必备 |
| `EMBEDDER` | demo | `hash` / 真实实现标识 | 否(默认 hash) |
| `SKIP_LIVE_LLM` | 测试 | =1 跳过需真 key 用例 | CI 主干必设 |

CI 主干走录制夹具是为**确定性**而非省钱;真实模型覆盖由 nightly 与本地 `test:live` 承担(15.4)。

### 21. 时间·日志·错误约定
**21.1 时间**:存储一律 UTC ISO-8601;衰减与置信度计算的 `now` 必须由调用方注入,核心逻辑禁止直取系统时间(demo 快进、测试可复现、确定性验收的共同前提);违反视为 bug。
**21.2 日志**:核心零日志依赖,注入式 `logger` 默认 no-op;任何日志不输出用户内容全文(截断 ≤80 字符,可关)、不输出密钥。
**21.3 错误**:核心抛类型化错误(错误码枚举);适配器按 AD-6 降级;错误信息不夹带用户内容全文。

### 22. 安全与隐私清单(逐条有测试或 lint 保证)
- SQL 一律参数化,禁止字符串拼接(全仓库 grep 审计一次,入 Phase 1 验收附注)
- 隐私位语义在每条出口路径生效:recall 注入、demo 展示、bundle 导出都过 allowLocalRead/allowInference 过滤,各有单独测试
- 依赖审计:每 Phase 收尾 `npm audit`,高危处理或记 CURRENT 阻塞区
- 密钥永不落盘(铁律 9;hooks 兜底)

### 23. 性能预算(本机、1 万条认知、HashEmbedder;首次基线后可修订,修订记 DECISIONS)

| 指标 | 预算 |
|---|---|
| hybrid recall P95 | ≤ 50ms |
| recordEvidence P95 | ≤ 5ms |
| 增量索引单条 | ≤ 10ms |
| 全量 reindex 1 万条 | ≤ 60s |
| demo 冷启动到第一幕首行输出 | ≤ 3s |

### 24. 迁移与回滚
**24.1 schema 迁移**:`PRAGMA user_version` 递增 + `migrations/<NNN>_<描述>.sql`,启动按序执行、每步幂等;**禁止修改已发布的迁移文件**,只能追加;任何迁移属铁律 2 范畴(影响面说明 + 人类批准)。
**24.2 回滚**:单 commit 问题 → `git revert`(保留历史);Phase 级失败 → 回 `phase-N-start` tag 重做;bundle 导入前自动备份库文件(`.bak-<时间戳>`)。

---

## 25. 风险登记册(每 Phase 收尾回查)

| # | 风险 | 概率 | 影响 | 缓解 | 触发信号 |
|---|---|---|---|---|---|
| R1 | node:sqlite/FTS5 不可用 | 中 | P1 阻塞 | 12.1 即验证;降级链 better-sqlite3 → 纯 TS BM25 | 验证命令失败 |
| R2 | 范围蔓延(最大风险) | 高 | 全局 | 铁律 4 + ROADMAP Later;守门清单第一条 | diff 出现任务书外文件 |
| R3 | 改测试凑绿 | 中 | 根基 | 铁律 1 + hooks + 人类抽查 `git log -p tests/eval/` | tests/eval 修改型 diff |
| R4 | 公共 API 被无意破坏 | 中 | 宿主/插件受损 | 13 章快照测试 + 16.4 注入格式快照 + 守门清单 | api-freeze 红 |
| R5 | 宿主 SDK 版本漂移 | 高 | 适配器失效 | 15.3 版本矩阵 CI;peer 版本范围 | 矩阵 job 红 |
| R6 | 固化需真 key,CI 不稳 | 中 | CI 可信度 | 录制夹具 + SKIP_LIVE_LLM;真 key 仅本地 | CI 偶发红 |
| R7 | live 套件不稳定(模型输出漂移) | 中 | nightly 误报 | 15.2 结构性断言先行;judge 温度 0 三票;fixtures:refresh 流程(15.4) | nightly 间歇性红 |
| R8 | demo 过度工程化 | 中 | 偏离锋利 | 17.3 纯文本约束;四幕各 ≤30 秒;不加运行时依赖 | demo 引入新依赖的 diff |
| R9 | 隐私位在出口被绕过 | 低 | 信任危机 | 22 章逐出口测试(注入/展示/导出) | 隐私测试红 |
| R10 | 并行冲突或角色越权 | 中 | 返工/铁律被绕 | 8.6 三规则;hooks 按 agent_type 拦截;I.4 合并纪律 | 合并冲突;拦截日志激增 |

---

# 附录

## 附录 A:给人类的启动指南

**A.1 环境**:Node ≥22、Git;LLM API key 放入环境变量——live 套件、固化质量评测、judge、真实 embedder、公开基准都直接使用,不设预算审批。

**A.2 开工前要定的事**:没有。预算约束取消后,原先唯一的决策点(是否跑基准)已并入常规计划;其余决策 Integrator 会按第 10 章格式来问你。

**A.3 启动步骤**:
1. 把本文件放进 MemoWeft 仓库根目录,命名 `PROJECT_PLAN.md`;
2. 在仓库内启动 Claude Code,第一条指令原文:

   > 完整阅读 PROJECT_PLAN.md。你的主会话身份是 Integrator(第 6 章)。先向我复述九条铁律与守门清单确认理解,然后给我 Phase 0 的执行计划——只出计划,不要动手,等我确认。
3. 之后每个 Phase:它先交计划 → 你确认 → 它委派子代理推进 → 铁律 8 事项会停下来问你;
4. Phase 0.4 落地 `.claude/` 后,按提示**重启一次会话**让 subagents 与 hooks 生效。

**A.4 日常检查(每天 5 分钟)**:读 CURRENT.md(在做什么/阻塞什么);`git log --oneline -20` 是否小步提交;每周亲自跑一次 `npm test`,并 `git log -p tests/eval/ | head -100` 抽查宪法目录、`git log -p tests/api/ | head -60` 抽查 API 快照变更是否都有对应 D-xxxx。

**A.5 Phase 验收话术**:见第 10 章,直接粘贴。

## 附录 B:AGENTS.md 升级模板(Integrator 章程落地版;Phase 0.5 以此替换现有内容,原三条纪律保留并展开)

```markdown
# AGENTS.md — 工程纪律与 Integrator 章程

本仓库由「人类 + Integrator(Claude Code 主会话)+ 六个子代理」协作维护。总纲见 PROJECT_PLAN.md,当前状态见 CURRENT.md。

## 三条底线(任何贡献者与任何 Agent 一体适用)
1. 不扩大任务范围:diff 只含任务声明的文件;新想法进 ROADMAP.md Later。
2. 改 API / schema / 权限模型前,先写影响面说明(动机 / 破坏性 / 迁移路径),经人类批准并记 DECISIONS.md。
3. 认知纪律不能顺手优化掉:助手输出不入证据;置信度只由规则计算;冲突只暴露不裁决;证据 ID 白名单。

## Integrator 守门清单(每份 diff 合并前逐条过)
- diff 未超任务书文件白名单
- 全量 eval 绿;api-freeze 测试绿(或附已批准的 D-xxxx)
- 认知纪律四点未被触碰
- schema 改动配幂等迁移脚本
- DoD(PROJECT_PLAN.md 附录 G)全过;commit message 合规

## 硬性约束(hooks 机器强制,见 .claude/)
tests/eval/ 只增不改;api-surface.snapshot 禁手改;LICENSE 变更属人类;发布/强推/破坏性命令由人类执行;密钥永不落盘。

## 常用命令
npm install / npm test / npm run demo / npm run api:update(慎用,见 PROJECT_PLAN.md 第 13 章)
```

CLAUDE.md 同步精简为三行入口:一句项目定位 + 「先读 AGENTS.md 与 PROJECT_PLAN.md」+ 常用命令表。

## 附录 C:CURRENT.md 模板

```markdown
# CURRENT — 当前状态(Integrator 每个工作段落结束更新)
更新于:<日期时间> | 所在 Phase:<N>(tag: phase-N-start @ <hash>)

## 正在进行
- <任务> → 委派:<subagent> → 文件所有权:<路径白名单> → 状态:<进行中/待审查>

## 刚完成(最近 5 条,附证据)
- <任务>(commit <hash>;证据:<测试输出摘要/报告路径>)

## 阻塞(等人类或等依赖)
- BLK-001 <标题>:现象 / 已尝试(≥3 次)/ 假设 / 需要:<决定 or 资源>

## 下一步(按序)
1. <任务>
```

## 附录 D:ROADMAP.md 模板

```markdown
# ROADMAP
## Now(本轮四主轴,对应 PROJECT_PLAN.md Phase 1–4)
- 召回更准 / 适配器更稳 / demo 更锋利 / 文档更不绕

## Next(本轮之后优先考虑)
- 更多适配器(OpenAI Agents / LangChain / LlamaIndex,待 adapter-kit 证明后批量)
- Reranker 实装 / 公开基准常态化

## Later(明确不在本轮;想法只进不丢)
- Python 移植与跨语言一致性
- REST server、多租户、pgvector/Postgres 后端
- 托管服务、Web 界面、多模态证据、CRDT 同步
- IDEA-xxx <一句话>(来源 Phase/日期)
```

## 附录 E:DECISIONS.md 模板(ADR-lite)

```markdown
## D-0001 FTS5 tokenizer 选择
日期:/ 状态:已采纳
背景:/ 选项:A trigram(CJK 稳,索引大) B unicode61(小,不分中文)
决定:A / 依据:golden set 中文用例 Recall 对比 + 索引体积实测(附数字)
影响面说明(若涉 API/schema/权限):动机 / 破坏性 / 调用方与适配器迁移路径 / 人类批准记录
```

## 附录 F:提交规范(Conventional Commits)

| 类型 | 示例 |
|---|---|
| feat | `feat(recall): add RRF hybrid search behind mode switch (D-0003)` |
| fix | `fix(consolidation): reject fabricated evidence ids` |
| test | `test(adapter-kit): AD-1..AD-6 reusable suite` |
| perf | `perf(index): incremental upsert instead of full rebuild` |
| docs | `docs: README elevator pitch, move long-form to docs/` |
| chore | `chore(ci): peer version matrix for adapters` |

规则:现在时祈使句;正文写动机与影响;涉决策引 `D-xxxx`;涉风险引 `Rn`。

## 附录 G:Definition of Done(每次 commit 前逐条过)

- [ ] 新逻辑有测试且先红后绿;全量测试绿
- [ ] lint 绿;typecheck 绿;**api-freeze 测试绿(或附已批准 D-xxxx)**
- [ ] 未触碰 tests/eval/ 既有断言(人类批准的规格修订除外)
- [ ] 公共行为变更 → contract 文档 + CHANGELOG 同步
- [ ] 无密钥/调试残留;日志不含用户内容全文
- [ ] CURRENT.md 已更新;需要的话 DECISIONS.md 已记录
- [ ] commit message 符合附录 F

## 附录 H:故障排查(FAQ)

| 症状 | 首查 | 处置 |
|---|---|---|
| npm test 环境性失败 | node --version | 与 engines 对齐;或切 better-sqlite3(D-0001) |
| FTS5 建表报错 | 12.1 验证命令 | 走降级链;记 D-0001 |
| 中文查询召回为 0 | tokenizer | 确认 trigram;golden 中文组回归 |
| api-freeze 红 | 是否有意变更 API | 无意 → 回滚;有意 → 影响面说明 + 人类批准 + D-xxxx + api:update |
| 子代理报「BLOCKED by protect.py」 | 拦截原因行 | 角色越权 → 换正确角色或收回主会话;铁律类 → 按提示走流程 |
| CI 偶发红 | 是否打了真实 API | 补 SKIP_LIVE_LLM 标记;夹具化 |
| nightly live 红 | 模型漂移还是真回归 | 先看 15.2 结构性断言;必要时 fixtures:refresh 后确定性套件复验;结论记 CURRENT |
| 版本矩阵红 | 哪个 SDK 版本 | 升最低支持版或适配,结论记 DECISIONS |
| demo 两次运行 diff 非空 | 是否直取系统时间/随机 | 排查 now 注入与 HashEmbedder;违反 21.1 视为 bug |
| Claude Code 想改 eval 断言 | 铁律 1 | 停止 → CURRENT 阻塞区 → 人类裁决 |

## 附录 I:多智能体配置包(subagents + hooks + 并行化)

> Phase 0.4 按本附录**逐字**落地到 `.claude/` 并提交入仓库。配置格式依据 Claude Code 官方文档(2026-06);落地时以 https://code.claude.com/docs 当前版本为准,差异记 DECISIONS.md。
> **Integrator 不是 subagent 文件**——它就是主会话,章程在第 6 章与 AGENTS.md;以下六个是它的班底。

### I.1 六个项目级子代理(`.claude/agents/<name>.md`)

**`.claude/agents/scout.md`**
```markdown
---
name: scout
description: 只读代码侦察员。理解结构、梳理调用链、核实参数实际值而不做修改时使用。PROACTIVELY 用于每个 Phase 开工前的校准侦察。
tools: Read, Grep, Glob
model: inherit
---
你是代码侦察员,只读不写。按 Integrator 给定的问题清单探索,输出结构化报告:
1) 涉及文件与行号;2) 调用链/数据流一步一行;3) 关键参数实际值(标注 文件:行号);4) 与 PROJECT_PLAN.md 描述不符处单独成节。
规则:每个结论附 文件:行号 证据;不确定标「未验证」;只报告,不提计划外行动。
```

**`.claude/agents/test-author.md`**
```markdown
---
name: test-author
description: 只写测试不写实现。子任务开始时先行编写红色测试(单测/契约/新增 eval)时使用。
tools: Read, Grep, Glob, Write, Edit, Bash
model: inherit
---
你是测试作者,先测后码。边界:只在 tests/ 下工作;只新增文件或修改本任务自建的文件;绝不碰 src/,绝不改既有 eval 断言(hooks 硬拦,被拦即越界)。
产出:红色失败测试 + 说明(每用例验证什么、对应 PROJECT_PLAN.md 哪条验收)+ npm test 失败摘要。
```

**`.claude/agents/implementer.md`**
```markdown
---
name: implementer
description: 实现者。红色测试已就位,需要编写实现使其变绿时使用(含 demo 代码)。
tools: Read, Grep, Glob, Edit, Write, Bash
model: inherit
---
你是实现者。目标:让 Integrator 指定的红色测试变绿,不多不少。
纪律:不碰 tests/eval/(hooks 硬拦);认知纪律四点不可顺手优化;核心逻辑禁止直取系统时间(now 注入);公共 API 变更必须先在任务书里有 D-xxxx 授权;禁止顺手重构无关代码——想法写进报告。
完成标准:目标测试绿 + 全量绿 + lint/typecheck 绿;报告改动文件清单与理由。
```

**`.claude/agents/reviewer.md`**
```markdown
---
name: reviewer
description: 只读代码审查员。PROACTIVELY 在每个子任务提交给 Integrator 前审查改动。
tools: Read, Grep, Glob, Bash
model: inherit
---
你是审查员,只读;用 Bash 看 git diff 与跑测试,不修改任何文件。
清单:1) 认知纪律四点;2) 是否超任务书文件白名单;3) api-freeze 与注入格式快照是否受影响;4) DoD(附录 G);5) 明显 bug 与边界。
输出:[严重]/[建议]/[通过] 分级,附 文件:行号;结尾给「可交付 Integrator / 需修改」结论。
```

**`.claude/agents/doc-writer.md`**
```markdown
---
name: doc-writer
description: 文档写作者。PROACTIVELY 用于 README、docs/ 的撰写与重写、demo 讲稿。
tools: Read, Grep, Glob, Write, Edit, Bash
model: inherit
---
你是文档写作者,只写 README.md 与 docs/;不修改 src/ 与 tests/(hooks 按角色硬拦)。
写作宪法(与 PROJECT_PLAN.md 18.0 一致):每页解决一个任务;先可运行示例后解释;短句、主动语态;一个概念只在一处正式定义,其余交叉链接;删除营销腔与「未来将」。
每篇自查:新人能否只靠这一页完成该任务?可运行片段是否通过 scripts/doc-snippets 校验?
```

**`.claude/agents/bench-runner.md`**
```markdown
---
name: bench-runner
description: 评测执行员(Phase 2 固化质量与 Phase 6 公开基准)。PROACTIVELY 用于跑评测、整理 runs、撰写对比报告。
tools: Read, Grep, Glob, Bash, Write
model: inherit
---
你是评测执行员,直接执行真实模型评测:固化质量评测(bench/eval-consolidation)、检索双臂评测、公开基准。
纪律:每次运行产出可复现记录(bench/runs/:命令、commit、模型、实际用量、成绩);judge 判分温度 0、三次多数投票;你只呈现数据,分数解读与参数采纳交回 Integrator 守门。只写 bench/(hooks 按角色硬拦其余路径);发布类操作永不执行。
```

### I.2 强制层:`.claude/settings.json` 与钩子脚本

hooks 对主会话与全部 subagent 的每次工具调用一视同仁;并利用钩子输入中的 agent_type 做**角色级写入限制**——职责分离由此从口头约定变成机器事实。

**`.claude/settings.json`**
```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Edit|Write|MultiEdit",
        "hooks": [{ "type": "command", "command": "python3 .claude/hooks/protect.py" }] },
      { "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "python3 .claude/hooks/protect.py" }] }
    ]
  }
}
```

**`.claude/hooks/protect.py`**(撰写本文档时已实测七个场景:eval 拦 / 快照拦 / doc-writer 写 src 拦 / doc-writer 写 docs 放 / test-author 写 tests 放 / 主会话写 src 放 / npm publish 拦)
```python
#!/usr/bin/env python3
"""PreToolUse 钩子:铁律 1/2/8/9 与角色权限的机器强制层。对主会话与全部 subagent 生效。
exit 2 = 拦截(stderr 反馈给 Claude);exit 0 = 放行。解析异常一律放行,避免误伤。"""
import json, sys, os, re

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool = data.get("tool_name", "")
ti = data.get("tool_input") or {}

def block(msg):
    print("BLOCKED by protect.py: " + msg, file=sys.stderr)
    sys.exit(2)

if tool in ("Edit", "Write", "MultiEdit"):
    p = ti.get("file_path") or ""
    if not p:
        sys.exit(0)
    norm = os.path.abspath(p).replace("\\", "/")
    base = os.path.basename(norm)
    # 铁律 1:宪法目录 tests/eval/ 只增不改
    if "/tests/eval/" in norm:
        if tool in ("Edit", "MultiEdit"):
            block("铁律1:tests/eval/ 既有测试禁止修改。测试不过=实现有错;新增用例请创建新文件。")
        if tool == "Write" and os.path.exists(norm):
            block("铁律1:tests/eval/ 既有文件禁止覆盖;只允许新增新文件。")
    # 铁律 2/8:API 快照禁手改;LICENSE 变更属人类
    if base == "api-surface.snapshot":
        block("铁律2:API 快照禁止手改;走影响面说明+人类批准+npm run api:update。")
    if base == "LICENSE" and (tool in ("Edit", "MultiEdit") or os.path.exists(norm)):
        block("铁律8:LICENSE 变更属人类决定,由人类执行。")
    # 铁律 9:敏感文件禁止写入
    if re.search(r"(^\.env($|\.)|id_rsa|\.pem$|credentials)", base):
        block("铁律9:敏感文件禁止写入;密钥只经环境变量。")
    # 角色级写入限制(钩子输入含 agent_type;字段缺失则跳过本节)
    agent = (data.get("agent_type") or "").lower()
    ROLE_WRITE_DENY = {
        "doc-writer":   ("/src/", "/tests/"),
        "test-author":  ("/src/",),
        "bench-runner": ("/src/", "/tests/", "/docs/"),
    }
    if agent in ROLE_WRITE_DENY:
        for seg in ROLE_WRITE_DENY[agent]:
            if seg in norm:
                block("角色越权:" + agent + " 禁止写入 " + seg + " 路径。")

elif tool == "Bash":
    cmd = ti.get("command") or ""
    if re.search(r"(npm\s+publish|twine\s+upload|uv\s+publish|git\s+push\s+\S*\s*(-f|--force)|rm\s+-rf\s+[/~])", cmd):
        block("铁律8/9:发布、强推与破坏性命令必须由人类亲自执行。")

sys.exit(0)
```
说明:合法的快照更新经 `npm run api:update`(Bash 内脚本自行写盘,不经 Write 工具),因此不会被误拦;eval 规格修订的合法通道 = 人类在 Claude Code 之外手改并留档 D-xxxx——钩子本身不设后门。

### I.3 各 Phase 并行化地图

| Phase | 可并行 | 方式 | 必须串行 |
|---|---|---|---|
| 0 | 12.2 校准三路分片(置信度 / 读写路径 / API 现状) | scout ×3 | 12.1、12.3–12.6 |
| 1 | 14.2 黄金集(test-author)∥ 14.3 FTS5(implementer),文件不相交 | subagent ×2 | 14.4/14.5(依赖前两者) |
| 2 | 15.1 场景语料(test-author)∥ 15.2 评测器(implementer),文件不相交 | subagent ×2 | 15.3 提示词回归、15.4 双轨与夹具刷新 |
| 3 | 16.1 adapter-kit 先行后,两个现有适配器接入加固不相交 | worktree ×2 | 16.2 契约语义定义、合并 |
| 4 ∥ 5 | **全计划最佳并行点**:demo(implementer/test-author)∥ 文档(doc-writer),目录不相交 | worktree ×2 或单会话双委派 | 18.2 迁移映射表需 Integrator 先批 |
| 6 | 两套基准运行、固化质量评测、runs 报告整理 | bench-runner(可直接真跑) | 参数敏感性结论的采纳:Integrator 守门 |
| 全程 | commit 前审查 | reviewer | 合并与打 tag:Integrator 独占 |

### I.4 git worktree 并行操作手册(以 P4 ∥ P5 为例)

```bash
# 前提:phase-3-done(适配器)已打 tag,main 干净,.claude/ 已入仓库(worktree 自动继承同套配置)
git worktree add ../wt-demo -b feat/p4-demo
git worktree add ../wt-docs -b feat/p5-docs
# 人类各启动一个 Claude Code 实例,开场指令模板:
#   wt-demo:「读 PROJECT_PLAN.md 第 17 章。只做 Phase 4,只允许改动 demo 相关目录与其测试,停在本分支不合并。」
#   wt-docs:「读 PROJECT_PLAN.md 第 18 章。只做 Phase 5,只允许改动 README.md 与 docs/,停在本分支不合并。」
# 合并纪律(主 worktree 的 Integrator 执行,一次只合一个):
git checkout main && git merge --no-ff feat/p4-demo
npm test                       # 全量绿(含 api-freeze 与注入格式快照)才合下一个;红 → revert,该分支回炉
git worktree remove ../wt-demo && git branch -d feat/p4-demo
# 同法合 feat/p5-docs;各打 phase-4-done / phase-5-done
```

### I.5 已知限制与注意事项

1. **subagent 无法中途请求人类批准** → 铁律 8 事项永远只在 Integrator 主会话执行。
2. **subagent 定义会话启动时加载**:改 `.claude/agents/*.md` 后需重启会话。
3. **hooks 对 subagent 同样生效**是角色权限成立的前提——12.4 实测未拦截则停止,按铁律 7 核对当前文档;agent_type 字段若产品侧变动,角色节自动跳过(脚本已容错),届时回退为提示词约束并记 DECISIONS。
4. **不膨胀角色**:固定 6 个;新增角色 = 范围蔓延,进 ROADMAP Later。
5. **并行写任务 ≤3**;同文件耦合改动永不并行;文件所有权在任务书白纸黑字。
6. **Agent Teams** 实验特性、token 成本数倍,默认不用;人类主动要求再评估并记 DECISIONS。
7. 本附录依据 2026-06 的 Claude Code 文档;frontmatter 字段、hooks 事件与 stdin 格式若有变动,以 https://code.claude.com/docs 为准。

---

*本文档结束。Claude Code:回到顶部「致 Claude Code」,以 Integrator 身份按执行方式开始。*
