/**
 * 活动窗口采集循环（Collector Plugin）。使用 activeWindow.ts 定义的平台适配器接口：
 * 实现 ActiveWindowCollector（start/stop），并加 pause/resume/tick。
 *
 * 职责边界：本文件只管「定时采样 → 连续相同合并 → 阈值过滤 → 产出」；
 * 停留时长由这里算好（Core 不碰"几点进/出窗口"的平台细节），产出统一走
 * activeWindowToObservation → 由调用方（运行器）决定落到哪（POST Host /api/observe）。
 * observed 默认不进入 MemoWeft 内建云写模型 prompt 不在这层放宽：产出的 Observation 不带任何显式授权位，
 * 下游 Host 审核 + Core ingestObservations 才套 observed 保守默认（local✓ / cloud✗ / infer✓）。
 *
 * 合并 / 计时口径：
 *   - 每 tick 采一次前台窗口；连续相同 app+title → 同一段，只推进 lastMs。
 *   - 切换：旧段截到「发现切换的这一 tick」（时长按真实时钟差算，不按 tick 数 × 间隔）。
 *   - 采不到（null：锁屏/出错）：旧段保守截到「最后一次确认看见」的时刻，不知道的时间不算停留。
 *   - stop / pause：冲刷当前段，截到冲刷时刻。
 *   - 段时长 < minDurationSec → 碎片丢弃（路过式切窗不算"停留"）。
 *
 * 可测性：sampler / 时钟 now / 定时器 setIntervalFn 全可注入——测试喂假采样序列 + 假时间，
 * 直接调 tick() 驱动，不起真定时器、不碰真 Win32。
 */
import type { Observation } from 'memoweft';
import {
  activeWindowToObservation,
  type ActiveWindowCollector,
  type ActiveWindowSample,
} from './activeWindow.ts';

/** 采集参数缺省（本插件自持，不依赖 Core 的 config；宿主可按需要覆盖）。 */
export const DEFAULT_SAMPLE_INTERVAL_SEC = 5; // 5s 采一次：够分辨"在哪个窗口"，又不至于狂 spawn PowerShell
export const DEFAULT_MIN_DURATION_SEC = 30; // 停留 <30s 的碎片丢弃：路过式切窗不算"停留"

/** 一次前台窗口采样结果（无时长——时长由本采集循环合并计算）。 */
export interface ForegroundWindow {
  app: string;
  title: string;
}

/** 采样函数契约：取当前前台窗口；取不到（锁屏 / 非 Windows / 出错）→ null，不许 throw 崩循环。 */
export type ForegroundSampler = () => Promise<ForegroundWindow | null>;

/** 一次产出：合并后的样本 + 标准化 Observation（未带显式授权位 → 下游走 observed 保守默认）。 */
export interface ActiveWindowEmit {
  sample: ActiveWindowSample;
  observation: Observation;
}

export interface ActiveWindowCollectorOptions {
  /** 前台窗口采样函数（真机传 sampleForegroundWindowWin32；测试注入假序列）。 */
  sampler: ForegroundSampler;
  /** 合并段产出回调（只有 durationSec ≥ 阈值的段会到这里）。 */
  onEmit: (e: ActiveWindowEmit) => void | Promise<void>;
  /** 采样间隔秒；缺省 DEFAULT_SAMPLE_INTERVAL_SEC。 */
  sampleIntervalSec?: number;
  /** 产出阈值秒（时长低于它的碎片丢弃）；缺省 DEFAULT_MIN_DURATION_SEC。 */
  minDurationSec?: number;
  /** 时钟（测试注入假时间）；缺省 Date.now。 */
  now?: () => number;
  /** 采样 / 产出回调出错时上报（缺省静默，循环不崩）；运行器注入 console.error。 */
  onError?: (err: unknown) => void;
  /** 定时器注入（测试传 no-op 假定时器 + 手动 tick）；缺省全局 setInterval/clearInterval。 */
  setIntervalFn?: (fn: () => void, ms: number) => unknown;
  clearIntervalFn?: (timer: unknown) => void;
}

/** 运行时采集器：start/stop + pause/resume/tick（stop/pause 会异步冲刷，返回 Promise）。 */
export interface RunningActiveWindowCollector extends ActiveWindowCollector {
  start(): void;
  /** 暂停：冲刷当前段后停止采样（pause 期间一次都不采）。 */
  pause(): Promise<void>;
  /** 恢复采样（新起一段，不接续 pause 前的段）。 */
  resume(): void;
  /** 停止：冲刷最后一段并收尾。stop 后不可重启（新建实例）。 */
  stop(): Promise<void>;
  /** 手动驱动一次「采样→合并」（start 的定时器内部就是调它；测试直接调，不起真定时器）。 */
  tick(): Promise<void>;
  /** 当前状态（观察用）。 */
  readonly state: 'idle' | 'running' | 'paused' | 'stopped';
}

/** 内部：正在累计的一段（同 app+title 连续出现）。 */
interface Segment {
  app: string;
  title: string;
  /** 段开始时刻（第一次看见，ms）。 */
  startMs: number;
  /** 最后一次确认看见的时刻（ms）。 */
  lastMs: number;
}

/** 工厂：创建活动窗口采集器（真采集 = sampler 传 Win32 采样器；测试 = 全注入）。 */
export function createActiveWindowCollector(
  opts: ActiveWindowCollectorOptions,
): RunningActiveWindowCollector {
  const intervalMs = Math.max(
    200,
    Math.round((opts.sampleIntervalSec ?? DEFAULT_SAMPLE_INTERVAL_SEC) * 1000),
  );
  const minDurationSec = opts.minDurationSec ?? DEFAULT_MIN_DURATION_SEC;
  const now = opts.now ?? Date.now;
  const onError = opts.onError ?? (() => {});
  const setIntervalFn = opts.setIntervalFn ?? ((fn: () => void, ms: number) => setInterval(fn, ms));
  const clearIntervalFn =
    opts.clearIntervalFn ?? ((t: unknown) => clearInterval(t as ReturnType<typeof setInterval>));

  let state: RunningActiveWindowCollector['state'] = 'idle';
  let timer: unknown = null;
  let ticking = false; // 防重入：上一次采样还没回来 → 本 tick 跳过
  let segment: Segment | null = null;

  /** 段收尾：够阈值才产出（碎片丢弃）；产出走 activeWindowToObservation，不带显式授权位。 */
  async function emitSegment(seg: Segment, endMs: number): Promise<void> {
    const durationSec = Math.round((endMs - seg.startMs) / 1000);
    if (durationSec < minDurationSec) return; // 碎片：不产出
    const sample: ActiveWindowSample = {
      app: seg.app,
      title: seg.title,
      durationSec,
      occurredAt: new Date(seg.startMs).toISOString(),
    };
    try {
      await opts.onEmit({ sample, observation: activeWindowToObservation(sample) });
    } catch (err) {
      onError(err); // 产出失败（如 Host 没起）不崩采集循环
    }
  }

  /** 冲刷当前段（stop / pause / 采不到时用），endMs 由调用方定口径。 */
  async function flush(endMs: number): Promise<void> {
    if (!segment) return;
    const seg = segment;
    segment = null;
    await emitSegment(seg, endMs);
  }

  /** 合并一步：null → 保守冲刷；相同 → 推进；不同 → 旧段截到当下、新段开始。 */
  async function handleSample(win: ForegroundWindow | null, atMs: number): Promise<void> {
    if (!win) {
      // 采不到：不知道的时间不算停留，旧段截到最后一次确认看见的时刻。
      if (segment) await flush(segment.lastMs);
      return;
    }
    if (segment && segment.app === win.app && segment.title === win.title) {
      segment.lastMs = atMs; // 连续相同 → 合并累计
      return;
    }
    const prev = segment;
    segment = { app: win.app, title: win.title, startMs: atMs, lastMs: atMs };
    if (prev) await emitSegment(prev, atMs); // 切换：旧段截到「发现切换」这一刻
  }

  async function tick(): Promise<void> {
    if (state !== 'running' || ticking) return;
    ticking = true;
    try {
      let win: ForegroundWindow | null = null;
      try {
        win = await opts.sampler();
      } catch (err) {
        onError(err); // sampler 违约 throw 也当 null 处理，不崩
      }
      if (state !== 'running') return; // 等采样期间被 stop/pause 了 → 丢弃本次
      await handleSample(win, now());
    } finally {
      ticking = false;
    }
  }

  function stopTimer(): void {
    if (timer != null) {
      clearIntervalFn(timer);
      timer = null;
    }
  }

  return {
    get state() {
      return state;
    },
    start(): void {
      if (state === 'running') return;
      if (state === 'stopped') throw new Error('采集器已 stop，不能重启；请新建实例');
      state = 'running';
      timer = setIntervalFn(() => {
        void tick();
      }, intervalMs);
    },
    async pause(): Promise<void> {
      if (state !== 'running') return;
      state = 'paused';
      stopTimer();
      await flush(now()); // 冲刷当前段：pause 前的停留不丢（够阈值就产出）
    },
    resume(): void {
      if (state !== 'paused') return;
      state = 'running';
      timer = setIntervalFn(() => {
        void tick();
      }, intervalMs);
    },
    async stop(): Promise<void> {
      if (state === 'stopped') return;
      state = 'stopped';
      stopTimer();
      await flush(now()); // 冲刷最后一段
    },
    tick,
  };
}
