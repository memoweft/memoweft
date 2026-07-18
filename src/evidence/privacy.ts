/**
 * 隐私过滤：本地优先，并严格执行证据级授权位。
 *
 * filterReadableByTier：从【提供给模型的输入】中排除当前模型 tier 无权读取的证据。
 *   tier='cloud'：只留 allowCloudRead=true（进入 MemoWeft 内建云写模型 prompt）。
 *   tier='local'：只留 allowLocalRead=true（进入本地写模型 prompt，可读 observed 默认不进入云写 prompt 的那些）。
 * 写路径各处取证据喂 LLM 前共用这道关，按 deps.llm 的 tier（缺省 'cloud'，最保守）决定筛哪个授权位。
 *
 * ⚠️ 两维正交，别混：本关只管【内建写模型 prompt 的证据选择】（云/本地 = allowCloudRead / allowLocalRead）。是否能据此推画像
 *   是另一维 allowInference——由画像/推理路径（distill / consolidate / attribute）在本关之外另行
 *   `.filter((e) => e.allowInference)`（三处口径一致，防 inference=false 的证据经 event summary 间接渗进画像）。
 */
import type { ModelTier } from '../llm/client.ts';

/** 按模型 tier 保留"该 tier 允许读"的项；其余原样顺序保留。缺省 tier = 'cloud'（最保守：不误把敏感证据当本地放行）。 */
export function filterReadableByTier<
  T extends { allowCloudRead: boolean; allowLocalRead: boolean },
>(items: T[], tier: ModelTier = 'cloud'): T[] {
  return tier === 'local'
    ? items.filter((e) => e.allowLocalRead)
    : items.filter((e) => e.allowCloudRead);
}

/**
 * @deprecated 用 `filterReadableByTier(items, tier)`（等价于 tier='cloud'）。
 * 库内已全切 filterReadableByTier；本别名仅保兼容（privacy.test 仍直接测它、外部旧调用方兜底）。
 */
export function filterCloudReadable<T extends { allowCloudRead: boolean }>(items: T[]): T[] {
  return items.filter((e) => e.allowCloudRead);
}
