/**
 * 事务执行器类型（写路径一致性）。
 *
 * 把一段【同步】写包成一个 SQLite 事务：全成或全滚。实现见 store/openStores.ts（可重入：已在事务里再调只直接跑，不嵌套 BEGIN）。
 * 单列成叶子文件，好让 consolidate / updateProfile 只 `import type` 这个类型，不必牵进整套 store 连接装配。
 *
 * ⚠️ 只能包【同步】写：LLM 调用是异步网络请求，不得在 fn 中 await（持有写锁等待网络或交错执行都会破坏事务边界）。
 */
export type Transaction = <T>(fn: () => T) => T;

/** 缺省事务器：不开事务、直接跑（用于没接共享连接的场景，如各开各连接的单元测试——行为同旧）。 */
export const noopTransaction: Transaction = (fn) => fn();
