/**
 * 证据来源标注。
 *
 * 把 evidence.sourceKind 映射成给固化 LLM（distill / consolidate）的来源前缀,让它据此正确定 formedBy——
 * observed（行为观察）/ tool（工具返回）**不是用户原话**,不该被固化成 `formed_by: stated`（用户亲口,底分最高）。
 * 避免 distill/consolidate 只读取 rawContent 时，把观察或工具结果误标为用户陈述。
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
 * 将上一轮 AI 回复作为【只读上下文】后缀追加到当前原话，帮助解析简短附和。
 * 明确标注「AI 前一句·仅上下文·非用户原话·不可作证据」——让 distill/consolidate 看懂孤儿回应
 * ("AI:你喜欢爬山吧? 用户:是的")指向什么,但绝不把 AI 那句当成用户亲口或可溯源证据。
 * 空/纯空白 → 返回 ''(不注入)。作为原话 text 的后缀拼接，**不铸独立 {id,text} 条目**，避免助手输出进入 support 白名单。
 */
export function aiContextSuffix(text: string | null, lang: Lang): string {
  const t = (text ?? '').trim();
  if (!t) return '';
  const clipped = t.length > AI_CONTEXT_MAX ? t.slice(0, AI_CONTEXT_MAX) + '…' : t;
  return lang === 'zh'
    ? `  ⟨AI 前一句(仅上下文,非用户原话、不可作证据):"${clipped}"⟩`
    : `  ⟨preceding AI turn (context only, NOT the user's words, not usable as evidence): "${clipped}"⟩`;
}
