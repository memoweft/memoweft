/**
 * 感知观测子系统 —— D-025。
 * 对应决策：D-025（裸文件缓冲 → 定时总结 → 沉淀进 SQLite）/ D-004（记≠信）/ D-007（权重自动压低）。
 * 阶段：独立子系统，可在主链路稳定后单独实现。
 *
 * 三步：
 * 1. 采集：感知数据持续 append 到裸文件缓冲（如 sensory_buffer.jsonl），不进 Event 库、不常驻内存。
 * 2. 触发：每 N 小时定时统计，无足够活动则跳过（时间闸门 + 活动燃料）。
 * 3. 沉淀：大模型读缓冲、总结成一条 Event 存入 SQLite（source_type=观测，topic 必填，
 *    event_form/sentiment/is_directional_change 留空——观测不是"表达"，严禁瞎填），清空缓冲。
 *
 * 存储分工：缓冲用裸文件（只追加/整批读/不查询/崩溃不丢），Event 用 SQLite（要被召回查询）。
 * 参数 N、活动阈值 → config.ts，运行后校准。
 */
export {};
