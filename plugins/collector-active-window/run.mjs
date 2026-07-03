/**
 * MemoWeft 活动窗口采集器 · 独立运行器（Collector Plugin V1）。独立进程，零依赖，仅 Windows。
 *
 * 数据流（架构归位路线 §3）：
 *   本运行器采窗口 → 映射成 generic Observation → POST Host /api/observe
 *     → Host 审核（剥上云授权 + 采集开关）→ core.ingestObservation → Core 落 observed 证据。
 *   采集插件【绝不直穿 Core / Store】，一律经 Host 审核这道门。
 *
 * 干什么：每 N 秒采一次 Windows 前台窗口 → 连续相同合并 → 停留够阈值的段
 * 经 activeWindowToObservation 映射成 Observation，POST 给 Host /api/observe（JSON · UTF-8）。
 * 隐私红线：POST 的 Observation 不带 allowCloudRead → Host 审核再强制剥一道 → Core 走 observed 保守默认
 *   （本地可读 / 不上云 / 可推画像）。上云是记忆管理页的人工动作，不是采集默认。
 *
 * 用法（先另开一个终端 npm start -w @memoweft/host 起 Host）：
 *   npm run collector                       # 缺省：5s 采一次，停留 ≥30s 才落
 *   node plugins/collector-active-window/run.mjs 2 10   # 可选参数：采样间隔秒 + 产出阈值秒（冒烟调短用）
 *   MEMOWEFT_HOST_URL=http://localhost:7788 …           # 可选：改 Host 地址（缺省 :7788）
 * Ctrl+C 优雅退出：冲刷最后一段再走。
 */
import {
  createActiveWindowCollector,
  DEFAULT_SAMPLE_INTERVAL_SEC,
  DEFAULT_MIN_DURATION_SEC,
} from './src/activeWindowCollector.ts';
import { sampleForegroundWindowWin32, foregroundSamplerSupported } from './src/win32Foreground.ts';

// Host 地址：缺省 :7788（Host 默认端口）。旧 testbench 的 :7888 已不是采集目标。
const BASE_URL = process.env.MEMOWEFT_HOST_URL || 'http://localhost:7788';
const ENDPOINT = `${BASE_URL}/api/observe`;

// 可选 CLI 参数：[采样间隔秒] [产出阈值秒]；不传/传瞎了 → 用插件自带缺省。
const argInterval = Number(process.argv[2]);
const argMin = Number(process.argv[3]);
const sampleIntervalSec = Number.isFinite(argInterval) && argInterval > 0 ? argInterval : DEFAULT_SAMPLE_INTERVAL_SEC;
const minDurationSec = Number.isFinite(argMin) && argMin > 0 ? argMin : DEFAULT_MIN_DURATION_SEC;

if (!foregroundSamplerSupported()) {
  console.error(`[采集器] 本采集器 V1 只支持 Windows（当前平台 ${process.platform}），退出。`);
  process.exit(1);
}

const ts = () => new Date().toLocaleTimeString('zh-CN', { hour12: false });
const fmtWin = (w) => `${w.app}${w.title ? `（${w.title}）` : ''}`;

// 展示用状态（只为打日志"新窗口/继续累计"，合并真逻辑在采集器里）。
let shown = null; // { app, title, sinceMs }

/** 包一层采样器：把每次采到什么打出来。 */
async function loggingSampler() {
  const win = await sampleForegroundWindowWin32();
  if (!win) {
    if (shown) console.log(`[${ts()}] 采样：取不到前台窗口（锁屏/出错）→ 当前段截断`);
    shown = null;
    return null;
  }
  if (shown && shown.app === win.app && shown.title === win.title) {
    const heldSec = Math.round((Date.now() - shown.sinceMs) / 1000);
    console.log(`[${ts()}] 采样：${fmtWin(win)} —— 继续累计 ${heldSec}s`);
  } else {
    console.log(`[${ts()}] 采样：${fmtWin(win)} —— 新窗口，开始累计`);
    shown = { app: win.app, title: win.title, sinceMs: Date.now() };
  }
  return win;
}

/** 把合并后段的 generic Observation POST 给 Host（重试 3 次防 Host 短暂没起；仍失败则丢弃并明说）。 */
async function postObservation(observation, sample) {
  // POST generic Observation 数组；不带任何 allowCloudRead → Host 审核 + Core observedDefaults 保证不上云。
  const body = JSON.stringify({ observations: [observation] });
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const res = await fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'content-type': 'application/json; charset=utf-8' },
        body,
      });
      const json = await res.json().catch(() => ({}));
      const stored = Number(json.stored) || 0;
      const skipped = Number(json.skipped) || 0;
      console.log(`[${ts()}] Host 返回：HTTP ${res.status} · 新落库 ${stored} 条 · 幂等跳过 ${skipped} 条`);
      return;
    } catch (err) {
      console.error(`[${ts()}] 写入失败（第 ${attempt}/3 次）：${err?.message ?? err}${attempt < 3 ? '，2s 后重试' : ''}`);
      if (attempt < 3) await new Promise((r) => setTimeout(r, 2000));
    }
  }
  console.error(`[${ts()}] 这段观察丢弃（Host 是不是没起？npm start -w @memoweft/host）：${fmtWin(sample)} 停留 ${sample.durationSec}s`);
}

const collector = createActiveWindowCollector({
  sampler: loggingSampler,
  sampleIntervalSec,
  minDurationSec,
  onError: (err) => console.error(`[${ts()}] 采集循环出错（不崩，继续）：`, err),
  async onEmit({ sample, observation }) {
    console.log(`[${ts()}] 产出：${fmtWin(sample)} 停留 ${sample.durationSec}s（≥${minDurationSec}s）→ POST ${ENDPOINT}`);
    await postObservation(observation, sample);
  },
});

console.log(`[采集器] MemoWeft 活动窗口采集器 V1（仅 Windows · 零依赖 · Collector Plugin）`);
console.log(`[采集器] 采样间隔 ${sampleIntervalSec}s · 产出阈值 ${minDurationSec}s（不足丢弃）· 目标 ${ENDPOINT}`);
console.log(`[采集器] observed 隐私默认：本地可读 / 不上云 / 可推画像（本进程不带任何上云授权）`);
console.log(`[采集器] Ctrl+C 退出（会冲刷最后一段）`);

// 起 Host 了吗？轻探一下，没起也继续跑（写入时还会重试），只是把话说明白。
try {
  await fetch(BASE_URL, { method: 'GET' });
  console.log(`[采集器] Host 在线：${BASE_URL}`);
} catch {
  console.warn(`[采集器] ⚠ 现在连不上 ${BASE_URL}——先 npm start -w @memoweft/host 起 Host，否则产出会丢`);
}

collector.start();

let quitting = false;
async function shutdown(signal) {
  if (quitting) return;
  quitting = true;
  console.log(`\n[${ts()}] 收到 ${signal}，冲刷最后一段后退出…`);
  await collector.stop(); // stop 内部冲刷：最后一段够阈值就 POST
  console.log(`[${ts()}] 已退出。`);
  process.exit(0);
}
process.on('SIGINT', () => { void shutdown('SIGINT'); });
process.on('SIGTERM', () => { void shutdown('SIGTERM'); });
