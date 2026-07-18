/**
 * 可注入时钟（demo 确定性 + 时间旅行）。
 *
 * 时间源做成可注入依赖：所有需要"现在"的落库/更新时间走注入的 Clock，而非散落的 new Date()。
 * 缺省 systemClock = 真实系统时间（行为不变）；测试/demo 注入固定或可前进的 clock 得到
 * 确定性（两次运行时间戳一致）+ 快进时钟（--fast-forward 让情绪衰减、事实留存）。
 *
 * Clock 只产【时间戳】，不参与置信度计算——衰减仍是读时基于 updatedAt
 * 与 clock() 之差，置信度底分由 FormedBy 规则算，不吃时间。
 */

/** 返回"现在"的函数。注入它即可固定/前进时间（确定性测试、demo --fast-forward、回放）。 */
export type Clock = () => Date;

/** 缺省时钟：真实系统时间。不注入 Clock 时的行为与历史散落的 new Date() 一致。 */
export const systemClock: Clock = () => new Date();
