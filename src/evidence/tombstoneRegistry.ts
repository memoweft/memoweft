/**
 * Portable restore 的内部墓碑查询登记。
 *
 * `EvidenceStore` 是冻结的宿主契约，普通读路径也刻意看不见软删除记录；但同 id 的旧备份
 * 在恢复时必须识别该墓碑，不能以 `get() === null` 为由复活。实际 SQLite store 在构造时把
 * 自己与共享连接登记到此私有 WeakMap。这个模块不从包入口导出，非 SQLite 自定义 store 也不会
 * 因此获得新的宿主 API 要求。
 */
import type { EvidenceStore } from './store.ts';
import type { DatabaseSync } from '../store/nodeSqliteDriver.ts';

const connections = new WeakMap<object, DatabaseSync>();

export function registerEvidenceTombstoneReader(store: EvidenceStore, db: DatabaseSync): void {
  connections.set(store, db);
}

export function isEvidenceTombstoned(store: EvidenceStore, id: string): boolean {
  const db = connections.get(store);
  if (!db) return false;
  return (
    db
      .prepare('SELECT 1 AS present FROM evidence WHERE id = ? AND deleted_at IS NOT NULL')
      .get(id) != null
  );
}
