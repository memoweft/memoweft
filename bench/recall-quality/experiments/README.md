# MEM-5 两条候选路径的合成实验

这是 Core 仓库的评测代码，不被产品导入。产品默认行为、依赖与存储格式不变。
结论见 [result.md](result.md)，原始数值和用量见 [results](results/)。

## 先冻结，再实验

独立的 `make_blind.py` 使用 MiMo 编写 195 个合成场景，不导入检索实现、不读取评分、不给作者任何检索参数。
原 12 类各 15 条：150 正例 / 30 负例，另加 15 条新的技术多义词负例。每条只有一个问句，避免开发集同一种子三个问句的重复相关。
数据与生成脚本先在 `c30f14b` 单独提交，之后才实现 A/B。四份暴露集合没有改动。
生成器只检查结构、重复问句和数量，标签来自模型作者；没有独立人工语义裁决。作者与 A 使用相同的 MiMo 模型，可能存在共享语言风格偏差。

开发集独占调参：A 扫描 0.05–1.00 步长 0.05；B 扫描 0.65–0.99 步长 0.01；都加 1.001 作为完全关闭新增候选的对照。
选择前 5 条命中率最高且开发集新增误召问句不超过 3/360、纯负例不增加误召、已有命中不丢、生命周期不泄漏的阈值；并列时先少误召，再取较高阈值。
AB 合用两个独立选定阈值，没有额外调参。`parameters.json` 固定参数、实现哈希和数据哈希。
开发阶段曾修复未关闭 SQLite 连接，并在盲测前修复 A 多算一次未使用向量的计时问题；每个开发集选择、排名和错误逐条断言相同，阈值没有重选。

`final` 使用排他创建的 `final-once.json` 预约一次运行，拒绝再次运行。它先验证冻结哈希，再首次加载盲测数据；不输出逐条结果，不画盲测阈值曲线。
一次最终运行包含基线、A、B、AB，以及内部只读校验调用；审查集也只在最终运行中评估，任何失败都保留，不补规则。
最终公开逐条证据只能用于后续回归，不能再视为盲测。

## 实验接入点和过滤

生产路径是 `QueryService.preview_recall → recall_world_snapshot → _current_world_rows → _match_world_rows → format_recall`。
候选实验仅在进程内替换两个私有函数并在退出时恢复。先从临时合成 World 取原始记录并计算候选分数，再调用原 `_current_world_rows` 检查账号、Evidence 权限、删除、过期等状态。
新增候选再经原 `_match_world_rows` 的查询相关更正、历史、人物名称规则，最后重新调用原 `world_item_visible`，然后才截取 5 条并交给原快照与渲染。
原始命中按原顺序放在前面。这能避免新增排序挤掉旧命中，也保留了基线既有的错误；实验没有通过删除原候选来美化指标。
别名文本不进入返回的正式命题。所有记录、账号和主机标识都是合成的，不读个人库、不读 Runtime 数据、不请求固定本地端口。

A 对一条记忆单独生成有界主题/别名检索旁注，最多八个别名；按中文双字/英文整词的 Dice（重叠系数）匹配，不写同义词或领域词表。
模型格式错误时记账并采用空旁注；超长列表截前八项。无旁注的旧记忆保留原召回行为。
当前形成器已经支持受来源控制的主题别名，因此 A 是更丰富的实验旁注，并非证明现在完全没有别名。配对成本实验另测把旁注加入真实形成提示词的增量，未将扩展信封交给产品编译器。

B 使用 `intfloat/multilingual-e5-small` 发布者提供的 qint8 ONNX（开放神经网络交换格式）文件，384 维。
只用 `CPUExecutionProvider`、两个线程、attention-mask mean pooling（注意力遮罩均值池化）、L2 normalization（L2 归一化），记忆前缀 `passage: `、问句前缀 `query: `，最长 512 词元。
模型下载在 `.local/mem5/model/`，不提交。许可证与固定版本、文件大小、SHA-256（安全散列）见结果 JSON。运行期不联网。

## 复现

```powershell
npm ci --no-audit --no-fund
cd py
uv sync --extra dev
cd ..
uv venv .local/mem5/venv --python py/.venv/Scripts/python.exe
uv pip install --python .local/mem5/venv/Scripts/python.exe -r bench/recall-quality/experiments/requirements.txt -e ./py
& .local/mem5/venv/Scripts/python.exe bench/recall-quality/experiments/models.py
& .local/mem5/venv/Scripts/python.exe bench/recall-quality/experiments/test_routes.py
```

模型生成时在进程内设置 `MIMO_API_KEY`，不打印、不写文件；Windows 的 Machine 环境变量也可由启动命令读取。
所有 CPU 模型整轮和完整测试在本机按编排约定原子占用一个重活名额，完成或失败都释放；一个包同一时间只占一个。
新复现实验应复制到独立输出目录并保留现有冻结文件，不覆盖本次证据。重新生成数据需要重新提交与重新认定盲测资格。

```powershell
# 本次证据已经存在，下面两项会拒绝覆盖/重跑：
& .local/mem5/venv/Scripts/python.exe bench/recall-quality/experiments/runner.py tune
& .local/mem5/venv/Scripts/python.exe bench/recall-quality/experiments/runner.py final
# 容量计时可单独复测，不读取盲测标签：
& .local/mem5/venv/Scripts/python.exe bench/recall-quality/experiments/scale.py
& .local/mem5/venv/Scripts/python.exe bench/recall-quality/experiments/scale.py --full-chain
```

容量报告分开写：向量矩阵检索 / 问句编码 / 真实链路。1 万、5 万矩阵使用 1,000 个实算合成向量平铺，仅测内存带宽和排序，不声称测了大库召回质量。
旧库回填计时实算 1,000 条，再线性估计更大规模；完整链路每种规模只做一个成对观察，不能当作 p95（第 95 百分位）或生产容量保证。
