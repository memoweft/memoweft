/**
 * 受治理提示词的公共形状（§15.3 提示词集中版本化 / DECISIONS D-0009）。
 *
 * 8 条受治理提示词从各模块散落的 export 常量收敛到 src/prompts/ 下：每条实现 VersionedPrompt，
 * 由 src/prompts/registry.ts 聚合。tests/prompts/registry.test.ts 是 npm test 里的哈希闸门——
 * 内容一旦被改而 version 没 bump，快照立刻变红。
 */
import type { Lang } from '../config.ts';

/** 受治理的提示词：改内容必须 bump version，否则 tests/prompts/registry.test.ts 的哈希快照会变红。 */
export interface VersionedPrompt {
  readonly id: string;
  readonly version: `v${number}`;
  /**
   * 提示词正文（按语言）。**注入角色由调用点决定**：8 条里 7 条以 `role:'system'` 注入，
   * 而 jsonRepairNudge 是重试时以 `role:'user'` 追加的纠偏提示——故此字段叫 text 而非 system。
   */
  readonly text: Readonly<Record<Lang, string>>;
}
