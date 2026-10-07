/** Typed observed revisions. Wire fields intentionally match DSH RPC v2. */
import { createHash } from 'node:crypto';
import type { StoreBundle } from '../store/openStores.ts';
import type { Clock } from '../clock.ts';
import { systemClock } from '../clock.ts';
import type { ModelTier } from '../llm/client.ts';

export interface ObservedPermissionsV1 {
  allow_local_read: boolean;
  allow_cloud_read: boolean;
  allow_inference: boolean;
}
export interface ObservedEvidenceV1 {
  source_key: string;
  version: string;
  content: string;
  occurred_at: string;
  valid_at: string;
  valid_until?: string | null;
  permissions: ObservedPermissionsV1;
}
export interface ObservedPermissionInput {
  source_key: string;
  permission_version: string;
  permissions: ObservedPermissionsV1;
}
export interface ObservedRetractionInput {
  source_key: string;
  withdrawn_through: string;
}
export interface ObservedReceiptV1 {
  schema_version: number;
  subject_id: string;
  source_hash: string;
  source_kind: 'observed';
  evidence_id: string | null;
  result_state: 'applied' | 'no_change';
  world_revision: number;
  before_revision: number;
  after_revision: number;
  model_call_count: number;
  storage_cleanup?: { state: string; detail_code: string };
}
export interface ObservedAPI {
  upsert(evidence: ObservedEvidenceV1): Promise<ObservedReceiptV1>;
  updatePermissions(input: ObservedPermissionInput): Promise<ObservedReceiptV1>;
  retract(input: ObservedRetractionInput): Promise<ObservedReceiptV1>;
}

const hash = (value: string) => createHash('sha256').update(value).digest('hex');
const fail = (code: string): never => {
  throw Object.assign(new Error(code), { code });
};
function keys(
  value: unknown,
  allowed: string[],
  required = allowed,
): asserts value is Record<string, unknown> {
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    Object.keys(value).some((key) => !allowed.includes(key)) ||
    required.some((key) => !Object.hasOwn(value, key))
  ) {
    fail('invalid_observed_parameter');
  }
}
function instant(value: unknown): string {
  if (
    typeof value !== 'string' ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(value) ||
    !Number.isFinite(Date.parse(value))
  )
    return fail('invalid_observed_time');
  const normalized = new Date(value).toISOString();
  if (normalized.slice(0, 19) !== value.slice(0, 19)) return fail('invalid_observed_time');
  return normalized;
}
function permissions(value: unknown): ObservedPermissionsV1 {
  const fields = ['allow_local_read', 'allow_cloud_read', 'allow_inference'];
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    Object.keys(value).sort().join(',') !== fields.sort().join(',') ||
    Object.values(value).some((item) => typeof item !== 'boolean')
  )
    return fail('invalid_observed_permissions');
  return value as unknown as ObservedPermissionsV1;
}

// Same content-free source metadata as Python schema v21. TS stores retain v6 ownership.
const SCHEMA = `CREATE TABLE IF NOT EXISTS observed_source (
  subject_id TEXT NOT NULL, host_id TEXT NOT NULL, source_hash TEXT NOT NULL,
  evidence_id TEXT, version TEXT NOT NULL, payload_hash TEXT NOT NULL,
  permission_version TEXT NOT NULL, withdrawn_through TEXT,
  valid_at TEXT NOT NULL, valid_until TEXT,
  PRIMARY KEY (subject_id, host_id, source_hash)
); CREATE TABLE IF NOT EXISTS observed_index_cleanup (id TEXT PRIMARY KEY);`;

export class ObservedEvidenceService {
  private readonly stores: StoreBundle;
  private readonly subject: string;
  private readonly host: string;
  private readonly clock: Clock;
  constructor(stores: StoreBundle, subject: string, host: string, clock: Clock = systemClock) {
    this.stores = stores;
    this.subject = subject;
    this.host = host;
    this.clock = clock;
    stores.db.exec(SCHEMA);
  }

  execute(
    operation: 'upsert_observed' | 'update_observed_permissions' | 'retract_observed',
    params: Record<string, unknown>,
  ): ObservedReceiptV1 {
    let key: unknown,
      version: string,
      content: string | undefined,
      occurredAt = '',
      validAt = '';
    let validUntil: string | null = null,
      payloadHash = '',
      perms: ObservedPermissionsV1 | undefined;
    if (operation === 'upsert_observed') {
      keys(params, ['evidence']);
      const value = params.evidence;
      const required = [
        'source_key',
        'version',
        'content',
        'occurred_at',
        'valid_at',
        'permissions',
      ];
      keys(value, [...required, 'valid_until'], required);
      key = value.source_key;
      version = instant(value.version);
      if (typeof value.content !== 'string' || Buffer.byteLength(value.content) > 65536)
        return fail('invalid_observed_content');
      content = value.content;
      occurredAt = instant(value.occurred_at);
      validAt = instant(value.valid_at);
      validUntil = value.valid_until == null ? null : instant(value.valid_until);
      if (validUntil !== null && validUntil <= validAt) return fail('invalid_observed_time_range');
      perms = permissions(value.permissions);
      payloadHash = hash(JSON.stringify([content, occurredAt, validAt, validUntil]));
    } else if (operation === 'update_observed_permissions') {
      keys(params, ['source_key', 'permission_version', 'permissions']);
      key = params.source_key;
      version = instant(params.permission_version);
      perms = permissions(params.permissions);
    } else {
      keys(params, ['source_key', 'withdrawn_through']);
      key = params.source_key;
      version = instant(params.withdrawn_through);
    }
    if (
      typeof key !== 'string' ||
      !key.trim() ||
      key !== key.trim() ||
      Array.from(key).length > 512
    )
      return fail('invalid_observed_source_key');
    const identity = hash(JSON.stringify([this.subject, this.host, key]));
    const db = this.stores.db;
    db.exec('PRAGMA secure_delete=ON');
    db.exec('BEGIN IMMEDIATE');
    let receipt: ObservedReceiptV1;
    try {
      const before = Number(
        db.prepare('SELECT revision FROM memory_state WHERE singleton=1').get()?.revision ?? 0,
      );
      const row = db
        .prepare('SELECT * FROM observed_source WHERE subject_id=? AND host_id=? AND source_hash=?')
        .get(this.subject, this.host, identity);
      let evidenceId = row?.evidence_id == null ? null : String(row.evidence_id),
        changed = false;
      if (operation === 'upsert_observed') {
        if (row) {
          if (row.withdrawn_through != null && version <= String(row.withdrawn_through))
            return fail('observed_source_withdrawn');
          if (version < String(row.version)) return fail('stale_observed_version');
          if (version === row.version && payloadHash !== row.payload_hash)
            return fail('observed_version_conflict');
          if (version < String(row.permission_version) && evidenceId) {
            const old = this.stores.evidenceStore.get(evidenceId);
            if (!old) return fail('observed_source_withdrawn');
            perms = {
              allow_local_read: old.allowLocalRead,
              allow_cloud_read: old.allowCloudRead,
              allow_inference: old.allowInference,
            };
          }
          if (evidenceId && payloadHash !== row.payload_hash) {
            this.remove(evidenceId);
            evidenceId = null;
          }
        }
        if (!evidenceId) {
          evidenceId = 'observed:' + hash(`${identity}:${version}:${payloadHash}`);
          if (db.prepare('SELECT 1 FROM evidence WHERE id=?').get(evidenceId))
            return fail('observed_source_withdrawn');
          const now = this.clock().toISOString();
          this.stores.evidenceStore.insert({
            id: evidenceId,
            subjectId: this.subject,
            sourceKind: 'observed',
            hostId: this.host,
            originId: evidenceId,
            occurredAt,
            recordedAt: now,
            rawContent: content!,
            summary: content!,
            allowLocalRead: perms!.allow_local_read,
            allowCloudRead: perms!.allow_cloud_read,
            allowInference: perms!.allow_inference,
            correctsEvidenceId: null,
          });
          if (content!.trim())
            this.stores.cognitionStore.insert(
              {
                id: 'state:' + evidenceId,
                subjectId: this.subject,
                content: content!,
                contentType: 'state',
                formedBy: 'observed',
                confidence: 500,
                credStatus: 'limited',
                scope: null,
                validAt,
                invalidAt: null,
                askedAt: null,
                archivedAt: null,
                mutedAt: null,
                createdAt: now,
                updatedAt: now,
              },
              [{ evidenceId, relation: 'support' }],
            );
          changed = true;
        }
        if (!row)
          db.prepare('INSERT INTO observed_source VALUES (?,?,?,?,?,?,?,NULL,?,?)').run(
            this.subject,
            this.host,
            identity,
            evidenceId,
            version,
            payloadHash,
            version,
            validAt,
            validUntil,
          );
        else {
          changed ||= version !== row.version;
          db.prepare(
            'UPDATE observed_source SET evidence_id=?,version=?,payload_hash=?,valid_at=?,valid_until=? WHERE subject_id=? AND host_id=? AND source_hash=?',
          ).run(
            evidenceId,
            version,
            payloadHash,
            validAt,
            validUntil,
            this.subject,
            this.host,
            identity,
          );
        }
        if (!row || version >= String(row.permission_version))
          changed = this.setPermissions(identity, evidenceId, version, perms!) || changed;
      } else if (operation === 'update_observed_permissions') {
        if (!evidenceId) return fail('observed_source_not_found');
        if (version < String(row!.permission_version)) return fail('stale_observed_permissions');
        changed = this.setPermissions(identity, evidenceId, version, perms!);
      } else {
        if (!row) {
          db.prepare("INSERT INTO observed_source VALUES (?,?,?,NULL,?,'',?,?,?,NULL)").run(
            this.subject,
            this.host,
            identity,
            version,
            version,
            version,
            version,
          );
          changed = true;
        } else {
          if (version < String(row.version)) return fail('stale_observed_withdrawal');
          if (evidenceId) {
            this.remove(evidenceId);
            changed = true;
          }
          changed ||= row.withdrawn_through == null || version > String(row.withdrawn_through);
          db.prepare(
            "UPDATE observed_source SET evidence_id=NULL,payload_hash='',withdrawn_through=? WHERE subject_id=? AND host_id=? AND source_hash=?",
          ).run(version, this.subject, this.host, identity);
        }
      }
      const after = changed ? before + 1 : before;
      if (changed) {
        // Rebuild the snapshot so deleted text cannot persist in memory_state.
        const snapshot = JSON.stringify({
          schema_version: 5,
          revision: after,
          cognitions: this.stores.cognitionStore
            .all()
            .map((c) => ({ id: c.id, content: c.content })),
        });
        db.prepare(
          'INSERT INTO memory_state VALUES (1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,snapshot_json=excluded.snapshot_json,snapshot_hash=excluded.snapshot_hash',
        ).run(after, snapshot, hash(snapshot));
      }
      db.exec('COMMIT');
      receipt = {
        schema_version: 1,
        subject_id: this.subject,
        source_hash: identity,
        source_kind: 'observed',
        evidence_id: operation === 'retract_observed' ? null : evidenceId,
        result_state: changed ? 'applied' : 'no_change',
        world_revision: after,
        before_revision: before,
        after_revision: after,
        model_call_count: 0,
      };
    } catch (cause) {
      db.exec('ROLLBACK');
      throw cause;
    }
    {
      let state = 'complete',
        detail_code = 'current_storage_committed';
      try {
        if (db.prepare('PRAGMA journal_mode').get()?.journal_mode === 'wal') {
          const checkpoint = db.prepare('PRAGMA wal_checkpoint(TRUNCATE)').get();
          if (Number(checkpoint?.busy ?? 1) !== 0) {
            state = 'pending';
            detail_code = 'wal_reader_busy';
          }
        }
      } catch {
        state = 'pending';
        detail_code = 'checkpoint_status_unconfirmed';
      }
      receipt.storage_cleanup = { state, detail_code };
    }
    return receipt;
  }

  private setPermissions(
    identity: string,
    evidenceId: string,
    version: string,
    perms: ObservedPermissionsV1,
  ): boolean {
    const db = this.stores.db;
    const current = this.stores.evidenceStore.get(evidenceId);
    if (!current) return fail('observed_source_withdrawn');
    const prior = db
      .prepare(
        'SELECT permission_version FROM observed_source WHERE source_hash=? AND subject_id=? AND host_id=?',
      )
      .get(identity, this.subject, this.host);
    const changed =
      current.allowLocalRead !== perms.allow_local_read ||
      current.allowCloudRead !== perms.allow_cloud_read ||
      current.allowInference !== perms.allow_inference;
    if (
      version === prior?.permission_version &&
      ((!current.allowLocalRead && perms.allow_local_read) ||
        (!current.allowCloudRead && perms.allow_cloud_read) ||
        (!current.allowInference && perms.allow_inference))
    )
      return fail('observed_permission_conflict');
    db.prepare(
      'UPDATE evidence SET allow_local_read=?,allow_cloud_read=?,allow_inference=? WHERE id=?',
    ).run(
      Number(perms.allow_local_read),
      Number(perms.allow_cloud_read),
      Number(perms.allow_inference),
      evidenceId,
    );
    db.prepare(
      'UPDATE observed_source SET permission_version=? WHERE source_hash=? AND subject_id=? AND host_id=?',
    ).run(version, identity, this.subject, this.host);
    return changed || prior?.permission_version !== version;
  }

  private remove(evidenceId: string): void {
    const db = this.stores.db;
    const affected = new Set<string>([evidenceId]);
    const rows = db
      .prepare('SELECT cognition_id FROM cognition_evidence WHERE evidence_id=?')
      .all(evidenceId);
    for (const row of rows) {
      const id = String(row.cognition_id);
      affected.add(id);
      db.prepare('INSERT OR IGNORE INTO observed_index_cleanup VALUES (?)').run(id);
      this.stores.cognitionStore.remove(id);
      for (const [table, column] of [
        ['cognition_fts', 'cognition_id'],
        ['kw_meta', 'id'],
        ['vectors', 'id'],
      ]) {
        if (db.prepare('SELECT 1 FROM sqlite_master WHERE name=?').get(table!))
          db.prepare(`DELETE FROM ${table} WHERE ${column}=?`).run(id);
      }
      db.prepare(
        'DELETE FROM cognition_transitions WHERE prior_cognition_id=? OR replacement_cognition_id=?',
      ).run(id, id);
    }
    for (const row of db
      .prepare('SELECT event_id FROM event_evidence WHERE evidence_id=?')
      .all(evidenceId)) {
      affected.add(String(row.event_id));
      this.stores.eventStore.remove(String(row.event_id));
    }
    db.prepare('DELETE FROM semantic_resolution WHERE evidence_id=?').run(evidenceId);
    db.prepare('DELETE FROM evidence_retraction WHERE evidence_id=?').run(evidenceId);
    for (const row of db.prepare('SELECT id,content,payload_json FROM evidence_ledger').all()) {
      if (
        [...affected].some(
          (id) => String(row.content).includes(id) || String(row.payload_json).includes(id),
        )
      )
        db.prepare('DELETE FROM evidence_ledger WHERE id=?').run(String(row.id));
    }
    for (const row of db
      .prepare('SELECT id,payload_json,review_payload_json FROM proposals')
      .all()) {
      if (
        [...affected].some(
          (id) =>
            String(row.payload_json).includes(id) || String(row.review_payload_json).includes(id),
        )
      ) {
        db.prepare('DELETE FROM proposals WHERE id=?').run(String(row.id));
      }
    }
    const contexts = db
      .prepare('SELECT id,context_json FROM interaction_context WHERE subject_id=?')
      .all(this.subject);
    const tainted = new Set(affected);
    const mentions = (value: unknown): boolean =>
      typeof value === 'string'
        ? tainted.has(value)
        : Array.isArray(value)
          ? value.some(mentions)
          : value !== null && typeof value === 'object'
            ? Object.values(value).some(mentions)
            : false;
    for (;;) {
      const oldSize = tainted.size;
      for (const row of contexts) {
        if (mentions(JSON.parse(String(row.context_json)))) tainted.add(String(row.id));
      }
      if (tainted.size === oldSize) break;
    }
    for (const row of contexts) {
      const turns = JSON.parse(String(row.context_json)) as Array<Record<string, unknown>>;
      const clean = turns.filter(
        (turn) => !(turn.role === 'assistant' && mentions(turn.model_context_dependencies)),
      );
      if (clean.length !== turns.length) {
        const json = JSON.stringify(clean);
        db.prepare('UPDATE interaction_context SET context_json=?,context_hash=? WHERE id=?').run(
          json,
          hash(json),
          String(row.id),
        );
      }
    }
    for (const id of affected) db.prepare('DELETE FROM management_log WHERE target_id=?').run(id);
    db.prepare('DELETE FROM identity_state WHERE world_id=?').run(this.subject);
    db.prepare(
      "UPDATE evidence SET raw_content='',summary='',preceding_ai_context=NULL,origin_id=NULL,allow_local_read=0,allow_cloud_read=0,allow_inference=0,deleted_at=COALESCE(deleted_at,?) WHERE id=?",
    ).run(this.clock().toISOString(), evidenceId);
  }

  /** Exact observed facts use local matching, never an embedding/model route. */
  candidates(query: string, tier: ModelTier): Array<{ id: string; score: number }> {
    return this.stores.cognitionStore
      .all(this.subject)
      .filter(
        (c) =>
          c.id.startsWith('state:observed:') &&
          c.content.toLowerCase().includes(query.toLowerCase()),
      )
      .map((c) => ({ id: c.id, score: 1 }))
      .filter((c) => this.readable(c.id, tier));
  }

  readable(cognitionId: string, tier: ModelTier): boolean {
    const links = this.stores.cognitionStore.sourcesOf(cognitionId);
    const now = this.clock().toISOString();
    return links.every((link) => {
      const e = this.stores.evidenceStore.get(link.evidenceId);
      if (
        !e ||
        e.subjectId !== this.subject ||
        !(tier === 'local' ? e.allowLocalRead : e.allowCloudRead)
      )
        return false;
      const time = this.stores.db
        .prepare('SELECT valid_at,valid_until FROM observed_source WHERE evidence_id=?')
        .get(e.id);
      return (
        !time ||
        (String(time.valid_at) <= now &&
          (time.valid_until == null || String(time.valid_until) > now))
      );
    });
  }
}
