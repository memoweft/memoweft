/**
 * 受治理提示词的聚合注册表（§15.3 提示词集中版本化 / DECISIONS D-0009）。
 *
 * 把 8 条受治理提示词（原先散落在各模块的 export 常量）收敛到一处：
 *   - PROMPT_REGISTRY：全部 8 条，按 id 字母序排列（便于 diff 时肉眼比对哈希快照）。
 *   - promptVersions()：id → version 映射，供 bench 评测器把「本轮用了哪版提示词」记进报告元数据。
 *
 * 哈希闸门：tests/prompts/registry.test.ts + tests/prompts/prompt-hashes.snapshot。
 * 改任一条提示词内容必须 bump 其 version，否则快照会立刻变红（§15.3 / D-0009）。
 * 生成/更新快照：`npm run prompts:update`（scripts/prompt-hashes.mjs）。
 */
import type { VersionedPrompt } from './types.ts';
import { ATTRIBUTE_PROMPT } from '../attribution/prompts.ts';
import { CONSOLIDATE_PROMPT } from '../consolidation/prompts.ts';
import { DISTILL_PROMPT } from '../distillation/prompts.ts';
import { JSON_REPAIR_NUDGE_PROMPT } from '../llm/prompts.ts';
import { PROPOSE_ASK_PROMPT, REVISIT_CONFLICTS_PROMPT } from '../asking/prompts.ts';
import { REPLY_PROMPT } from '../pipeline/prompts.ts';
import { TRENDS_PROMPT } from '../background/prompts.ts';

/** 全部受治理提示词，按 id 字母序（与哈希快照的行序一致）。 */
export const PROMPT_REGISTRY: readonly VersionedPrompt[] = [
  ATTRIBUTE_PROMPT,
  CONSOLIDATE_PROMPT,
  DISTILL_PROMPT,
  JSON_REPAIR_NUDGE_PROMPT,
  PROPOSE_ASK_PROMPT,
  REPLY_PROMPT,
  REVISIT_CONFLICTS_PROMPT,
  TRENDS_PROMPT,
];

/** id → version 映射（供 bench 评测器记进报告元数据：本轮用了哪版提示词）。 */
export function promptVersions(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of PROMPT_REGISTRY) out[p.id] = p.version;
  return out;
}
