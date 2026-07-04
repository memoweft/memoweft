/**
 * Schema 版本化 + 迁移器护栏（0.2.0 硬债收口）。
 * 核心验收：用【0.1.0 式】库（user_version=0、有表有数据）经 openStores 打开 → 无损升到最新版、数据一条不少。
 * 另验：新库直接盖最新版 / 假 v2 迁移真 ALTER+备份+升版号 / dry-run 不改库 / 迁移抛错整段回滚。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { openStores } from '../src/store/openStores.ts';
import {
  runMigrations,
  getSchemaVersion,
  MIGRATIONS,
  LATEST_SCHEMA_VERSION,
  type Migration,
} from '../src/store/migrations.ts';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';

function tempDb(): { path: string; cleanup: () => void } {
  const dir = mkdtempSync(join(tmpdir(), 'mw-mig-'));
  return { path: join(dir, 'test.db'), cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

const uv = (db: DatabaseSync): number => getSchemaVersion(db);

test('★ 0.1.0 老库无损升级：user_version=0 的库经 openStores 打开 → 盖到最新版 + 数据不丢', () => {
  const { path, cleanup } = tempDb();
  try {
    // 造"0.1.0 式"库：直接构造 store 建表 + 插数据，【不经 runMigrations】，user_version 留 0。
    {
      const db = new DatabaseSync(path);
      const ev = new SqliteEvidenceStore(db);
      new SqliteEventStore(db); // 建 event 表，更像真 0.1.0 库
      const cog = new SqliteCognitionStore(db);
      ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: '我晚上写代码效率最高' });
      cog.put({ subjectId: 'u', content: '偏好夜间工作', contentType: 'preference', formedBy: 'stated', confidence: 600, credStatus: 'stable' });
      assert.equal(uv(db), 0, '老库版本号=0（从没 stamp 过）');
      db.close();
    }
    // 经 openStores（内部跑 runMigrations）打开：版本号盖到最新，数据还在。
    const stores = openStores(path);
    try {
      assert.equal(uv(stores.db), LATEST_SCHEMA_VERSION, '升到最新版');
      assert.equal(stores.evidenceStore.all().length, 1, '证据没丢');
      const cogs = stores.cognitionStore.all('u');
      assert.equal(cogs.length, 1, '认知没丢');
      assert.equal(cogs[0]!.content, '偏好夜间工作', '认知内容原样');
    } finally {
      stores.close();
    }
  } finally {
    cleanup();
  }
});

test('新库：openStores 建库直接盖最新版本号（不跑迁移）', () => {
  const { path, cleanup } = tempDb();
  try {
    const stores = openStores(path);
    try {
      assert.equal(uv(stores.db), LATEST_SCHEMA_VERSION, '新库=最新版');
    } finally {
      stores.close();
    }
  } finally {
    cleanup();
  }
});

test(':memory: 库视为新库，盖最新版', () => {
  const stores = openStores(':memory:');
  try {
    assert.equal(uv(stores.db), LATEST_SCHEMA_VERSION);
  } finally {
    stores.close();
  }
});

test('迁移器：假 v2 迁移会 ALTER + 迁移前备份 + 升版号（不碰生产迁移列表）', () => {
  const { path, cleanup } = tempDb();
  try {
    // 先建一个当前版本(v1)的库
    openStores(path).close();
    // 注入一条 test-only v2 迁移（加一列）——不进 MIGRATIONS 生产列表。
    const fakeV2: Migration = {
      version: 2,
      name: 'test-add-col',
      up: (db) => db.exec('ALTER TABLE cognition ADD COLUMN test_col TEXT'),
    };
    const db = new DatabaseSync(path);
    try {
      const r = runMigrations(db, { dbPath: path, fresh: false, migrations: [...MIGRATIONS, fakeV2] });
      assert.equal(r.from, 1, '迁移前 v1');
      assert.equal(r.to, 2, '迁移后 v2');
      assert.deepEqual(r.applied, [2], '只应用了 v2');
      assert.ok(r.backupPath && existsSync(r.backupPath), '迁移前备份文件在');
      assert.equal(uv(db), 2, '版本号已升到 2');
      const cols = db.prepare("SELECT name FROM pragma_table_info('cognition')").all() as Array<{ name: string }>;
      assert.ok(cols.some((c) => c.name === 'test_col'), '新列 test_col 真加上了');
    } finally {
      db.close();
    }
  } finally {
    cleanup();
  }
});

test('dry-run：只报计划、不改库', () => {
  const { path, cleanup } = tempDb();
  try {
    openStores(path).close();
    const fakeV2: Migration = { version: 2, name: 'test', up: (db) => db.exec('ALTER TABLE cognition ADD COLUMN x TEXT') };
    const db = new DatabaseSync(path);
    try {
      const r = runMigrations(db, { dbPath: path, fresh: false, migrations: [...MIGRATIONS, fakeV2], dryRun: true });
      assert.equal(r.dryRun, true);
      assert.deepEqual(r.applied, [2], '计划里有 v2');
      assert.equal(uv(db), 1, '库版本号没被动');
      const cols = db.prepare("SELECT name FROM pragma_table_info('cognition')").all() as Array<{ name: string }>;
      assert.ok(!cols.some((c) => c.name === 'x'), 'dry-run 没真加列');
    } finally {
      db.close();
    }
  } finally {
    cleanup();
  }
});

test('迁移抛错 → 整段回滚，版本号不变、库不留半迁移', () => {
  const { path, cleanup } = tempDb();
  try {
    openStores(path).close();
    const badV2: Migration = {
      version: 2,
      name: 'test-boom',
      up: (db) => {
        db.exec('ALTER TABLE cognition ADD COLUMN half TEXT'); // 先做一半
        throw new Error('boom'); // 再炸
      },
    };
    const db = new DatabaseSync(path);
    try {
      assert.throws(() => runMigrations(db, { dbPath: path, fresh: false, migrations: [...MIGRATIONS, badV2] }), /boom/);
      assert.equal(uv(db), 1, '版本号仍是 1（未升）');
      const cols = db.prepare("SELECT name FROM pragma_table_info('cognition')").all() as Array<{ name: string }>;
      assert.ok(!cols.some((c) => c.name === 'half'), '半截 ALTER 被回滚');
    } finally {
      db.close();
    }
  } finally {
    cleanup();
  }
});

test('幂等：已是最新版再 runMigrations，啥都不做', () => {
  const { path, cleanup } = tempDb();
  try {
    const stores = openStores(path);
    try {
      const r = runMigrations(stores.db, { dbPath: path, fresh: false });
      assert.equal(r.from, LATEST_SCHEMA_VERSION);
      assert.equal(r.to, LATEST_SCHEMA_VERSION);
      assert.deepEqual(r.applied, []);
    } finally {
      stores.close();
    }
  } finally {
    cleanup();
  }
});
