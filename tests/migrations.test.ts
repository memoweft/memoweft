/**
 * Schema 版本化 + 迁移器护栏（0.2.0 兼容性清理）。
 * 核心验收：真·0.1.0 fixture 库（tests/fixtures/memoweft-0.1.0.db，user_version=0）经 openStores 打开
 *   → 无损升到最新版、数据一条不少。
 * 另验：降级防护（未来版本建的库拒绝打开）/ fresh vs 迁移库 schema 签名一致 / 新库直接盖版 /
 *   假 v2 迁移真 ALTER+备份+升版号 / dry-run 不改库 / 迁移抛错整段回滚 / 幂等。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from '../src/store/nodeSqliteDriver.ts';
import { mkdtempSync, rmSync, existsSync, copyFileSync } from 'node:fs';
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

const FIXTURE_010 = join(import.meta.dirname, 'fixtures', 'memoweft-0.1.0.db');

function tempDir(): { dir: string; cleanup: () => void } {
  const dir = mkdtempSync(join(tmpdir(), 'mw-mig-'));
  return { dir, cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}
const uv = (db: DatabaseSync): number => getSchemaVersion(db);

/** 一个库的 schema 签名：每张表的列（名:类型:notnull:pk）排序拼串，用来比"两条建库路径是否收敛到同一 schema"。 */
function schemaSignature(db: DatabaseSync): string {
  const tables = (
    db
      .prepare(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
      )
      .all() as Array<{ name: string }>
  ).map((t) => t.name);
  return tables
    .map((t) => {
      const cols = (
        db
          .prepare(`SELECT name, type, "notnull", pk FROM pragma_table_info('${t}')`)
          .all() as Array<{
          name: string;
          type: string;
          notnull: number;
          pk: number;
        }>
      )
        .map((c) => `${c.name}:${c.type}:${c.notnull}:${c.pk}`)
        .sort()
        .join(',');
      return `${t}(${cols})`;
    })
    .join('|');
}

test('★ 真·0.1.0 fixture 无损升级：user_version=0 的库经 openStores 打开 → 盖最新版 + 数据不丢', () => {
  const { dir, cleanup } = tempDir();
  try {
    // 拷一份 fixture 到临时目录再打开（openStores 会写 user_version，别动仓库里那份冻结基线）。
    const path = join(dir, 'old.db');
    copyFileSync(FIXTURE_010, path);
    const stores = openStores(path);
    try {
      assert.equal(uv(stores.db), LATEST_SCHEMA_VERSION, '升到最新版');
      assert.equal(stores.evidenceStore.all().length, 2, '2 条证据没丢');
      const cogs = stores.cognitionStore.all('demo');
      assert.equal(cogs.length, 2, '2 条认知没丢');
      assert.ok(
        cogs.some((c) => c.content === '偏好夜间工作'),
        '认知内容原样',
      );
    } finally {
      stores.close();
    }
  } finally {
    cleanup();
  }
});

test('降级防护：库版本高于本代码支持的最新版 → 拒绝打开（不静默放行）', () => {
  const { dir, cleanup } = tempDir();
  try {
    const path = join(dir, 'future.db');
    // 造"未来版本建的库"：先 openStores 建个【有效 schema】的正常库，再把 user_version 手动顶到远高于 latest。
    openStores(path).close();
    {
      const db = new DatabaseSync(path);
      db.exec(`PRAGMA user_version = ${LATEST_SCHEMA_VERSION + 6}`);
      db.close();
    }
    assert.throws(() => openStores(path), /高于当前 MemoWeft|newer/i, '旧代码打开未来库应抛错');
  } finally {
    cleanup();
  }
});

test('两条路收敛：fresh 建的库 vs 从 0.1.0 fixture 迁上来的库，schema 签名必须一致', () => {
  const { dir, cleanup } = tempDir();
  try {
    // A：全新库（store 建最新 schema）
    const freshPath = join(dir, 'fresh.db');
    const fresh = openStores(freshPath);
    const sigFresh = schemaSignature(fresh.db);
    fresh.close();
    // B：从 0.1.0 fixture 迁上来的库
    const migPath = join(dir, 'migrated.db');
    copyFileSync(FIXTURE_010, migPath);
    const migrated = openStores(migPath);
    const sigMigrated = schemaSignature(migrated.db);
    migrated.close();
    assert.equal(sigMigrated, sigFresh, '迁移库与新库 schema 必须一致（否则"两处同改"忘了一处）');
  } finally {
    cleanup();
  }
});

test('新库：openStores 建库直接盖最新版本号（不跑迁移）', () => {
  const { dir, cleanup } = tempDir();
  try {
    const stores = openStores(join(dir, 'new.db'));
    try {
      assert.equal(uv(stores.db), LATEST_SCHEMA_VERSION);
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
  const { dir, cleanup } = tempDir();
  try {
    const path = join(dir, 'v1.db');
    openStores(path).close(); // 先建一个当前版本(v1)的库
    const fakeV2: Migration = {
      version: 2,
      name: 'test-add-col',
      up: (db) => db.exec('ALTER TABLE cognition ADD COLUMN test_col TEXT'),
    };
    const db = new DatabaseSync(path);
    try {
      const r = runMigrations(db, {
        dbPath: path,
        fresh: false,
        migrations: [...MIGRATIONS, fakeV2],
      });
      assert.equal(r.from, 1);
      assert.equal(r.to, 2);
      assert.deepEqual(r.applied, [2]);
      assert.ok(r.backupPath && existsSync(r.backupPath), '迁移前备份文件在');
      assert.equal(uv(db), 2);
      const cols = db.prepare("SELECT name FROM pragma_table_info('cognition')").all() as Array<{
        name: string;
      }>;
      assert.ok(
        cols.some((c) => c.name === 'test_col'),
        '新列 test_col 真加上了',
      );
    } finally {
      db.close();
    }
  } finally {
    cleanup();
  }
});

test('dry-run：只报计划、不改库', () => {
  const { dir, cleanup } = tempDir();
  try {
    const path = join(dir, 'v1.db');
    openStores(path).close();
    const fakeV2: Migration = {
      version: 2,
      name: 'test',
      up: (db) => db.exec('ALTER TABLE cognition ADD COLUMN x TEXT'),
    };
    const db = new DatabaseSync(path);
    try {
      const r = runMigrations(db, {
        dbPath: path,
        fresh: false,
        migrations: [...MIGRATIONS, fakeV2],
        dryRun: true,
      });
      assert.equal(r.dryRun, true);
      assert.deepEqual(r.applied, [2]);
      assert.equal(uv(db), 1, '库版本号没被动');
      const cols = db.prepare("SELECT name FROM pragma_table_info('cognition')").all() as Array<{
        name: string;
      }>;
      assert.ok(!cols.some((c) => c.name === 'x'), 'dry-run 没真加列');
    } finally {
      db.close();
    }
  } finally {
    cleanup();
  }
});

test('迁移抛错 → 整段回滚，版本号不变、库不留半迁移', () => {
  const { dir, cleanup } = tempDir();
  try {
    const path = join(dir, 'v1.db');
    openStores(path).close();
    const badV2: Migration = {
      version: 2,
      name: 'test-boom',
      up: (db) => {
        db.exec('ALTER TABLE cognition ADD COLUMN half TEXT');
        throw new Error('boom');
      },
    };
    const db = new DatabaseSync(path);
    try {
      assert.throws(
        () => runMigrations(db, { dbPath: path, fresh: false, migrations: [...MIGRATIONS, badV2] }),
        /boom/,
      );
      assert.equal(uv(db), 1, '版本号仍是 1（未升）');
      const cols = db.prepare("SELECT name FROM pragma_table_info('cognition')").all() as Array<{
        name: string;
      }>;
      assert.ok(!cols.some((c) => c.name === 'half'), '半截 ALTER 被回滚');
    } finally {
      db.close();
    }
  } finally {
    cleanup();
  }
});

test('幂等：已是最新版再 runMigrations，啥都不做', () => {
  const { dir, cleanup } = tempDir();
  try {
    const stores = openStores(join(dir, 'x.db'));
    try {
      const r = runMigrations(stores.db, { fresh: false });
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
