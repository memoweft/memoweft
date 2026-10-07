/**
 * Schema 版本化 + 迁移器护栏（0.2.0 兼容性清理）。
 * 核心验收：真·0.1.0 fixture 库（tests/fixtures/memoweft-0.1.0.db，user_version=0）经 openStores 打开
 *   → 无损升到最新版、数据一条不少。
 * 另验：降级防护（未来版本建的库拒绝打开）/ fresh vs 迁移库 schema 签名一致 / 新库直接盖版 /
 *   自定义下一版迁移真 ALTER+备份+升版号 / dry-run 不改库 / 迁移抛错整段回滚 / 幂等。
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
    // 造 v7 的"未来版本库"：先建有效 schema，再把 user_version 手动升到当前 v6 之后。
    openStores(path).close();
    {
      const db = new DatabaseSync(path);
      db.exec('PRAGMA user_version = 7');
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

test('v2：rc.1 撤回台账对应的 cognition 与关联行会整体删除，并按框架备份/升版', () => {
  const { dir, cleanup } = tempDir();
  try {
    const path = join(dir, 'rc1.db');
    const seed = openStores(path);
    const evidence = seed.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 'test',
      rawContent: '已经撤回的原话',
    });
    const cognition = seed.cognitionStore.put({
      subjectId: 'owner',
      content: '仍会泄露的旧派生认知',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
      evidence: [{ evidenceId: evidence.id, relation: 'support' }],
    });
    seed.evidenceStore.remove(evidence.id);
    seed.db
      .prepare(
        'INSERT INTO evidence_retraction (cognition_id, evidence_id, retracted_at) VALUES (?,?,?)',
      )
      .run(cognition.id, evidence.id, '2026-07-30T00:00:00.000Z');
    seed.db.exec('PRAGMA user_version = 1');
    seed.close();

    const db = new DatabaseSync(path);
    try {
      const result = runMigrations(db, { dbPath: path, fresh: false });
      assert.deepEqual(result.applied, [2, 3, 4, 5, 6]);
      assert.equal(result.to, LATEST_SCHEMA_VERSION);
      assert.ok(result.backupPath && existsSync(result.backupPath), 'v2 数据迁移前留下备份');
      assert.equal(
        Number(db.prepare('SELECT COUNT(*) AS n FROM cognition').get()?.n),
        0,
        '旧派生认知被删除',
      );
      assert.equal(
        Number(db.prepare('SELECT COUNT(*) AS n FROM cognition_evidence').get()?.n),
        0,
        '旧认知关联行被删除',
      );
      assert.equal(
        Number(db.prepare('SELECT COUNT(*) AS n FROM evidence_retraction').get()?.n),
        0,
        '撤回台账行被删除',
      );
      assert.equal(uv(db), LATEST_SCHEMA_VERSION);
    } finally {
      db.close();
    }
  } finally {
    cleanup();
  }
});

test('v3：现有 v2 主库新增 2.0 world/identity 表，1.0 数据原样保留', () => {
  const { dir, cleanup } = tempDir();
  try {
    const path = join(dir, 'v2.db');
    const seed = openStores(path);
    const evidence = seed.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 'test',
      rawContent: '保留的 1.0 数据',
    });
    for (const table of [
      'identity_state',
      'cognition_transitions',
      'proposals',
      'evidence_ledger',
      'memory_state',
    ]) {
      seed.db.exec(`DROP TABLE "${table}"`);
    }
    seed.db.exec('PRAGMA user_version = 2');
    seed.close();

    const upgraded = openStores(path);
    try {
      assert.equal(uv(upgraded.db), 6);
      assert.equal(upgraded.evidenceStore.get(evidence.id)?.rawContent, '保留的 1.0 数据');
      const tables = new Set(
        (
          upgraded.db
            .prepare(
              "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
            )
            .all() as Array<{ name: string }>
        ).map((row) => row.name),
      );
      for (const table of [
        'memory_state',
        'evidence_ledger',
        'proposals',
        'cognition_transitions',
        'identity_state',
      ]) {
        assert.ok(tables.has(table), `${table} 已由 v3 migration 建立`);
      }
    } finally {
      upgraded.close();
    }
  } finally {
    cleanup();
  }
});

test('v3 world 数据经 v4 演化合同 stamp 后逐字保留', () => {
  const { dir, cleanup } = tempDir();
  try {
    const path = join(dir, 'v3.db');
    const seed = openStores(path);
    seed.db.exec(
      "INSERT INTO evidence_ledger(id, content, payload_json) VALUES ('e:kept', 'kept evidence', '{\"id\":\"e:kept\"}')",
    );
    seed.db.exec(
      'INSERT INTO proposals(id, kind, base_revision, result_hash, payload_json, status) ' +
        "VALUES ('review:kept', 'addition', 0, 'sha256:kept', '{\"delta\":{},\"evidence\":[]}', 'reject')",
    );
    const before = seed.db
      .prepare(
        'SELECT id, kind, base_revision, result_hash, payload_json, review_payload_json, status FROM proposals',
      )
      .get();
    seed.db.exec('PRAGMA user_version = 3');
    seed.close();

    const upgraded = openStores(path);
    try {
      assert.equal(uv(upgraded.db), 6);
      const after = upgraded.db
        .prepare(
          'SELECT id, kind, base_revision, result_hash, payload_json, review_payload_json, status FROM proposals',
        )
        .get();
      assert.deepEqual(after, before);
      assert.deepEqual(
        {
          ...(upgraded.db
            .prepare('SELECT id, content, payload_json FROM evidence_ledger')
            .get() as Record<string, unknown>),
        },
        { id: 'e:kept', content: 'kept evidence', payload_json: '{"id":"e:kept"}' },
      );
    } finally {
      upgraded.close();
    }
  } finally {
    cleanup();
  }
});

test('v4 product_bundle 数据经 v5 successor 演化合同 stamp 后逐字保留', () => {
  const { dir, cleanup } = tempDir();
  try {
    const path = join(dir, 'v4-product-bundle.db');
    const seed = openStores(path);
    seed.db.exec(
      "INSERT INTO evidence_ledger(id, content, payload_json) VALUES ('e:v5-kept', 'kept evidence', '{\"id\":\"e:v5-kept\"}')",
    );
    seed.db.exec(
      'INSERT INTO proposals(id, kind, base_revision, result_hash, payload_json, status) ' +
        "VALUES ('review:v5-kept', 'product_bundle', 7, 'sha256:v5-kept', '{\"productBundle\":{\"relationshipEvolutionSteps\":[{\"kind\":\"successor\"}]}}', 'accept')",
    );
    const before = seed.db
      .prepare(
        'SELECT id, kind, base_revision, result_hash, payload_json, review_payload_json, status FROM proposals',
      )
      .get();
    seed.db.exec('PRAGMA user_version = 4');
    seed.close();

    const upgraded = openStores(path);
    try {
      assert.equal(uv(upgraded.db), 6);
      assert.deepEqual(
        upgraded.db
          .prepare(
            'SELECT id, kind, base_revision, result_hash, payload_json, review_payload_json, status FROM proposals',
          )
          .get(),
        before,
      );
      assert.deepEqual(
        {
          ...(upgraded.db
            .prepare('SELECT id, content, payload_json FROM evidence_ledger')
            .get() as Record<string, unknown>),
        },
        { id: 'e:v5-kept', content: 'kept evidence', payload_json: '{"id":"e:v5-kept"}' },
      );
    } finally {
      upgraded.close();
    }
  } finally {
    cleanup();
  }
});

test('v5 product_bundle cognition Evidence update 数据经 v6 合同 stamp 后 rows/bytes 不变', () => {
  const { dir, cleanup } = tempDir();
  try {
    const path = join(dir, 'v5-cognition-evidence-update.db');
    const evidencePayload = '{"id":"e:v6-kept","metadata":{"occurred_at":"2026-08-13T12:00:00Z"}}';
    const proposalPayload =
      '{"cognition_updates":[{"id":"cog:v6-kept","sources":[' +
      '{"evidence_id":"e:v6-kept","relation":"support"}]}],' +
      '"delta":{"source_evidence_ids":["e:v6-kept"]},' +
      '"evolution_steps":[{"kind":"cognition_change","relation":"reaffirms"}]}';
    const reviewPayload = '{"display":"再次确认","confidence_before":600,"confidence_after":640}';
    const seed = openStores(path);
    seed.db
      .prepare('INSERT INTO evidence_ledger(id, content, payload_json) VALUES (?, ?, ?)')
      .run('e:v6-kept', '李华很可靠。', evidencePayload);
    seed.db
      .prepare(
        'INSERT INTO proposals(id, kind, base_revision, result_hash, payload_json, ' +
          "review_payload_json, status) VALUES (?, 'product_bundle', 8, ?, ?, ?, 'accept')",
      )
      .run('review:v6-kept', 'sha256:v6-kept', proposalPayload, reviewPayload);
    const evidenceSql =
      'SELECT id, content, payload_json, typeof(payload_json) AS payload_type, ' +
      'length(CAST(payload_json AS BLOB)) AS payload_bytes, ' +
      "hex(CAST(payload_json AS BLOB)) AS payload_hex FROM evidence_ledger WHERE id = 'e:v6-kept'";
    const proposalSql =
      'SELECT id, kind, base_revision, result_hash, payload_json, review_payload_json, status, ' +
      'typeof(payload_json) AS payload_type, length(CAST(payload_json AS BLOB)) AS payload_bytes, ' +
      'hex(CAST(payload_json AS BLOB)) AS payload_hex, ' +
      'hex(CAST(review_payload_json AS BLOB)) AS review_payload_hex ' +
      "FROM proposals WHERE id = 'review:v6-kept'";
    const before = {
      evidence: { ...(seed.db.prepare(evidenceSql).get() as Record<string, unknown>) },
      proposal: { ...(seed.db.prepare(proposalSql).get() as Record<string, unknown>) },
      schema: schemaSignature(seed.db),
    };
    seed.db.exec('PRAGMA user_version = 5');
    seed.close();

    const db = new DatabaseSync(path);
    try {
      const result = runMigrations(db, { dbPath: path, fresh: false });
      assert.equal(
        MIGRATIONS.find((migration) => migration.version === 6)?.name,
        'product-bundle-cognition-evidence-update-payload-contract',
      );
      assert.deepEqual(result.applied, [6]);
      assert.equal(result.from, 5);
      assert.equal(result.to, 6);
      assert.equal(uv(db), 6);
      assert.deepEqual(
        {
          evidence: { ...(db.prepare(evidenceSql).get() as Record<string, unknown>) },
          proposal: { ...(db.prepare(proposalSql).get() as Record<string, unknown>) },
          schema: schemaSignature(db),
        },
        before,
      );
    } finally {
      db.close();
    }
  } finally {
    cleanup();
  }
});

test('迁移器：自定义下一版迁移会 ALTER + 迁移前备份 + 升版号（不碰生产迁移列表）', () => {
  const { dir, cleanup } = tempDir();
  try {
    const path = join(dir, 'v1.db');
    openStores(path).close();
    const nextVersion = LATEST_SCHEMA_VERSION + 1;
    const fakeNext: Migration = {
      version: nextVersion,
      name: 'test-add-col',
      up: (db) => db.exec('ALTER TABLE cognition ADD COLUMN test_col TEXT'),
    };
    const db = new DatabaseSync(path);
    try {
      const r = runMigrations(db, {
        dbPath: path,
        fresh: false,
        migrations: [...MIGRATIONS, fakeNext],
      });
      assert.equal(r.from, LATEST_SCHEMA_VERSION);
      assert.equal(r.to, nextVersion);
      assert.deepEqual(r.applied, [nextVersion]);
      assert.ok(r.backupPath && existsSync(r.backupPath), '迁移前备份文件在');
      assert.equal(uv(db), nextVersion);
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
    const nextVersion = LATEST_SCHEMA_VERSION + 1;
    const fakeNext: Migration = {
      version: nextVersion,
      name: 'test',
      up: (db) => db.exec('ALTER TABLE cognition ADD COLUMN x TEXT'),
    };
    const db = new DatabaseSync(path);
    try {
      const r = runMigrations(db, {
        dbPath: path,
        fresh: false,
        migrations: [...MIGRATIONS, fakeNext],
        dryRun: true,
      });
      assert.equal(r.dryRun, true);
      assert.deepEqual(r.applied, [nextVersion]);
      assert.equal(uv(db), LATEST_SCHEMA_VERSION, '库版本号没被动');
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
    const nextVersion = LATEST_SCHEMA_VERSION + 1;
    const badNext: Migration = {
      version: nextVersion,
      name: 'test-boom',
      up: (db) => {
        db.exec('ALTER TABLE cognition ADD COLUMN half TEXT');
        throw new Error('boom');
      },
    };
    const db = new DatabaseSync(path);
    try {
      assert.throws(
        () =>
          runMigrations(db, { dbPath: path, fresh: false, migrations: [...MIGRATIONS, badNext] }),
        /boom/,
      );
      assert.equal(uv(db), LATEST_SCHEMA_VERSION, '版本号仍是当前版本（未升）');
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
