/**
 * 证据软删除（墓碑）行为覆盖（A7）。
 *
 * remove() 从物理删改为软删：打 deleted_at 墓碑、保留原文供审计，但所有读取一律排除墓碑
 * （不再进召回/画像）。purge()/purgeBySubject() 才是真抹除（隐私/出厂用）。
 * 软删时清空 origin_id，使同 originId 的证据可再摄入而不撞幂等唯一约束。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from '../src/store/nodeSqliteDriver.ts';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import type { EvidenceInput } from '../src/evidence/model.ts';

function mk(): { db: DatabaseSync; ev: SqliteEvidenceStore } {
  const db = new DatabaseSync(':memory:');
  return { db, ev: new SqliteEvidenceStore(db) };
}

function put(ev: SqliteEvidenceStore, over: Partial<EvidenceInput> = {}) {
  return ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: 'x', ...over });
}

test('remove 是软删除：打墓碑保留原行，但读取一律排除', () => {
  const { db, ev } = mk();
  try {
    const e = put(ev, { rawContent: '素食者' });
    assert.equal(ev.remove(e.id), true, '首次删返回 true');
    // 读取一律排除
    assert.equal(ev.get(e.id), null, 'get 看不到墓碑');
    assert.equal(ev.all().length, 0, 'all 看不到墓碑');
    // 但物理行仍在、原文保留（可审计路径）
    const row = db
      .prepare('SELECT raw_content, deleted_at FROM evidence WHERE id = ?')
      .get(e.id) as { raw_content: string; deleted_at: string | null } | undefined;
    assert.ok(row, '物理行仍在（墓碑）');
    assert.equal(row.raw_content, '素食者', '原文保留供审计');
    assert.ok(row.deleted_at, 'deleted_at 已打');
    // 重复删返回 false（已是墓碑）
    assert.equal(ev.remove(e.id), false, '重复删返回 false');
  } finally {
    db.close();
  }
});

test('purge 物理删（真抹除，含墓碑）', () => {
  const { db, ev } = mk();
  try {
    const e = put(ev);
    ev.remove(e.id); // 先软删成墓碑
    assert.equal(ev.purge(e.id), true, 'purge 墓碑返回 true');
    const row = db.prepare('SELECT id FROM evidence WHERE id = ?').get(e.id);
    assert.equal(row, undefined, '物理行已抹除');
  } finally {
    db.close();
  }
});

test('软删后同 originId 可再摄入（墓碑清了 origin_id，不撞幂等唯一约束）', () => {
  const { db, ev } = mk();
  try {
    const e1 = put(ev, { originId: 'msg-1' });
    assert.equal(ev.remove(e1.id), true);
    // 同 originId 再摄入：不撞唯一约束，插入新行（而非幂等返回墓碑）
    const e2 = put(ev, { originId: 'msg-1', rawContent: '重新说一次' });
    assert.notEqual(e2.id, e1.id, '是新行，不是幂等返回的墓碑');
    assert.equal(ev.all().length, 1, '只有新行可见');
    assert.equal(ev.findByOrigin('msg-1')?.id, e2.id, 'findByOrigin 命中新行');
    void db;
  } finally {
    db.close();
  }
});

test('purgeBySubject 物理删该 subject 全部证据（含墓碑），不碰其它 subject', () => {
  const { db, ev } = mk();
  try {
    put(ev, { subjectId: 'a', rawContent: 'a1' });
    const softA = put(ev, { subjectId: 'a', rawContent: 'a2' });
    ev.remove(softA.id); // a 的一条成墓碑
    put(ev, { subjectId: 'b', rawContent: 'b1' });
    const n = ev.purgeBySubject('a');
    assert.equal(n, 2, 'a 的两条（含墓碑）都被物理删');
    const rows = db.prepare('SELECT subject_id FROM evidence').all() as Array<{
      subject_id: string;
    }>;
    assert.deepEqual(
      rows.map((r) => r.subject_id),
      ['b'],
      '只剩 b 的证据',
    );
  } finally {
    db.close();
  }
});

test('byTimeRange 也排除墓碑', () => {
  const { db, ev } = mk();
  try {
    const iso = '2026-01-01T00:00:00.000Z';
    const e = put(ev, { occurredAt: iso });
    assert.equal(ev.byTimeRange(iso, iso).length, 1, '删前时间窗能取到');
    ev.remove(e.id);
    assert.equal(ev.byTimeRange(iso, iso).length, 0, '删后时间窗排除墓碑');
    void db;
  } finally {
    db.close();
  }
});
