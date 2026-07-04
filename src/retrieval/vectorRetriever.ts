/**
 * 向量召回（地图 cell 7/11：抄 Graphiti 语义召回思想，自研最小版）。
 * SQLite 存向量 + JS 余弦相似度——单人几千条够用，**零依赖**（不上 sqlite-vec 原生扩展）。
 *
 * indexAll 对外仍是"替换式重建"语义（调用后索引 = 传入集合），但内部改为**增量**实现：
 * 每条文本记 sha256 hash，与库中已有 (id, hash) 做 diff——只对"新增 + 内容变更"的条目
 * 调 embedder.embed（一次批量），"库里有但这次没传"的条目 DELETE。
 * 于是嵌入调用量从 O(N) 降到 O(Δ)，而对外行为（含 indexAll([]) 清空全表）不变。
 *
 * search 嵌入 query 后算余弦取 top-k。
 */
import { createHash } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { BUSY_TIMEOUT_MS } from '../store/busyTimeout.ts';
import type { Retriever, RetrievalHit } from './retriever.ts';
import type { Embedder } from './embedder.ts';

const SCHEMA = `CREATE TABLE IF NOT EXISTS vectors (id TEXT PRIMARY KEY, hash TEXT NOT NULL, vec TEXT NOT NULL);`;

/** 文本内容指纹：sha256（node:crypto 内置，零依赖）。判断"同 id 内容是否变了"。 */
function contentHash(text: string): string {
  return createHash('sha256').update(text).digest('hex');
}

function cosine(a: number[], b: number[]): number {
  let dot = 0;
  let na = 0;
  let nb = 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const x = a[i] ?? 0;
    const y = b[i] ?? 0;
    dot += x * y;
    na += x * x;
    nb += y * y;
  }
  if (na === 0 || nb === 0) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

export class VectorRetriever implements Retriever {
  private readonly db: DatabaseSync;
  private readonly embedder: Embedder;

  constructor(dbPath: string, embedder: Embedder) {
    this.db = new DatabaseSync(dbPath);
    // 并发保底：向量表缺省与主库同一个文件、又独开这第二条连接，多进程下写路径要靠它等锁而非裸抛。
    this.db.exec(`PRAGMA busy_timeout = ${BUSY_TIMEOUT_MS}`);
    this.migrateIfNeeded();
    this.db.exec(SCHEMA);
    this.embedder = embedder;
  }

  /**
   * 旧 schema（无 hash 列）→ 直接 DROP 重建。
   * 理由：向量索引是**可重建资产**，宁可推倒重建也不带病迁移——
   * 旧行没有 hash 没法参与 diff，硬补一列只会留下假指纹；
   * 删掉后下次 indexAll 会自然全量回填，代价只是一轮嵌入。
   */
  private migrateIfNeeded(): void {
    const cols = this.db.prepare('PRAGMA table_info(vectors)').all() as unknown as Array<{
      name: string;
    }>;
    if (cols.length > 0 && !cols.some((c) => c.name === 'hash')) {
      this.db.exec('DROP TABLE vectors');
    }
  }

  async indexAll(items: Array<{ id: string; text: string }>): Promise<void> {
    // 边界：空集合 = 清空全表（替换式语义，与旧实现一致）。
    if (items.length === 0) {
      this.db.exec('DELETE FROM vectors');
      return;
    }

    // 读出库中现有 (id, hash)，与传入集合做 diff。
    const existing = new Map<string, string>();
    const rows = this.db.prepare('SELECT id, hash FROM vectors').all() as unknown as Array<{
      id: string;
      hash: string;
    }>;
    for (const r of rows) existing.set(r.id, r.hash);

    // 分三集：新增（库无此 id）/ 变更（id 同 hash 异）→ 都要重新嵌入；删除（库有但 items 无）。
    const hashed = items.map((it) => ({ ...it, hash: contentHash(it.text) }));
    const toEmbed = hashed.filter((it) => existing.get(it.id) !== it.hash);
    const keepIds = new Set(hashed.map((it) => it.id));
    const toDelete = [...existing.keys()].filter((id) => !keepIds.has(id));

    // 只对 新增+变更 调一次批量嵌入；全无变化则完全不打嵌入接口。
    if (toEmbed.length > 0) {
      const vecs = await this.embedder.embed(toEmbed.map((i) => i.text));
      const upsert = this.db.prepare(
        'INSERT INTO vectors (id, hash, vec) VALUES (?, ?, ?) ' +
          'ON CONFLICT(id) DO UPDATE SET hash = excluded.hash, vec = excluded.vec',
      );
      toEmbed.forEach((it, i) => upsert.run(it.id, it.hash, JSON.stringify(vecs[i] ?? [])));
    }

    if (toDelete.length > 0) {
      const del = this.db.prepare('DELETE FROM vectors WHERE id = ?');
      for (const id of toDelete) del.run(id);
    }
  }

  async search(query: string, topK: number): Promise<RetrievalHit[]> {
    const rows = this.db.prepare('SELECT id, vec FROM vectors').all() as unknown as Array<{
      id: string;
      vec: string;
    }>;
    if (rows.length === 0) return [];
    const qv = (await this.embedder.embed([query]))[0] ?? [];
    const scored = rows.map((r) => ({ id: r.id, score: cosine(qv, JSON.parse(r.vec) as number[]) }));
    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, topK);
  }

  close(): void {
    this.db.close();
  }
}
