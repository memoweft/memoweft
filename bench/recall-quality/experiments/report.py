"""Render numerical evidence after the one final invocation; never tunes."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from aliases import read
from runner import HERE, OUT


def percent(value):
    return f"{100 * value:.2f}%"


def review_required(rows):
    positives = [r for r in rows if r["must_hit"]]
    negative = [r for r in rows if r["must_abstain"]]
    return f"{sum(0 < r['rank'] <= 5 for r in positives)}/{len(positives)}", f"{sum(not r['selected'] for r in negative)}/{len(negative)}"


def main():
    assert read(OUT / "final-once.json")["status"] == "completed"
    dev, final = read(OUT / "development.json"), read(OUT / "final.json")
    scale, full, formation = read(OUT / "scale.json"), read(OUT / "full-scale.json"), read(OUT / "formation-cost.json")
    hardware = read(OUT / "hardware.json")
    sections = {"开发": {"baseline": dev["baseline"], "arms": dev["arms"]}, "盲测": final["splits"]["blind"], "审查": final["splits"]["review"]}
    lines = []
    add = lines.append
    add("# MEM-5：换个说法也想得起来，两条路实测")
    add("")
    add("本次新盲测的现状命中率为 46.67%，比旧开发集 17% 高，说明它的表达分布更容易被字面链路命中。A 只救回原漏掉 80 问中的 4 问（5%），B 救回 67 问（83.75%），合用救回 68 问（85%）。但 B/合用在 150 条正例中的 73 条夹带了非目标记忆；高命中率同时带来错误上下文。")
    add("")
    add("这次只做实验，**产品召回没有修改**。195 条新合成问句先单独提交，再写实现；150 条正例、45 条负例。开发集选完阈值后，盲测只进行一次最终运行，之后没有改阈值或补规则。以下数字包括原召回已有的命中与错误。")
    add("")
    add("| 路线 | 没见过的问法：前 5 条命中 | 不相关问句误召 | 原有命中丢失 | 审查：原行为/人物；技术负例 | 开发集典型检索耗时 |")
    add("| --- | ---: | ---: | ---: | --- | ---: |")
    for arm, label in [("baseline", "现状"), ("A", "A：形成时写主题/别名"), ("B", "B：本机按意思找"), ("AB", "A+B 合用")]:
        blind = sections["盲测"]["baseline"] if arm == "baseline" else sections["盲测"]["arms"][arm]
        review = sections["审查"]["baseline"] if arm == "baseline" else sections["审查"]["arms"][arm]
        current = dev["baseline"] if arm == "baseline" else dev["arms"][arm]
        positive_pass, negative_pass = review_required(review["results"])
        lost = 0 if arm == "baseline" else blind["protections"]["lost_baseline_hit5"]
        add(f"| {label} | {percent(blind['overall']['hit5'])} | {percent(blind['overall']['negative_query_false_recall_rate'])} | {lost} | {positive_pass}；{negative_pass} | 中位 {current['overall']['latency_ms']['p50']:.2f} ms |")
    add("")
    final_cost = final["alias_cost_final"]
    dev_cost = dev["alias_cost"]
    n_alias = dev_cost["memories"] + final_cost["memories"]
    uncached1000 = (dev_cost["estimated_cny_uncached"] + final_cost["estimated_cny_uncached"]) * 1000 / n_alias
    add(f"A 查询时无需常驻模型、不发云请求。另发一次生成旁注的办法，每 1,000 条约 ¥{uncached1000:.2f}（按未缓存单价）；一起放进已有形成请求，12 组配对实测的增量约 ¥{formation['incremental_cny_per_1000_uncached']:.2f}/1,000 条。形成时内容会发给云端 MiMo，本次发送的全是合成内容。旧记忆没有旁注就保留原行为；回填会产生同样的生成用量。")
    model = scale["embedding"]
    model_bytes = model["files"]["model_qint8_avx512_vnni.onnx"]["bytes"]
    add(f"B 没有云端词元费用，记忆和问句在本机处理。权重 {model_bytes / 1e6:.1f} MB，含分词器等下载约 {sum(p['bytes'] for p in model['files'].values()) / 1e6:.1f} MB；加载 {model['load_ms'] / 1000:.2f} 秒，进程加载后常驻内存约 {model['rss_after_load_bytes'] / 1e6:.0f} MB。要保持毫秒级查询需要常驻模型；临时加载会再付加载时间。每条向量的纯数据 1,536 字节。A+B 同时承担两种生成与存储成本。")
    add(f"隔离 SQLite（嵌入式数据库）侧表实测每 1,000 条多占：A 约 {scale['storage']['1000']['alias_extra_bytes']/1e6:.2f} MB，B 约 {scale['storage']['1000']['vector_extra_bytes']/1e6:.2f} MB。B 在 50,000 条上的纯向量搜索中位约 {scale['matrix_search']['50000']['p50_ms']:.2f} ms，但当前真实整条链路单次约 {full['sizes']['50000']['B']/1000:.1f} 秒，仍需正式的候选接口解决整库扫描。")
    add("")
    safe = [arm for arm in ("A", "B", "AB") if sections["审查"]["arms"][arm]["protections"]["required_negative_pass"] == 10 and sections["审查"]["arms"][arm]["protections"]["required_positive_pass"] == 8 and sections["审查"]["arms"][arm]["protections"]["new_false_queries"] == 0]
    if safe:
        best = max(safe, key=lambda arm: sections["盲测"]["arms"][arm]["overall"]["hit5"])
        add(f"**建议优先继续验证 {best}，暂不直接上线。** 它通过本次审查必过项；盲测的命中与误召见上表。上线前仍须解决大库完整链路扫描、旧库派生数据生命周期及真实问法分布验证。通过定向审查不等于所有实际技术问句都安全。")
    else:
        add("**建议两条都暂不直接上线。** A/合用未通过全部技术负例；B 通过 18 条必过探针，却在审查正例新增两次非目标返回，在盲测 195 问中有 91 问带了错误记忆。B 可以作为下一轮候选质量研究的主线，但需要能识别查询对象/用途的通用相关性判断，再用新的独立盲测验证；不能给失败问句逐条加词表。A 保留为来源受控的主题线索辅助，单独依靠它救回的盲测漏项有限。")
    add("")
    for arm in ("A", "B", "AB"):
        d, b = dev["arms"][arm]["overall"]["hit5"], sections["盲测"]["arms"][arm]["overall"]["hit5"]
        add(f"{arm} 开发 / 盲测前 5 条命中为 {percent(d)} / {percent(b)}，差 {(b-d)*100:+.2f} 个百分点。")
    add("两套合成数据的主题与句式不同，盲测由模型作者标注，缺少人工裁决；这些数字不能外推成本人的真实记忆命中率。若开发明显高而盲测下降，应作为过拟合证据，不用开发分数做宣传。本次没有真实数据上传或真实库验证。")
    add("B 的开发所有问句误召只有 5.56%，盲测却是 46.67%；开发新增错误预算并未迁移到新分布。**所选阈值对开发分布过拟合**，即使命中率在更易匹配的新集合上更高，也不能据此说泛化可靠。A 对应为 5.83% / 12.82%。")
    add("")
    add("## 方法、冻结与原链路")
    add("")
    add("盲测冻结提交 `c30f14b`，参数和实现冻结提交 `5f16102`，产品基于 `dc780326`。原开发/挑战/保留/审查四份都已暴露；只有开发用于选阈值，原四份全量回归 638 问仍与基线逐条一致。")
    add("候选先在临时合成库上算分，再交给原 `_current_world_rows`、原 `_match_world_rows` 的更正/历史/名称规则，并在最后调用原 `world_item_visible`。原 `QueryService`、快照、权限、账号、遗忘、过期和渲染全程保留。原命中按原顺序排在新增候选前；因此同时保留原有错误，并不声称修复了基线误召。")
    add("A 用 MiMo 从记忆本身生成最多八个别名，没有看问句；中文双字/英文整词 Dice（重叠系数）最大匹配。B 使用本机 multilingual-e5-small、384 维归一化向量、余弦相似度；纯 CPU（中央处理器）、两个线程，不请求本机端口、不启动显卡服务。合用版本取候选并集，阈值没有再调。")
    add("每份集合的正例数用于 Hit@1/3/5（前 1/3/5 条命中率）的分母；纯负例误召以该集合没有目标的问句为分母。另报所有问句的非目标误召，避免只看纯负例掩盖正例夹带错误。开发集一个事实有多个改述，样本相关；新盲测每场景只一个问句。")
    add("")
    add("## 三份集合的数字")
    add("")
    add("表中 baseline（现状基线）使用未修改的产品召回。所有百分比的分母列在集合名称中。")
    add("")
    add("| 集合（正/负） | 路线 | 前 1 | 前 3 | 前 5 | 纯负例误召 | 所有问句误召 | 新增错误问句 | 原命中丢失 |")
    add("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for split, data in sections.items():
        for arm in ("baseline", "A", "B", "AB"):
            result = data["baseline"] if arm == "baseline" else data["arms"][arm]
            o = result["overall"]
            p = result.get("protections", {})
            add(f"| {split}（{o['positive_n']}/{o['negative_query_n']}） | {arm} | {percent(o['hit1'])} | {percent(o['hit3'])} | {percent(o['hit5'])} | {percent(o['negative_query_false_recall_rate'])} | {percent(o['negative_false_recall_rate'])} | {p.get('new_false_queries', 0)} | {p.get('lost_baseline_hit5', 0)} |")
    add("")
    add("审查必须保留的 4 条原行为与 2 个人物名称 × 2 个权限层，共 8 条正例，单独计数；10 条技术负例必须每条空结果。")
    add("")
    add("| 路线 | 4 条原行为 | 4 条名称 | 10 条技术负例空结果 | 审查新增错误问句 | 全部必过项 |")
    add("| --- | ---: | ---: | ---: | ---: | --- |")
    for arm in ("baseline", "A", "B", "AB"):
        result = sections["审查"]["baseline"] if arm == "baseline" else sections["审查"]["arms"][arm]
        rows = result["results"]
        regressions = [r for r in rows if r["category"] == "review_regression"]
        names = [r for r in rows if r["must_hit"] and r["category"] != "review_regression"]
        neg = [r for r in rows if r["must_abstain"]]
        rp, np_, ng = sum(0 < r["rank"] <= 5 for r in regressions), sum(0 < r["rank"] <= 5 for r in names), sum(not r["selected"] for r in neg)
        add(f"| {arm} | {rp}/4 | {np_}/4 | {ng}/10 | {result.get('protections', {}).get('new_false_queries', 0)} | {'通过' if rp==4 and np_==4 and ng==10 else '未通过'} |")
    add("")
    add("原始审查失败示例：A 把 `running process怎么停止？` 召回成个人活动记忆；B 在 `吃苹果时怎么用手机记录？` 命中原目标的同时夹带一个干扰项，`subway出行怎么安排？` 返回共享库里另一条事实。这些失败没有用词表或调高阈值补救。B 虽然 18 条必过探针都满足，审查回归仍因新增非目标返回不合格。")
    add("")
    add("盲测新增技术多义词负例共 15 条：")
    add("")
    for arm in ("baseline", "A", "B", "AB"):
        result = sections["盲测"]["baseline"] if arm == "baseline" else sections["盲测"]["arms"][arm]
        cat = result["categories"]["technical_polysemy"]
        add(f"- {arm}：{sum(bool(r['wrong']) for r in result['results'] if r['category']=='technical_polysemy')}/15 误召（{percent(cat['negative_query_false_recall_rate'])}）。")
    add("")
    add("每条样本的候选、排名、非目标、类别及原始耗时见 `results/development.json` 与 `results/final.json`。没有删除失败样本、改标签或修改原回归门。")
    add("保存的 `lifecycle_leaks` 指标将生命周期类问句的任何返回都计入：B/合用盲测各有 2 问返回无关但 active 的干扰项。它们是相关性错误，不是屏蔽记录泄漏。最终只读审计 `results/derived-audit.json` 按保存结果与状态逐条核对：所有集合、所有路线实际返回被屏蔽记录都是 0；没有重跑盲测或修改原指标。独立强制候选测试覆盖十类屏蔽状态。")
    add("")
    add("## 阈值与误召曲线")
    add("")
    add("只在开发集扫描：A 每 0.05 一档，B 每 0.01 一档。允许最多 3/360 个新增非目标误召问句，但不允许纯负例增加误召、已有命中丢失或生命周期泄漏。按前 5 条命中最多选择，并列先少误召再取较高阈值。最终 A=0.30、B=0.86；选择规则和完整每档指标在 `results/parameters.json`、`results/development.json`。")
    add("图中的纯负例错误率包含基线的 13/60 个错误；虚线表示新增错误问句占所有问句的比例，避免基线错误遮住新增风险。没有在盲测上重新扫阈值。")
    add("")
    add("![开发集阈值与命中、误召的关系](results/threshold-curves.svg)")
    add("")
    add("| B 余弦阈值 | 开发前 5 条命中 | 开发纯负例误召 | 新增错误问句 |")
    add("| ---: | ---: | ---: | ---: |")
    for p in dev["threshold_curves"]["B"]:
        if p["threshold"] in {.65, .70, .75, .80, .85, .86, .90, .95, .99, 1.001}:
            add(f"| {p['threshold']:.3f} | {percent(p['hit5'])} | {percent(p['negative_query_false_recall_rate'])} | {p['new_false_queries']}/360 |")
    add("")
    add("## 成本、速度、内存和旧库")
    add("")
    add(f"测量环境：{hardware['cpu'].strip()}，{hardware['physical_cores']} 核 / {hardware['logical_processors']} 逻辑处理器，{hardware['os']}；ONNX（开放神经网络交换格式）仅用两个 CPU 线程。机器为共享工作环境，完整链路先基线后 B，仅一次成对观察，缓存和其他任务可影响结果；不根据这些单次时间宣称 B 比基线更快或更慢。")
    add("")
    add("小库质量评测的完整链路耗时，含连接、当前状态、候选计算、原过滤、最近 Evidence、待更正信息和渲染；A 不计算向量，B 每次实算问句向量，没有问句向量缓存。开发集每问三次，最终盲测/审查每问一次，另有只读校验调用；共享电脑上的延迟仅作描述。")
    add("")
    add("| 集合 | 路线 | 中位 ms | p95（第 95 百分位）ms |")
    add("| --- | --- | ---: | ---: |")
    for split, data in sections.items():
        for arm in ("baseline", "A", "B", "AB"):
            o = (data["baseline"] if arm == "baseline" else data["arms"][arm])["overall"]
            add(f"| {split} | {arm} | {o['latency_ms']['p50']:.3f} | {o['latency_ms']['p95']:.3f} |")
    add("")
    add(f"B 逐条写入编码实测 100 条：中位 {scale['single_write']['p50_ms']:.3f} ms，p95 {scale['single_write']['p95_ms']:.3f} ms；问句编码实测 60 条：中位 {scale['query_encode']['p50_ms']:.3f} ms。批量 16 条编码回填实测 1,000 条 {scale['batch_backfill']['seconds']:.3f} 秒。")
    add("")
    add("| 记忆数 | 纯向量检索中位/p95 ms | 加问句编码的平均 ms | 原完整链路基线/B ms（单次） | B 回填秒（1,000 实算，其余估计） | 别名额外库大小 | 向量额外库大小 |")
    add("| ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for n in (1000, 10000, 50000):
        s, f, storage = scale["matrix_search"][str(n)], full["sizes"][str(n)], scale["storage"][str(n)]
        add(f"| {n:,} | {s['p50_ms']:.3f}/{s['p95_ms']:.3f} | {s['mean_with_query_encode_ms']:.3f} | {f['baseline']:.1f}/{f['B']:.1f} | {scale['batch_backfill']['estimated_seconds'][str(n)]:.2f} | {storage['alias_extra_bytes']/1e6:.2f} MB | {storage['vector_extra_bytes']/1e6:.2f} MB |")
    add("")
    add("纯向量报告是预载矩阵的精确余弦点积与前 32 条排序，1 万/5 万由 1,000 个实算向量平铺；它测容量和内存带宽，不测大库质量，也不含权限过滤。完整链路表在真实临时 World/Evidence 中播种全部条目，每规模只有一个基线/B 成对观察，不给 p95 保证。原链路逐条扫描和前项查询仍可能主导大库延迟；本包不修产品性能，也没有把矩阵耗时冒充最终召回耗时。")
    add("存储是隔离 SQLite（嵌入式数据库）表的文件差值，含页开销；向量 float32（32 位浮点数）每条原始 1,536 字节，别名按开发集旁注循环。真实落库还要版本、来源、账号、模型信息、更新/删除记录，因此不能把这份侧表估计当作最终数据库迁移成本。")
    add("A 无旁注旧记忆完全回到原行为；独立回填约每千条一个千次请求，费用见上面的未缓存估计，速度受 MiMo 延迟与并发限制。B 未回填旧记录保留原字面路径；后台只对权限允许的记忆实算向量并记录进度，换模型/维度需重新回填。计时按合成短句，较长记忆、更低功耗 CPU 和并发负载需要另量。")
    cache_entries = [*read(OUT / "aliases-dev.json")["entries"].values(), *read(OUT / "aliases-final.json")["entries"].values()]
    alias_ms = sorted(e["latency_ms"] for e in cache_entries)
    mean_in = (dev_cost["usage"]["prompt_tokens"] + final_cost["usage"]["prompt_tokens"]) / n_alias
    mean_out = (dev_cost["usage"]["completion_tokens"] + final_cost["usage"]["completion_tokens"]) / n_alias
    add(f"A 独立生成旁注的实测请求中位 {alias_ms[len(alias_ms)//2]/1000:.3f} 秒，p95 {alias_ms[min(len(alias_ms)-1, int(len(alias_ms)*.95))]/1000:.3f} 秒（6 并发、含排队/网络）。平均每条输入 {mean_in:.1f}、输出 {mean_out:.1f} 词元；1,000 条旧记忆回填约 1,000 请求、输入 {mean_in*1000:.0f}、输出 {mean_out*1000:.0f} 词元。旁注原始 UTF-8 JSON（通用数据交换格式）平均 {(dev_cost['sidecar_utf8_bytes']+final_cost['sidecar_utf8_bytes'])/n_alias:.1f} 字节，不含来源与版本元数据。")
    add("")
    add("## MiMo 实测用量")
    add("")
    add("| 用途 | 请求数 | 输入词元 | 输出词元 | 备注 |")
    add("| --- | ---: | ---: | ---: | --- |")
    manifest = read(HERE / "blind-manifest.json")
    add(f"| 独立盲测作者 | {manifest['requests']} | {manifest['usage']['prompt_tokens']} | {manifest['usage']['completion_tokens']} | 含数量不足后固定结构补齐的请求 |")
    for name, cost in (("A 开发旁注", dev_cost), ("A 最终旁注", final_cost)):
        add(f"| {name} | {cost['requests']} | {cost['usage']['prompt_tokens']} | {cost['usage']['completion_tokens']} | 每个不同合成记忆一次请求；失败旁注计费后退为空 |")
    for arm in ("current", "extended"):
        t = formation["totals"][arm]
        add(f"| 真实形成提示词 {'现状' if arm=='current' else '追加旁注'} | {t['requests']} | {t['prompt_tokens']} | {t['completion_tokens']} | {t['formed_cognitions']} 个认知，12 个输入 |")
    add("早期生成器有两次开发中断：独立作者第一次 5 请求的词元未保留；A 初版要求最多八个别名而断言中断，三个完成旁注的用量已进入开发表，其余已发出请求数/词元没有完整日志，不能给整个包精确总账。之后在解码前记录每次请求用量，并采用有界处理。上表是可复核的请求/词元，不将未知项计为零。")
    known_requests = manifest['requests'] + n_alias + sum(formation['totals'][a]['requests'] for a in ('current', 'extended'))
    known_input = manifest['usage']['prompt_tokens'] + dev_cost['usage']['prompt_tokens'] + final_cost['usage']['prompt_tokens'] + sum(formation['totals'][a]['prompt_tokens'] for a in ('current', 'extended'))
    known_output = manifest['usage']['completion_tokens'] + dev_cost['usage']['completion_tokens'] + final_cost['usage']['completion_tokens'] + sum(formation['totals'][a]['completion_tokens'] for a in ('current', 'extended'))
    add(f"可追溯请求合计 {known_requests} 次、输入 {known_input:,}、输出 {known_output:,} 词元；另有上述早期未知项，不声称这是整个包的精确总请求或总花费。")
    add(f"开发和最终旁注合计已记录 {n_alias} 条，按实际返回的缓存词元估算 ¥{dev_cost['estimated_cny']+final_cost['estimated_cny']:.6f}；按全未缓存估算 ¥{dev_cost['estimated_cny_uncached']+final_cost['estimated_cny_uncached']:.6f}。两者均为估算而非账单。形成配对实测多出输入 {formation['incremental']['prompt_tokens']}、输出 {formation['incremental']['completion_tokens']} 词元，增加请求数为 0。")
    add("价格在 2026-10-11 从 [MiMo 官方按量计费文档](https://mimo.mi.com/static/docs/price/pay-as-you-go.md)读取：mimo-v2.6-flash 输入/缓存输入/输出分别 ¥1/¥0.02/¥2 每百万词元。回填通常仍多出一次请求；一起形成的 ¥0.29 仅是本次十二对短句估计，已有形成输出、缓存与模型决策会影响增量。")
    add("")
    add("## 开源模型来源与许可")
    add("")
    add(f"[intfloat/multilingual-e5-small 发布者模型文件]({model['model_source']})，固定版本 `{model['revision']}`，MIT 许可见 [发布者模型卡]({model['license_source']})。使用原发布者量化文件，不执行远程代码，不安装/启动任何服务。下载文件只在 `.local/`；中文和英文使用同一个模型，query/passage 前缀、池化、归一化和每个文件的散列均记录在 `results/scale.json`。")
    add("")
    add("## 真要落地还需做什么")
    add("")
    add("- A：在现有来源受控的主题别名机制上明确扩展字段契约，区分等价别名与上位概念；验证生成失败、身份名称、否定与权限变化；用同请求生成代替本次独立请求时，再验证是否改变形成认知。扩展信封目前不被真实编译器接受，本包没有加字段或迁移。")
    add("- B：需要正式的候选提供接口，让向量索引先取候选、原过滤最后逐个校验，避免本次评测挂接仍走整库扫描；接口须保留当前更正/历史/人物规则。未自行增加产品挂接点。CPU 模型加载、按账号隔离、索引版本、删除与权限撤销、向量清理、回填、崩溃恢复和磁盘占用均需产品实现与验证。")
    add("- A+B：需要统一分数/重复候选策略与新增误召预算，不能直接把两个集合相加后宣称安全；本次保留原候选的并集已经给出实测。")
    add("- 多义词：用通用、可评估的相关性或查询意图方法解决，再生成另一份独立盲测。不得围绕审查失败词补手写词表，也不得把已公开盲测重新当盲测。")
    add("")
    add("## 验证与遗留")
    add("")
    add("- `npm ci --no-audit --no-fund`、`uv sync --extra dev`，实验依赖仅在 `.local/mem5/venv`，产品依赖文件不改。")
    add("- 按本 Core 仓库 `.github/workflows/ci.yml` 的命令验收；编排规则中 WeftMate 专用的 required/vendor 脚本不属于本仓库，没有跨目录运行或改动 WeftMate。")
    add("- CI（持续集成）同命令：`npm run test:coverage` 636/636；`uv run pytest -q` 1,928/1,928；`npm test --workspaces --if-present` 817/817（含根 636，与前面的根测试重复，不能相加成独立总数）。")
    add("- `npm run typecheck`、各工作区 `npm run typecheck --workspaces --if-present`、`uv run mypy --strict src tests`（223 个文件）通过。各工作区构建、lint、format、API 快照、共享资产、文档链接、发行版烟测通过。")
    add("- `uv run python ../bench/recall-quality/evaluate.py --check --exact-baseline --repeats 3`：638 问，四份集合逐条一致，回归门通过。")
    add("- `test_routes.py` 6/6：强制候选后十类屏蔽状态不泄漏、更正前项不返还、人物名称和原顺序保留、无旁注回退、异常后恢复挂接、A 不调用嵌入模型。")
    add("")
    add("重活占用按顺序：第 1 次 Core coverage（覆盖率测试），第 2 次完整 Python，第 3 次工作区完整测试；第 4–6 次开发实验（前两次分别遇到别名数量断言、临时连接未关，最后一次完成），第 7 次固定阈值重计时；第 8 次最终盲测/审查，第 9 次矩阵/回填/存储，第 10 次真实完整链路容量。记录见 `results/verification.json`。每段占一个原子名额，结束/失败都在 finally 释放；无同包并行重活。机器上没有启动大模型、显卡任务或长期服务。")
    add("")
    add("遗留：模型作者标签未人工裁决；早期中断调用日志不全；质量大库/真实记忆分布未验证；容量完整链路每规模只有一次成对观察；回填大规模时间是估计；新相关性判断、产品候选接口和派生数据生命周期尚未实现。")
    add("")
    add("已清理进程：0。所有已启动执行会话正常退出，无需强杀；交付前再检查本包路径和创建时间对应的残留。")
    add("")
    add("云端检查与合 main 后复验见交付追加记录。")
    (HERE / "result.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    render_curves(dev, final)
    print("Report and development threshold figure written")


def render_curves(dev, final):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    for ax, arm in zip(axes, ("A", "B")):
        curve = dev["threshold_curves"][arm]
        x = [p["threshold"] for p in curve]
        ax.plot(x, [100*p["hit5"] for p in curve], label="Hit@5", color="#1466b8")
        ax.plot(x, [100*p["negative_query_false_recall_rate"] for p in curve], label="Pure-negative false recall", color="#b44a38")
        ax.plot(x, [100*p["new_false_queries"]/360 for p in curve], label="New false queries / all", linestyle="--", color="#8a408a")
        ax.axvline(final["thresholds"][arm], color="#333", linewidth=1, linestyle=":", label="Selected dev threshold")
        ax.set(title=arm + (": alias Dice" if arm == "A" else ": cosine similarity"), xlabel="Threshold", ylabel="Percent", ylim=(-1, 101))
        ax.grid(alpha=.2)
        ax.legend(fontsize=8)
    svg_path = OUT / "threshold-curves.svg"
    fig.savefig(svg_path)
    svg_path.write_text("\n".join(line.rstrip() for line in svg_path.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8", newline="\n")
    fig.savefig(HERE.parents[2] / ".local/mem5/threshold-curves.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
