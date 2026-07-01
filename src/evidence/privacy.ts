/**
 * 隐私过滤（地图 cell 8 隐私规则：本地优先、授权位真生效）。
 *
 * filterCloudReadable：把"不许上云"（allowCloudRead=false）的证据挡在【喂给云端模型的材料】之外。
 * 写路径三处（distill / consolidate / attribute）取证据喂 LLM 前共用这一道关。
 *
 * ⚠️ 前提（重要，别当成永久死规则）：本函数【假设调用处的 deps.llm 是云端模型】，故按 allowCloudRead 筛。
 *   将来写路径接入【本地模型】（如 3090 本地推理）时，本地模型可读 cloud=false 的证据——
 *   届时不能再无脑调本函数，要改成"按当前模型是云端/本地决定筛不筛"。
 *   完整版思路（留给"上本地模型"任务，本次不做）：给 LLMClient 加 'cloud'|'local' 标识，按 tier 决定。
 */

/** 只保留"允许发送给云端模型"的项（allowCloudRead=true）；其余原样顺序保留。 */
export function filterCloudReadable<T extends { allowCloudRead: boolean }>(items: T[]): T[] {
  return items.filter((e) => e.allowCloudRead);
}
