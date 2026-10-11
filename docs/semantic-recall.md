# 本机按意思召回（MEM-6）

记忆召回默认使用 multilingual-e5-small 在本机补充候选。原有字面命中保留原顺序、排在前面；每次最多补 3 条，余弦相似度门槛默认 `0.87`。Python（编程语言）主库结构版本仍为 **21**，TypeScript（类型脚本）仍为 **6**，Portable（可移植格式）仍为 **4**。不改形成提示词，不调用生成模型，不引入向量数据库。

`0.87` 比 MEM-5 的 `0.86` 高一档。已有开发曲线中，过门槛的非目标返回条数从 46 减为 43，既有命中没有丢失；继续升到 `0.88` 会把开发命中降到 29.33%。本次采用这一适度保守的取值，并把补充数量收紧到 3。参数在重放暴露盲测前确定；不根据它调参。合成数据不能证明日常使用效果，误带仍需本人试用判断。

## 安装与配置

基础安装保持原依赖。按意思找使用可选的 `semantic` extra（附加依赖组），缺少它时自动使用字面召回。

```powershell
cd py
uv sync --locked --extra dev --extra semantic
```

三个直接依赖固定为 `numpy==2.4.3`、`onnxruntime==1.24.4`、`tokenizers==0.22.2`，无需 PyTorch（深度学习框架）、Transformers（模型训练与推理框架）或服务进程。锁文件记录传递依赖、下载地址与散列。附加依赖支持 Python 3.11–3.14；其它版本仍可运行基础 Core（记忆核心），语义功能报告依赖不可用。Windows 上三个直接依赖的安装文件共约 85.39 MB，含 16 个直接/传递包共约 132.07 MB（其中 packaging、colorama 已在本工作树开发环境中存在），见 [实际安装文件统计](../bench/recall-quality/semantic-results/dependencies.json)。这些是十进制文件字节，不含已有基础依赖、虚拟环境和额外生成的字节码；其它平台的二进制体积会不同。

许可：模型和 ONNX Runtime（ONNX 推理运行时）为 MIT；NumPy 的主许可为 BSD-3-Clause（含随包列出的其它许可）；Tokenizers 和 Hugging Face Hub 为 Apache-2.0。传递依赖包括 FlatBuffers（Apache-2.0）、Protobuf/SymPy/mpmath（BSD）、filelock/PyYAML/fsspec（MIT）、hf-xet（Apache-2.0）、Click（BSD-3-Clause）、tqdm（MPL-2.0/MIT）、packaging（Apache-2.0 或 BSD-2-Clause）、colorama（BSD-3-Clause）。随发行包的许可文件保持原样，不把模型文件放进源码或可移植备份。

配置读取 Core 子进程的环境变量，旧调用方无需新增参数：

| 配置键                        | 默认值                                     | 含义                             |
| ----------------------------- | ------------------------------------------ | -------------------------------- |
| `MEMOWEFT_SEMANTIC_ENABLED`   | `1`                                        | `0`、`false`、`off` 关闭补充候选 |
| `MEMOWEFT_SEMANTIC_THRESHOLD` | `0.87`                                     | 有限数，范围 0–1；调高减少补充   |
| `MEMOWEFT_SEMANTIC_CACHE_DIR` | 记忆库目录下 `cache/multilingual-e5-small` | 自定义本机模型缓存目录           |

环境变量修改后重启记忆桥。无效配置降级并在状态中给出 `invalid_configuration`，不影响其它记忆功能。Python 调用方也可以使用 `SemanticConfig` 和 `configure_runtime(path, config)`；`max_additions` 可设为 0–3，不能超过 3。`recall_world_snapshot` 的可选 `candidate_provider` 是正式提名接口，原调用签名继续可用。

## 首次使用与状态

DSH（宿主记忆桥）启动时只安装可重入的附加侧表、触发器和索引，不加载模型。首次召回在后台启动模型准备，那次请求直接返回原有字面结果。已加载但正在补算一个小批次时，召回也可以直接走字面路径；不等待后台编码锁。

固定下载源为发布者 `intfloat/multilingual-e5-small` 的版本 `614241f622f53c4eeff9890bdc4f31cfecc418b3`，只下载 `onnx/model_qint8_avx512_vnni.onnx` 和 `onnx/tokenizer.json`，共 **135,429,554 字节（135.43 MB）**。文件名、长度和 SHA-256（安全散列）固定在 [model.py](../py/src/memoweft/semantic/model.py)。缓存与下载临时文件均校验后才使用；下载中、无网络、校验错误或缺依赖时召回继续走字面。失败原因不包含问句、正文、路径或网络响应；修好环境后重启桥重试。首次下载只向固定模型源发送文件请求，不发送记忆或问句。

模型仅使用 CPUExecutionProvider（中央处理器执行后端），两个推理线程、384 维、最长 512 词元、`query: ` / `passage: ` 前缀、attention-mask mean pooling（注意力遮罩均值池化）和 L2 normalization（L2 归一化）。加载后常驻内存；MEM-5 同模型约 476 MB，仅作容量参考。不同批次填充和量化运算可能让阈值附近的分数轻微不同，因此本包报告真实产品向量的结果。

既有 RPC（远程过程调用）`health` 的 `runtime.semantic_recall` 提供状态，不新增协议版本或必选调用参数：

| `state`       | 中文含义 | `reason` 示例                                                       |
| ------------- | -------- | ------------------------------------------------------------------- |
| `disabled`    | 未启用   | `null`                                                              |
| `preparing`   | 准备中   | 首次召回前为 `not_loaded`，开始加载后为 `null`                      |
| `ready`       | 就绪     | `null`                                                              |
| `unavailable` | 不可用   | `dependency_missing`、`invalid_configuration`、异常类型或 `stopped` |

状态同时带 `model_id`、`threshold`、`max_additions`、`indexed_count`、`backfill_complete`。就绪表示模型可推理；`backfill_complete` 才表示这一轮已有记忆补算完成。查询状态本身不触发模型下载。无需设置页即可由原 RPC `health` 检查，WeftMate 的未来设置页可以直接显示这些字段。

## 派生数据与隐私

附加结构位于同一个 SQLite（嵌入式数据库）文件里：`semantic_vector` 保存账号、种类、记忆编号、内容散列、模型标识、维度、向量及过期时刻；`semantic_pending` 只保存待处理编号；`semantic_epoch` 用于内存矩阵失效。它们不属于 World（正式记忆世界）、Portable 或主库版本契约。旧库未安装这些表仍正常读写与字面召回；旧代码能读取装过附加表的 v21 库，触发器继续删除旧派生向量，但旧代码不会补算它们。

记忆与来源增改进入持久待处理队列，后台每批 16 条编码。模型推理在写事务外进行，提交时再核对原正文散列、当前权限和生命周期。记忆内容变化、删除、遗忘、更正、撤回、归档、静音、来源或权限变化时，SQLite 触发器在**原变更的同一事务**内删除对应向量；回滚原变更也会回滚向量删除。来源有效期到达后独立清理线程约每 100 ms 删除过期向量，下载中、推理失败或关闭语义开关时仍清理；Core 停止期间经过的有效期在下次启动时清理。前台始终以原有权限与有效期规则拒绝过期记录，不依赖清理时机。

忘掉之后侧表查不到向量；编码期间遗忘也不能把它写回来。内存矩阵按账号懒加载，只从向量侧表取数据；删除/更新后按侧表代次清空或重载，绝不从原表整库计算语义分数。最多取 32 个提名，按原有账号、来源权限、更正、历史及人物名称规则过滤，最后逐条运行 `world_item_visible`，只补 3 条。候选分数不是放行凭据。

旧记忆后台分批补算，前台正常读写不等待；未补到的记忆仅走字面。进程中断后已提交向量继续使用，未提交的一批重新排队；重启会分批检查遗漏编号，已有正确模型向量不重算。模型标识包括固定版本、量化与池化契约，旧模型向量不会用于新模型。每条原始向量 1,536 字节，5 万条约 76.8 MB，另有矩阵和库页开销；本包使用直接余弦矩阵搜索，没有向量数据库。

需要重建时，停止桥，在写连接中执行 `DELETE FROM semantic_vector` 和 `DELETE FROM semantic_pending` 后重启即可。**不要单独 DROP（删除表结构）侧表而留下引用它的触发器。** 附加表可以随源码和对应触发器一起卸载；日常重算只需清空派生行，原记忆无需修改。关闭开关不会加载模型或增加候选，已安装触发器继续保证遗忘删除。

## 验证与已知限制

所有数据为合成。关闭开关已在 638 问上验证原选择、顺序、命中、误带逐条一致。打开后使用真实生产调用链重放开发、挑战、保留、审查和 MEM-5 暴露盲测；审查 18 项必过保护和原有命中保护保留。结果见 [质量证据](../bench/recall-quality/semantic-results/quality.json)、[容量与补算实测](../bench/recall-quality/semantic-results/scale.json)和 [重放脚本](../bench/recall-quality/semantic_production.py)。没有改冻结标签、开新盲测或重新扫描阈值。

```powershell
$env:MEMOWEFT_SEMANTIC_ENABLED = '0'
cd py
uv run --extra dev --extra semantic pytest -q
uv run --extra dev --extra semantic mypy --strict src tests
uv run --extra dev --extra semantic python ../bench/recall-quality/evaluate.py --check --exact-baseline --repeats 3
```

后台补算、矩阵加载和更保守的门槛不会消除相关性错误。大库仍有原字面路径和完整快照校验成本；容量结果包含这些成本，不以纯矩阵时间冒充实际召回速度。最长 512 词元会截断超长记忆的向量输入，原正文与字面检索仍完整保留。后台补算期间或模型尚未就绪时语义结果可能随着派生索引完成而增加；当前召回仍在同一只读事务中校验正式 World，派生索引就绪后固定索引/修订的输出保持一致。
