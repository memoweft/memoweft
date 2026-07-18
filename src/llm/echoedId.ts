/**
 * 把模型【回显的 id】解回「白名单内的真 id」；解不出返回 null。
 *
 * 共享给三条「让模型回显 id、再精确匹配」的路径:consolidate(证据 id + cognition_id)、
 * attribute、trends。有些模型会模仿示例 id 的形态，截断真实 UUID；只做精确匹配会静默丢弃有效输出。
 *
 * 三级解析:① 标号映射(prompt 发的短标号 `e1` → 真实 id,结构化路径) → ② 精确匹配(写对时零变化)
 * → ③ 唯一前缀兜底(剥示例前缀 `ev-`/`cog-` 后,在白名单里找唯一前缀命中)。
 *
 * 安全边界:只可能解到白名单【内】、且必须**唯一命中**——捏造 id(非任何真 id 前缀)、
 * 歧义前缀(命中多条)、过短前缀(< MIN_ID_PREFIX)一律 null。宁可不记,不可记错。
 */

/** 前缀容错的最短长度:短于此一律不猜。示例占位如 `ev-1` 剥前缀后只剩 `1`,绝不能撞上真 id。 */
export const MIN_ID_PREFIX = 8;

export function resolveEchoedId(
  raw: string | undefined,
  whitelist: Set<string>,
  tagMap?: Map<string, string>,
): string | null {
  if (!raw) return null;
  const key = raw.trim();
  const byTag = tagMap?.get(key);
  if (byTag && whitelist.has(byTag)) return byTag; // ① 标号：prompt 发的就是 e1，模型照抄即可
  if (whitelist.has(raw)) return raw; // ② 精确:模型写对完整 id → 行为零变化
  const bare = key.replace(/^(ev-|cog-)/i, ''); // ③ 剥掉照抄示例的 ev-/cog- 前缀，再唯一前缀兜底
  if (bare.length < MIN_ID_PREFIX) return null;
  let hit: string | null = null;
  for (const id of whitelist) {
    if (!id.startsWith(bare)) continue;
    if (hit !== null) return null; // 歧义 → 不猜
    hit = id;
  }
  return hit;
}
