/**
 * 感知（地图 cell 4 ①）：把一条原始输入包装成证据入参。
 * 阶段 0：对话源，默认"用户亲口"（spoken）。窗口感知 / 设备等阶段 4 再接。
 */
import { config } from '../config.ts';
import type { EvidenceInput, SourceKind } from '../evidence/model.ts';

export interface PerceiveOptions {
  subjectId?: string;
  hostId?: string;
  sourceKind?: SourceKind;
  originId?: string | null;
  occurredAt?: string;
}

export function perceive(rawContent: string, opts: PerceiveOptions = {}): EvidenceInput {
  return {
    subjectId: opts.subjectId ?? config.identity.subjectId,
    hostId: opts.hostId ?? config.identity.hostId,
    sourceKind: opts.sourceKind ?? 'spoken',
    originId: opts.originId ?? null,
    occurredAt: opts.occurredAt,
    rawContent,
    // summary 留空：存储层补成 rawContent（v1）；阶段 1 起 LLM 抽取
  };
}
