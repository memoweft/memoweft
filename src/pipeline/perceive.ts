/**
 * 感知：把一条原始输入包装成证据入参。
 * ：对话源，默认"用户亲口"（spoken）。窗口感知 / 设备等 再接。
 */
import { config, type MemoWeftConfig } from '../config.ts';
import type { EvidenceInput, SourceKind } from '../evidence/model.ts';

export interface PerceiveOptions {
  subjectId?: string;
  hostId?: string;
  sourceKind?: SourceKind;
  originId?: string | null;
  occurredAt?: string;
}

export function perceive(
  rawContent: string,
  opts: PerceiveOptions = {},
  cfg: MemoWeftConfig = config,
): EvidenceInput {
  return {
    subjectId: opts.subjectId ?? cfg.identity.subjectId,
    hostId: opts.hostId ?? cfg.identity.hostId,
    sourceKind: opts.sourceKind ?? 'spoken',
    originId: opts.originId ?? null,
    occurredAt: opts.occurredAt,
    rawContent,
    // summary 留空：存储层补成 rawContent（v1）； 起 LLM 抽取
  };
}
