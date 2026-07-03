/**
 * 一条连接、三个 store、一个事务器——让写路径的多步、多表写能原子化（地图 cell 4 写路径一致性）。
 *
 * 为什么要它：三个 store 若各开各的 DatabaseSync 连接，SQLite 事务是【按连接】的，
 *   一条 BEGIN/COMMIT 跨不了两条连接——consolidate 既写 cognition 又写 event（markConsolidated），
 *   分在两条连接上就没法一起原子化。本函数让三个 store【共用一条连接】，于是 transaction() 能把它们的写一起提交/回滚。
 *
 * ⚠️ transaction 只包【同步】写：LLM 调用是异步网络请求，别把 await 塞进 transaction(fn)——
 *   既不该攥着写锁等网络，交错的 await 还会踩坏事务边界。写路径的正确用法是：先 await 拿到模型输出，
 *   再把随后的【同步写一段】交给 transaction（见 consolidation/consolidate.ts）。
 */
import { DatabaseSync } from 'node:sqlite';
import { SqliteEvidenceStore, type EvidenceStore } from '../evidence/store.ts';
import { SqliteEventStore, type EventStore } from '../event/store.ts';
import { SqliteCognitionStore, type CognitionStore } from '../cognition/store.ts';
import type { Transaction } from './transaction.ts';
import type { MemoWeftConfig } from '../config.ts';

export interface StoreBundle {
  /** 三个 store 共用的这条连接（一般不用直接碰；关它用 close()）。 */
  db: DatabaseSync;
  evidenceStore: EvidenceStore;
  eventStore: EventStore;
  cognitionStore: CognitionStore;
  /** 把一段【同步】写包进一个事务（可重入）。传给 consolidate / updateProfile 即让其写入原子化。 */
  transaction: Transaction;
  /** 关掉这条共享连接（统一在此关，别去关单个 store——单 store 的 close 对共享连接是 no-op）。 */
  close(): void;
}

/**
 * 开一条连接，装好三个 store 与事务器。dbPath 传文件路径或 ':memory:'。
 * @param cfg 可注入配置（P2-5 config 去单例）：不传 = 用全局单例；透给 evidence store 作 put 补授权默认。
 */
export function openStores(dbPath: string, cfg?: MemoWeftConfig): StoreBundle {
  const db = new DatabaseSync(dbPath);
  // 三个 store 都接同一条连接（构造里会各自 CREATE TABLE IF NOT EXISTS + 迁移，幂等）。
  // 只有 evidence store 的 put 会读 config 补授权默认，故只把 cfg 透给它（event/cognition 不读 config）。
  const evidenceStore = new SqliteEvidenceStore(db, cfg);
  const eventStore = new SqliteEventStore(db);
  const cognitionStore = new SqliteCognitionStore(db);

  // 事务深度：SQLite 不支持嵌套事务，只有最外层真 BEGIN/COMMIT；里层再调只直接跑（可重入）。
  let depth = 0;
  const transaction: Transaction = (fn) => {
    if (depth > 0) return fn(); // 已在事务里 → 直接跑，不再 BEGIN（否则报 "cannot start a transaction within a transaction"）
    depth++;
    db.exec('BEGIN');
    try {
      const r = fn();
      db.exec('COMMIT');
      return r;
    } catch (e) {
      db.exec('ROLLBACK'); // 任一步抛错 → 整段回滚（cognition 写入与 markConsolidated 一起回滚）
      throw e;
    } finally {
      depth--;
    }
  };

  return {
    db,
    evidenceStore,
    eventStore,
    cognitionStore,
    transaction,
    close: () => db.close(),
  };
}
