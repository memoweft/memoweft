/**
 * Windows 前台窗口采样（Collector Plugin 的平台实现）。
 *
 * 做法：spawn Windows 自带 powershell.exe + P/Invoke（GetForegroundWindow / GetWindowTextW /
 * GetWindowThreadProcessId → Get-Process 取进程名）。零 npm 依赖，只用 node:child_process。
 *
 * 编码防线：显式使用 UTF-8，并对 PowerShell 输出做一致解码，避免系统代码页导致乱码。
 *   1. 脚本本体走 -EncodedCommand（base64 of UTF-16LE），不经命令行引号/代码页转义；
 *   2. 结果 JSON 先 UTF-8 取字节再 base64 输出——base64 是纯 ASCII，任何代码页都糟蹋不了，
 *      Node 侧再按 UTF-8 解回来。中文窗口标题全程不落进系统代码页。
 *
 * 失败面：非 Windows / 拿不到窗口 / 超时 / 输出解析不了 → 一律 resolve null，绝不 throw、不崩采集循环。
 */
import { execFile } from 'node:child_process';
import type { ForegroundWindow } from './activeWindowCollector.ts';

/** 当前平台能不能采（只支持 win32；运行器用它给出明确报错而不是空转）。 */
export function foregroundSamplerSupported(): boolean {
  return process.platform === 'win32';
}

// PowerShell 一次性脚本：取前台窗口 → {app,title} → JSON → UTF-8 字节 → base64（纯 ASCII 输出）。
// 注意别用 $PID（PowerShell 保留自动变量），进程号用 $procId。
const PS_SCRIPT = `
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class MemoWeftFg {
  [DllImport("user32.dll")]
  public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet = CharSet.Unicode)]
  public static extern int GetWindowTextW(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
  [DllImport("user32.dll")]
  public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
}
'@
$app = ''
$title = ''
$h = [MemoWeftFg]::GetForegroundWindow()
if ($h -ne [IntPtr]::Zero) {
  $sb = New-Object System.Text.StringBuilder 1024
  [void][MemoWeftFg]::GetWindowTextW($h, $sb, $sb.Capacity)
  $title = $sb.ToString()
  $procId = [uint32]0
  [void][MemoWeftFg]::GetWindowThreadProcessId($h, [ref]$procId)
  if ($procId -ne 0) {
    try { $app = (Get-Process -Id $procId -ErrorAction Stop).ProcessName } catch { $app = '' }
  }
}
$json = @{ app = $app; title = $title } | ConvertTo-Json -Compress
[Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($json))
`;

// -EncodedCommand 要 UTF-16LE 的 base64（PowerShell 规定），模块加载时算一次。
const PS_ENCODED = Buffer.from(PS_SCRIPT, 'utf16le').toString('base64');

/** 只认 base64 行（脚本唯一的 stdout 输出）；万一混进警告文本也能挑出来。 */
const BASE64_LINE = /^[A-Za-z0-9+/]+=*$/;

/**
 * 采一次当前前台窗口。取不到（非 Windows / 锁屏无前台 / 超时 / 进程名标题都空）→ null。
 * @param timeoutMs PowerShell 超时（默认 8s；Add-Type 每次冷编译约 0.5~2s，别设太紧）。
 */
export function sampleForegroundWindowWin32(timeoutMs = 8000): Promise<ForegroundWindow | null> {
  if (!foregroundSamplerSupported()) return Promise.resolve(null);
  return new Promise((resolve) => {
    execFile(
      'powershell.exe',
      [
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy',
        'Bypass',
        '-EncodedCommand',
        PS_ENCODED,
      ],
      { timeout: timeoutMs, windowsHide: true, encoding: 'utf8' },
      (err, stdout) => {
        if (err) {
          resolve(null);
          return;
        }
        try {
          // 取最后一个 base64 行解码回 UTF-8 JSON。
          const line = stdout
            .split(/\r?\n/)
            .map((l) => l.trim())
            .filter((l) => BASE64_LINE.test(l))
            .pop();
          if (!line) {
            resolve(null);
            return;
          }
          const parsed = JSON.parse(Buffer.from(line, 'base64').toString('utf8')) as {
            app?: unknown;
            title?: unknown;
          };
          const app = String(parsed.app ?? '').trim();
          const title = String(parsed.title ?? '').trim();
          if (!app && !title) {
            resolve(null);
            return;
          } // 什么都认不出 → 视为取不到
          resolve({ app: app || '未知应用', title });
        } catch {
          resolve(null);
        }
      },
    );
  });
}
