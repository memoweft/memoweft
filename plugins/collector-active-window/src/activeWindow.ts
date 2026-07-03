/**
 * 活动窗口采集器 · 契约 + 标准化映射（Collector Plugin，架构归位后从 Core 迁出）。
 *
 * 边界（boundaries.md §4.1 / 四步定案 #1）：MemoWeft Core 只管"认知 + 通用摄入口（generic Observation）"；
 * 从操作系统抓窗口、把窗口样本翻译成 Observation，都是【采集插件】的知识，不属于 Core。
 *
 * 本文件是插件对 Core 的产出契约：把一条活动窗口样本映射成通用 Observation（纯函数，可测），
 * 由运行器 / 宿主经 Host /api/observe → core.ingestObservation 落库。停留时长由采集器算好再喂
 * （Core 不碰"几点进/出窗口"这种平台细节）。
 */
import type { Observation } from 'memoweft';

/** 采集器算好的一条活动窗口样本（durationSec 已由采集器计算）。 */
export interface ActiveWindowSample {
  /** 应用名，例 "VS Code"。 */
  app: string;
  /** 窗口标题，例 "DLA_rebuild"。本版不读正文，只取标题。 */
  title: string;
  /** 停留时长（秒），采集器算好。 */
  durationSec: number;
  /** 该窗口会话发生时刻（ISO）。 */
  occurredAt: string;
}

/** 把一条活动窗口样本标准化成通用 Observation（纯函数，可测）。 */
export function activeWindowToObservation(s: ActiveWindowSample): Observation {
  const minutes = Math.round(s.durationSec / 60);
  const title = s.title ? `（${s.title}）` : '';
  return {
    kind: 'active_window',
    occurredAt: s.occurredAt,
    content: `在 ${s.app}${title}停留约 ${minutes} 分钟`,
    // 幂等键：同一 app + 标题 + 时刻只落一条（防重复注入 / 重复采集）。
    originId: `active_window:${s.app}:${s.title}:${s.occurredAt}`,
    meta: { app: s.app, title: s.title, durationSec: s.durationSec },
  };
}

/**
 * 活动窗口采集器契约（start/stop）。真实现见 activeWindowCollector.ts，
 * 内部把样本经 activeWindowToObservation → 产出，由运行器 POST 给 Host /api/observe。
 */
export interface ActiveWindowCollector {
  start(): void;
  stop(): void;
}
