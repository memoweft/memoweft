/**
 * 召回底座接口：可替换 seam，便于泛化。
 *
 * 通用化：索引 {id, text} 条目、按 query 找 top-k。读路径用它召回相关【认知】注入回话。
 * 实现可换：NullRetriever（空）/ VectorRetriever（云端嵌入 + JS 余弦）/ 将来 Mem0 等。
 * 配合画像"重算替换"：indexAll 是替换式重建。
 */

export interface RetrievalHit {
  id: string;
  /** 相似度分（实现自定义，越大越相关）。 */
  score: number;
}

export interface Retriever {
  /** 替换式重建索引（清空后重新索引全部条目）。 */
  indexAll(items: Array<{ id: string; text: string }>): Promise<void>;
  /** 找 top-k 最相关，返回带分的 id（按分降序）。 */
  search(query: string, topK: number): Promise<RetrievalHit[]>;
}
