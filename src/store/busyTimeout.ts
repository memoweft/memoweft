/**
 * SQLite 并发保底：等锁重试时长（毫秒）。
 *
 * 每条【自开】连接开库后都设 `PRAGMA busy_timeout = BUSY_TIMEOUT_MS`：
 *   写锁被其他进程占用时，SQLite 最多等待该时长再报告 SQLITE_BUSY，而不是立即抛出。
 *   单进程内 DatabaseSync 全同步 API 天然串行、不需要它；多进程同库并发写才用得上
 *   （如 Host 与 testbench 指向同一个库文件）。
 *
 * 这是底座参数、不是产品参数——刻意不进 config（进 config 会扩公共接口面）。
 * 各自开连接处统一引这个常量、别写魔法数。
 */
export const BUSY_TIMEOUT_MS = 5000;
