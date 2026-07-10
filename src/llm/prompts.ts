/**
 * JSON_REPAIR_NUDGE_PROMPT —— JSON 解析失败后重试的纠偏提示词（重试时以 role:'user' 追加，非 system）
 * （parseJsonObjectWithRepair · §15.3 集中版本化）。
 *
 * 首次输出非合法 JSON 对象时，追加这条「只输出一个 JSON 对象」的纠偏提示再重试一次。
 *
 * 版本变更日志：
 *   - v1：基线。
 *
 * 改动纪律（§15.3 / D-0009）：改内容必须 bump version、重跑 bench/eval-consolidation.mjs 全量、
 *   commit 正文附前后分数对比。否则 tests/prompts/registry.test.ts 的哈希快照会变红。
 */
import type { VersionedPrompt } from '../prompts/types.ts';

export const JSON_REPAIR_NUDGE_PROMPT: VersionedPrompt = {
  id: 'jsonRepairNudge',
  version: 'v1',
  text: {
    zh: '你上一条回复不是合法的 JSON 对象。请【只】输出一个 JSON 对象，不要任何解释、不要 Markdown 代码块围栏。',
    en: 'Your previous reply was not a valid JSON object. Output [only] a single JSON object, with no explanation and no Markdown code fences.',
  },
};
