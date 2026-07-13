/**
 * 共享召回（架构归位·批次2）：Conversation 与 core.recall 共用的同一段召回语义。
 *
 * 从 pipeline/conversation.ts 的召回段原样抽出（retriever.search → 相似度门控 → 取认知 →
 * invalid 跳过 → subjectId 硬过滤 → 衰减门控），门槛顺序与判断条件一字不改——语义零变化。
 * 唯一新增：同时跳过 archived_at 非空的认知（归档＝invalid 同款待遇，批次2 受控管理引入）。
 *
 * 错误不在此吞：Conversation 保留自己的 try/catch（召回失败不挡回话）；
 * core.recall 则如实抛给调用方（调用方是主动要召回结果的，失败要能感知）。
 */
import { config, type MemoWeftConfig } from '../config.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { ContentType } from '../cognition/model.ts';
import type { Retriever } from './retriever.ts';
import { effectiveConfidence } from '../background/decay.ts';

export interface RecallDeps {
  retriever: Retriever;
  cognitionStore: CognitionStore;
}

/** 召回到、且通过全部门控的一条认知（含相似度分）。 */
export interface RecalledCognitionItem {
  /** 认知 id（供管理页 / 透视反查；注入回话只用后三个字段）。 */
  id: string;
  content: string;
  /** 有效置信（衰减后，非库中原值）。 */
  confidence: number;
  credStatus: string;
  score: number;
  /** 认知类型（D-0022：暴露给宿主 + 供 core.recall 的 contentTypes 过滤）。 */
  contentType: ContentType;
}

/**
 * 按 query 召回 subjectId 的相关认知，走满全部既有门控。
 * @param cfg 可注入配置（缺省=全局单例）；@param now 衰减计算的"现在"（测试可注入求确定性）。
 */
export async function recallCognitions(
  query: string,
  subjectId: string,
  deps: RecallDeps,
  cfg: MemoWeftConfig = config,
  now: Date = new Date(),
): Promise<RecalledCognitionItem[]> {
  const out: RecalledCognitionItem[] = [];
  const hits = await deps.retriever.search(query, cfg.retrieval.topK);
  for (const h of hits) {
    // 相似度门控：这一轮问题跟这条认知不够像 → 别硬塞（防 top-k 召回不相关认知）。
    // 默认阈值 0 = 不筛（行为同旧）；调成非零后，低于阈值的召回直接跳过。
    if (h.score < cfg.retrieval.minSimilarity) continue;
    const c = deps.cognitionStore.get(h.id);
    if (!c || c.invalidAt) continue; // 失效的不注入（即便索引还没重建，也别把过期/被纠正的塞回话）
    if (c.archivedAt) continue; // 归档的不注入（invalid 同款待遇：数据还在、召回不出，批次2 唯一新增门控）
    if (c.subjectId !== subjectId) continue; // 越界召回硬过滤（多 subject 隐私止血）：索引可能混入其他 subject 的条目，不是本人的认知绝不注入。契约见地图「召回边界」。
    // 衰减门控（cell 8 规则 8）：把握度用【有效置信】，淡了的情绪/过气的假设直接不注入。
    const eff = effectiveConfidence(c, now, cfg);
    if (eff < cfg.retrieval.minEffectiveConfidence) continue;
    out.push({ id: c.id, content: c.content, confidence: eff, credStatus: c.credStatus, score: h.score, contentType: c.contentType });
  }
  return out;
}
