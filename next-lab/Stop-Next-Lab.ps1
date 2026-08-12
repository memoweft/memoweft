[CmdletBinding()]
param([switch]$RecoverUnhealthy)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

$mutex = Enter-NextLabLifecycleMutex
try {
  try { $state = Get-NextLabState } catch { Write-Host 'Refusing to stop: managed state file is invalid.'; exit 3 }
  if ($null -eq $state) {
    $listeners = @(Get-NextLabListeners)
    if ($listeners.Count -eq 0) { Write-Host 'Next Lab is stopped (no managed state and no listener).'; exit 0 }
    $pids = ($listeners | ForEach-Object { [string]$_.OwningProcess }) -join ','
    Write-Host "Refusing to stop unmanaged-or-collision listener(s) on 7891 (PID $pids)."
    exit 3
  }
  try { $inspection = Get-NextLabFullInspection $state } catch { Write-Host 'Refusing to stop: managed lifecycle inspection failed closed. State retained for diagnosis.'; exit 3 }
  if (-not $inspection.Verified) {
    if (-not $RecoverUnhealthy) { Write-Host "Refusing to stop managed state: $($inspection.Reason). State retained for diagnosis."; exit 3 }
    $transport = Get-NextLabTransportInspection $state
    if (-not $transport.Verified) { Write-Host "Refusing unhealthy recovery stop: $($transport.Reason). State retained for diagnosis."; exit 3 }
    Write-Host "Recovering unresponsive managed Next Lab after transport identity verification (PID $($transport.Process.ProcessId))."
    $inspection = $transport
  }
  $process = Get-Process -Id ([int]$inspection.Process.ProcessId) -ErrorAction SilentlyContinue
  if ($null -eq $process) { Write-Host 'Refusing to stop: process disappeared during verification. State retained for diagnosis.'; exit 3 }
  try { $reinspection = if ($RecoverUnhealthy) { Get-NextLabTransportInspection $state } else { Get-NextLabFullInspection $state } } catch { Write-Host 'Refusing to stop: identity reinspection failed closed. State retained for diagnosis.'; exit 3 }
  if (-not $reinspection.Verified) { Write-Host "Refusing to stop: identity changed before termination ($($reinspection.Reason)). State retained for diagnosis."; exit 3 }
  Stop-Process -InputObject $process -ErrorAction Stop
  $until = (Get-Date).AddSeconds(15)
  while ((Get-Date) -lt $until) {
    if (@(Get-NextLabListeners).Count -eq 0) {
      Remove-NextLabState ([int](Get-NextLabStateProperty $state 'pid'))
      Write-Host 'Next Lab stopped.'
      exit 0
    }
    Start-Sleep -Milliseconds 200
  }
  Write-Host 'Next Lab listener did not disappear; state retained for diagnosis.'
  exit 3
} finally {
  Exit-NextLabLifecycleMutex $mutex
}
