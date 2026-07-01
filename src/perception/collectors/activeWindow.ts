/**
 * 活动窗口采集器（阶段 4-A 档1 · 骨架）。
 *
 * 边界（cell 9 / 四步定案 #1）：MemoWeft 核心只管"认知 + 通用摄入口"；从操作系统抓窗口是【独立可选外挂】。
 * 本版【只留契约 + 标准化映射】，不实现长驻采集、不引入 active-win 依赖（依赖最小化，cell 11）。
 *
 * 真采集器（下一版做）：长驻进程，监听窗口切换、算停留时长，定时把 ActiveWindowSample
 * 经 activeWindowToObservation → ingestObservations 批量落库。
 * 停留时长由【采集器】算好再喂（四步定案：MemoWeft 不碰"几点进/出窗口"这种平台细节）。
 */
import type { Observation } from '../ingest.ts';

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
 * 真采集器契约（骨架，未实现）。下一版实现：start 起长驻监听、stop 收尾；
 * 内部把样本经 activeWindowToObservation → ingestObservations 落库。
 */
export interface ActiveWindowCollector {
  start(): void;
  stop(): void;
}
