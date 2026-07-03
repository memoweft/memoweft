/**
 * MemoWeft 独立采集运行器（阶段 8-A · 真采集器 V1）。独立进程，不改 server.mjs / index.html。
 *
 * 干什么：每 N 秒采一次 Windows 前台窗口 → 连续相同合并 → 停留够阈值的段
 * POST 到测试台已有的注入端点 /api/observe-window（JSON · UTF-8）。
 * 隐私红线：POST 不带 allowCloud → 服务端走 observed 保守默认（本地可读 / 不上云 / 可推画像）。
 *
 * 用法（先另开一个终端 npm run testbench 起测试台）：
 *   npm run collector                       # 缺省：5s 采一次，停留 ≥30s 才落
 *   node testbench/run-collector.mjs 2 10   # 可选参数：采样间隔秒 + 产出阈值秒（冒烟调短用）
 *   MEMOWEFT_TESTBENCH_URL=http://localhost:7899 …  # 可选：改测试台地址（冒烟指向一次性回显服务器）
 * Ctrl+C 优雅退出：冲刷最后一段再走。
 */
import { createActiveWindowCollector } from '../src/perception/collectors/activeWindowCollector.ts';
import { sampleForegroundWindowWin32, foregroundSamplerSupported } from '../src/perception/collectors/win32Foreground.ts';
import { config } from '../src/config.ts';

const BASE_URL = process.env.MEMOWEFT_TESTBENCH_URL || 'http://localhost:7888';
const ENDPOINT = `${BASE_URL}/api/observe-window`;

// 可选 CLI 参数：[采样间隔秒] [产出阈值秒]；不传/传瞎了 → 用 config 缺省。
const argInterval = Number(process.argv[2]);
const argMin = Number(process.argv[3]);
const sampleIntervalSec = Number.isFinite(argInterval) && argInterval > 0 ? argInterval : config.activeWindowCollector.sampleIntervalSec;
const minDurationSec = Number.isFinite(argMin) && argMin > 0 ? argMin : config.activeWindowCollector.minDurationSec;

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

/** 把合并后的段 POST 给测试台（重试 3 次防测试台短暂没起；仍失败则丢弃并明说）。 */
async function postSample(sample) {
  const body = JSON.stringify({
    app: sample.app,
    title: sample.title,
    durationSec: sample.durationSec,
    occurredAt: sample.occurredAt,
    // 注意：不带 allowCloud → 服务端走 observed 保守默认（不上云）。这是隐私红线，别加。
  });
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const res = await fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'content-type': 'application/json; charset=utf-8' },
        body,
      });
      const json = await res.json().catch(() => ({}));
      const stored = Array.isArray(json.stored) ? json.stored.length : 0;
      const skipped = Number(json.skipped) || 0;
      console.log(`[${ts()}] 服务端返回：HTTP ${res.status} · 新落库 ${stored} 条 · 幂等跳过 ${skipped} 条`);
      return;
    } catch (err) {
      console.error(`[${ts()}] 写入失败（第 ${attempt}/3 次）：${err?.message ?? err}${attempt < 3 ? '，2s 后重试' : ''}`);
      if (attempt < 3) await new Promise((r) => setTimeout(r, 2000));
    }
  }
  console.error(`[${ts()}] 这段观察丢弃（测试台是不是没起？npm run testbench）：${fmtWin(sample)} 停留 ${sample.durationSec}s`);
}

const collector = createActiveWindowCollector({
  sampler: loggingSampler,
  sampleIntervalSec,
  minDurationSec,
  onError: (err) => console.error(`[${ts()}] 采集循环出错（不崩，继续）：`, err),
  async onEmit({ sample }) {
    console.log(`[${ts()}] 产出：${fmtWin(sample)} 停留 ${sample.durationSec}s（≥${minDurationSec}s）→ POST ${ENDPOINT}`);
    await postSample(sample);
  },
});

console.log(`[采集器] MemoWeft 活动窗口采集器 V1（仅 Windows · 零依赖）`);
console.log(`[采集器] 采样间隔 ${sampleIntervalSec}s · 产出阈值 ${minDurationSec}s（不足丢弃）· 目标 ${ENDPOINT}`);
console.log(`[采集器] observed 隐私默认：本地可读 / 不上云 / 可推画像（本进程不带任何上云授权）`);
console.log(`[采集器] Ctrl+C 退出（会冲刷最后一段）`);

// 起测试台了吗？轻探一下，没起也继续跑（写入时还会重试），只是把话说明白。
try {
  await fetch(BASE_URL, { method: 'GET' });
  console.log(`[采集器] 测试台在线：${BASE_URL}`);
} catch {
  console.warn(`[采集器] ⚠ 现在连不上 ${BASE_URL}——先 npm run testbench 起测试台，否则产出会丢`);
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
