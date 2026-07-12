/**
 * 来源标注（D-0018 来源感知固化）。
 *
 * 把 evidence.sourceKind 映射成给固化 LLM（distill / consolidate）的来源前缀,让它据此正确定 formedBy——
 * observed（行为观察）/ tool（工具返回）**不是用户原话**,不该被固化成 `formed_by: stated`（用户亲口,底分最高）。
 * 修正的是「distill/consolidate 只喂 rawContent、把一切框成用户亲口」这个既有偏差(伤 fact/guess + 来源强度)。
 *
 * 内部工具,**不从 index.ts 导出**（不动公共 API 快照）。
 */
import type { SourceKind } from './model.ts';
import type { Lang } from '../config.ts';

const LABELS: Record<SourceKind, { zh: string; en: string }> = {
  spoken: { zh: '用户说', en: 'user said' },
  observed: { zh: '行为观察', en: 'observed behavior' },
  tool: { zh: '工具返回', en: 'tool result' },
  inferred: { zh: 'AI 推测', en: 'AI inference' },
};

/** 来源前缀,如 `[行为观察] ` / `[observed behavior] `;未知来源退回 spoken 标签(保守)。 */
export function sourceLabel(sourceKind: SourceKind, lang: Lang): string {
  const l = LABELS[sourceKind] ?? LABELS.spoken;
  return `[${l[lang]}] `;
}
