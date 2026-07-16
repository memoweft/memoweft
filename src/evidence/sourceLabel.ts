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

/** AI 上文注入上限(字符):很长的 AI 那句 → 用户的"是的"指向含糊,截断既省 token 又与"窄范围"纪律一致。 */
const AI_CONTEXT_MAX = 240;

/**
 * 上一轮 AI 那句 → 追加进原话行的【只读上下文】后缀（D-0033 Phase 1b 附和/AI 上下文）。
 * 明确标注「AI 前一句·仅上下文·非用户原话·不可作证据」——让 distill/consolidate 看懂孤儿回应
 * ("AI:你喜欢爬山吧? 用户:是的")指向什么,但绝不把 AI 那句当成用户亲口或可溯源证据。
 * 空/纯空白 → 返回 ''(不注入)。作为原话 text 的后缀拼接,**不铸独立 {id,text} 条目**(否则会进 support 白名单,破 3a)。
 */
export function aiContextSuffix(text: string | null, lang: Lang): string {
  const t = (text ?? '').trim();
  if (!t) return '';
  const clipped = t.length > AI_CONTEXT_MAX ? t.slice(0, AI_CONTEXT_MAX) + '…' : t;
  return lang === 'zh'
    ? `  ⟨AI 前一句(仅上下文,非用户原话、不可作证据):"${clipped}"⟩`
    : `  ⟨preceding AI turn (context only, NOT the user's words, not usable as evidence): "${clipped}"⟩`;
}
