/**
 * 向量召回（地图 cell 7/11：抄 Graphiti 语义召回思想，自研最小版）。
 * SQLite 存向量 + JS 余弦相似度——单人几千条够用，**零依赖**（不上 sqlite-vec 原生扩展）。
 *
 * indexAll 替换式重建（配合画像重算替换）；search 嵌入 query 后算余弦取 top-k。
 */
import { DatabaseSync } from 'node:sqlite';
import type { Retriever, RetrievalHit } from './retriever.ts';
import type { Embedder } from './embedder.ts';

const SCHEMA = `CREATE TABLE IF NOT EXISTS vectors (id TEXT PRIMARY KEY, vec TEXT NOT NULL);`;

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
    this.db.exec(SCHEMA);
    this.embedder = embedder;
  }

  async indexAll(items: Array<{ id: string; text: string }>): Promise<void> {
    this.db.exec('DELETE FROM vectors');
    if (items.length === 0) return;
    const vecs = await this.embedder.embed(items.map((i) => i.text));
    const stmt = this.db.prepare('INSERT INTO vectors (id, vec) VALUES (?, ?)');
    items.forEach((it, i) => stmt.run(it.id, JSON.stringify(vecs[i] ?? [])));
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
