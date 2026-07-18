/**
 * 前台窗口采样器工厂：按平台选择实现（Collector Plugin 的平台扩展点）。
 *
 * 采集循环（activeWindowCollector）本就平台无关，只有"采一次前台窗口"这步是平台专有。
 * 要支持 macOS / Linux：在这里加一个 case、实现对应的 `sample*(): Promise<ForegroundWindow|null>`
 *   （mac 用 osascript/AppleScript 取 frontmost app + window title；Linux 用 xdotool / wmctrl），
 *   采样器契约照 ForegroundSampler（取不到返回 null、绝不 throw），采集循环一行不用改。
 *
 * ⚠️ 现只支持 Windows；macOS/Linux 尚无经过验证的采样器。其余平台返回 null，
 *   运行器据此给出明确的未支持提示并退出，避免空转。
 * ⚠️ 零依赖约束：平台采样器只使用 Node 内置能力（child_process 调系统自带命令），不引入 npm 包。
 */
import type { ForegroundSampler } from './activeWindowCollector.ts';
import { sampleForegroundWindowWin32 } from './win32Foreground.ts';

/** 已支持的平台（运行器报"未支持"时列出）。加平台时把它加进来。 */
export const SUPPORTED_PLATFORMS = ['win32'] as const;

/**
 * 按平台造前台窗口采样器；未支持的平台返回 `null`（不 throw）。
 * @param platform 缺省 `process.platform`（可注入便于测试）。
 */
export function createForegroundSampler(
  platform: string = process.platform,
): ForegroundSampler | null {
  switch (platform) {
    case 'win32':
      return () => sampleForegroundWindowWin32();
    // 新平台实现注册在此：
    //   case 'darwin': return () => sampleForegroundWindowDarwin(); // osascript / AppleScript
    //   case 'linux':  return () => sampleForegroundWindowLinux();  // xdotool / wmctrl
    default:
      return null;
  }
}
