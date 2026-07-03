/**
 * 空召回（降级 / 占位）。地图 cell 7。
 * 没配嵌入器时用它：indexAll 不做事，search 返回 []（回话不注入画像，等同阶段 0）。
 */
import type { Retriever, RetrievalHit } from './retriever.ts';

export class NullRetriever implements Retriever {
  async indexAll(_items: Array<{ id: string; text: string }>): Promise<void> {
    /* 不索引 */
  }
  async search(_query: string, _topK: number): Promise<RetrievalHit[]> {
    return [];
  }
}
