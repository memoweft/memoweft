/**
 * better-sqlite3 可选驱动（T6 步2）验收。
 *
 * 两类断言：
 *   A. 零依赖路径（Node 24 默认，无 better-sqlite3 也能跑）：activeDriver 应为 'node:sqlite'。
 *      —— 除非用 MEMOWEFT_TEST_DRIVER 强制了别的驱动（Node 22 job 会这么干），那时跳过这条。
 *   B. better-sqlite3 驱动本体：只有【检测得到 better-sqlite3】时才跑，否则整组 skip
 *      —— 保证本机 Node 24 无 better-sqlite3 时三绿仍全绿（校对：不装原生模块 = 全 skip，非红）。
 *
 * 为什么直接测 makeBetterSqlite3Constructor() 而不靠全局强制：驱动选择在模块顶层【一次性】急切执行，
 *   进程内改 env 也换不动已定的 activeDriver。要独立验第二驱动的行为，就直接拿它的构造器打 CRUD。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { activeDriver } from '../src/store/nodeSqliteDriver.ts';
import { makeBetterSqlite3Constructor } from '../src/store/betterSqlite3Driver.ts';

/** 本机能否 require 到 better-sqlite3（原生模块，可选依赖，默认没装）。检测不到 → 整组 skip。 */
function hasBetterSqlite3(): boolean {
  try {
    createRequire(import.meta.url)('better-sqlite3');
    return true;
  } catch {
    return false;
  }
}

const HAS_BETTER = hasBetterSqlite3();
const skipBetter = HAS_BETTER ? false : 'better-sqlite3 未安装（可选依赖）——跳过第二驱动断言';

// ── A. 零依赖路径 ────────────────────────────────────────────────────────────
// 没强制别的驱动时，默认应选中零依赖的 node:sqlite（Node ≥24）。这条验"零依赖卖点没被做没"。
test('activeDriver：未强制驱动时默认走零依赖的 node:sqlite', {
  skip: process.env.MEMOWEFT_TEST_DRIVER ? `已用 MEMOWEFT_TEST_DRIVER=${process.env.MEMOWEFT_TEST_DRIVER} 强制驱动，跳过默认路径断言` : false,
}, () => {
  assert.equal(activeDriver, 'node:sqlite');
});

// ── B. better-sqlite3 驱动本体（无它则整组 skip） ─────────────────────────────

test('better-sqlite3 驱动：开库 / exec 建表 / 位置参数写读 / changes / close 全链通', { skip: skipBetter }, () => {
  const DatabaseSync = makeBetterSqlite3Constructor();
  const db = new DatabaseSync(':memory:');
  try {
    db.exec('CREATE TABLE t (id TEXT PRIMARY KEY, n INTEGER NOT NULL)');
    const ins = db.prepare('INSERT INTO t (id, n) VALUES (?, ?)').run('a', 1);
    assert.equal(Number(ins.changes), 1, 'run 返回 changes=1');

    const row = db.prepare('SELECT * FROM t WHERE id = ?').get('a') as { id: string; n: number } | undefined;
    assert.deepEqual(row, { id: 'a', n: 1 }, 'get 位置参数取回一行');

    db.prepare('INSERT INTO t (id, n) VALUES (?, ?)').run('b', 2);
    const all = db.prepare('SELECT * FROM t ORDER BY id').all() as Array<{ id: string; n: number }>;
    assert.equal(all.length, 2, 'all 取回全部');

    const del = db.prepare('DELETE FROM t WHERE id = ?').run('a');
    assert.equal(Number(del.changes), 1, 'delete 的 changes=1');

    const none = db.prepare('SELECT * FROM t WHERE id = ?').get('zzz');
    assert.equal(none, undefined, '无结果 get 返回 undefined');
  } finally {
    db.close();
  }
});

test('better-sqlite3 驱动：命名对象绑定（裸键配 $name 占位符）——与 node:sqlite 一致', { skip: skipBetter }, () => {
  const DatabaseSync = makeBetterSqlite3Constructor();
  const db = new DatabaseSync(':memory:');
  try {
    db.exec('CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT, note TEXT)');
    // 占位符用 $name，绑定对象用【裸键】（步1 已把全库统一成裸键）——better-sqlite3 与 node:sqlite 都收。
    db.prepare('INSERT INTO t (id, name, note) VALUES ($id, $name, $note)').run({
      id: 'x',
      name: 'Ann',
      note: null, // null 值透传（origin_id / corrects_evidence_id 等可为 null）
    });
    const row = db.prepare('SELECT * FROM t WHERE id = $id').get({ id: 'x' }) as
      | { id: string; name: string; note: string | null }
      | undefined;
    assert.deepEqual(row, { id: 'x', name: 'Ann', note: null }, '命名对象绑定写入 + 读取正确');
  } finally {
    db.close();
  }
});

test('better-sqlite3 驱动：PRAGMA user_version 读写（迁移器所依赖）', { skip: skipBetter }, () => {
  const DatabaseSync = makeBetterSqlite3Constructor();
  const db = new DatabaseSync(':memory:');
  try {
    const before = db.prepare('PRAGMA user_version').get() as { user_version?: number } | undefined;
    assert.equal(Number(before?.user_version ?? 0), 0, '新库 user_version=0');
    db.exec('PRAGMA user_version = 3'); // PRAGMA 不能参数化，迁移器就是这么写的
    const after = db.prepare('PRAGMA user_version').get() as { user_version?: number } | undefined;
    assert.equal(Number(after?.user_version ?? 0), 3, 'exec 写 user_version 生效');
  } finally {
    db.close();
  }
});

test('better-sqlite3 驱动：BEGIN/COMMIT/ROLLBACK 事务语义（openStores/迁移器所依赖）', { skip: skipBetter }, () => {
  const DatabaseSync = makeBetterSqlite3Constructor();
  const db = new DatabaseSync(':memory:');
  try {
    db.exec('CREATE TABLE t (id TEXT PRIMARY KEY)');
    db.exec('BEGIN');
    db.prepare('INSERT INTO t (id) VALUES (?)').run('a');
    db.exec('ROLLBACK');
    assert.equal((db.prepare('SELECT COUNT(*) c FROM t').get() as { c: number }).c, 0, 'ROLLBACK 撤销写入');

    db.exec('BEGIN');
    db.prepare('INSERT INTO t (id) VALUES (?)').run('b');
    db.exec('COMMIT');
    assert.equal((db.prepare('SELECT COUNT(*) c FROM t').get() as { c: number }).c, 1, 'COMMIT 保留写入');
  } finally {
    db.close();
  }
});
