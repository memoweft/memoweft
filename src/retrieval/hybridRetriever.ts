/**
 * 混合召回：RRF 融合多通道。
 * 用 Reciprocal Rank Fusion 把若干个各自实现 Retriever 的通道（向量 + 关键词 + …）的排名表融合成一份。
 *
 * 组合式：new HybridRetriever(channels, opts?)——不 new 底层 store、不认识具体通道类型，
 * 只吃 Retriever 接口。indexAll 扇出到每个通道（各自索引同一批 items）；search 各通道各出一份
 * 按 score 降序的候选表，按 **1-based 排名** 折算 RRF 贡献 `1/(rrfK+rank)`，逐 doc 求和后降序取 topK。
 *
 * RRF 会累加文档在各通道中的排名贡献，因此多个通道共同排在前列的文档可超过仅在单一通道
 * 排名第一的文档；这使词面召回与语义召回能够互补。
 *
 * 确定性：纯融合逻辑，无系统时间、无网络、无随机——rank 只取通道返回的**自身顺序**（不按 score 重排，
 * 各通道已降序），平票按"首次出现顺序"稳定裁决；故同输入恒同输出。
 *
 * ⚠ 本类不导出到 src/index.ts：公共 API 冻结面不动，api:check 保持绿。
 */
import type { Retriever, RetrievalHit } from './retriever.ts';

/** 默认每通道取的候选数：融合前各通道各取前 kCandidate 个。 */
const DEFAULT_K_CANDIDATE = 50;
/** 默认 RRF 平滑常数（经典取 60）：压低头部名次的边际权重，让"多通道共识"胜过"单通道极靠前"。 */
const DEFAULT_RRF_K = 60;

export interface HybridRetrieverOptions {
  /** 每个通道 search 时取的候选数（截断点）。默认 50。 */
  kCandidate?: number;
  /** RRF 平滑常数 k。默认 60。 */
  rrfK?: number;
}

export class HybridRetriever implements Retriever {
  private readonly channels: Retriever[];
  private readonly kCandidate: number;
  private readonly rrfK: number;

  constructor(channels: Retriever[], opts: HybridRetrieverOptions = {}) {
    this.channels = channels;
    this.kCandidate = opts.kCandidate ?? DEFAULT_K_CANDIDATE;
    this.rrfK = opts.rrfK ?? DEFAULT_RRF_K;
  }

  /**
   * 扇出到所有通道各自 indexAll（各通道各自索引同一批 items，替换式语义由各通道自理）。
   * 顺序 await：通道可能共享同一 DB 文件，串行写避免锁竞争（读路径 search 才并行）。
   */
  async indexAll(items: Array<{ id: string; text: string }>): Promise<void> {
    for (const ch of this.channels) {
      await ch.indexAll(items);
    }
  }

  async search(query: string, topK: number): Promise<RetrievalHit[]> {
    // 空 channels → []（无通道可融合）。
    if (this.channels.length === 0) return [];

    // 各通道并行召回其 kCandidate 候选。Promise.all 保序：perChannel[i] 恒对应 channels[i]，
    // 与通道解析先后无关 → 融合结果确定。
    const perChannel = await Promise.all(
      this.channels.map((ch) => ch.search(query, this.kCandidate)),
    );

    // RRF 累加：doc 在某通道的 1-based 排名 rank（= 返回数组下标 + 1，用通道自身顺序，不按 score 重排）
    // → 该通道贡献 1/(rrfK + rank)；doc 未出现在某通道则该通道不贡献。逐 doc 求各通道贡献之和。
    // order 记"首次出现顺序"，供平票时稳定裁决。
    const acc = new Map<string, { score: number; order: number }>();
    let order = 0;
    for (const hits of perChannel) {
      for (let i = 0; i < hits.length; i++) {
        const id = hits[i]!.id;
        const contribution = 1 / (this.rrfK + (i + 1));
        const cur = acc.get(id);
        if (cur === undefined) {
          acc.set(id, { score: contribution, order: order++ });
        } else {
          cur.score += contribution;
        }
      }
    }

    // 所有通道都空 → acc 空 → []。按 RRF 分降序；平票按首次出现顺序升序 → 确定。
    const fused = [...acc.entries()].map(([id, v]) => ({ id, score: v.score, order: v.order }));
    fused.sort((a, b) => b.score - a.score || a.order - b.order);
    return fused.slice(0, topK).map(({ id, score }) => ({ id, score }));
  }

  /** 关闭每个实现了 close 的通道（Retriever 接口未声明 close，故按结构探测后调用）。 */
  close(): void {
    for (const ch of this.channels) {
      const c = ch as Retriever & { close?: () => void };
      if (typeof c.close === 'function') c.close();
    }
  }
}
