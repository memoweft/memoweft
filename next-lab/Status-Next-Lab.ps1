[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

$mutex = Enter-NextLabLifecycleMutex -TimeoutMilliseconds 3000
try {
  try { $state = Get-NextLabState } catch { Write-Host 'State: invalid managed-state file'; exit 3 }
  if ($null -eq $state) {
    $listeners = @(Get-NextLabListeners)
    if ($listeners.Count -eq 0) { Write-Host 'State: stopped (no managed state and no listener).'; exit 1 }
    $pids = ($listeners | ForEach-Object { [string]$_.OwningProcess }) -join ','
    Write-Host "State: unmanaged-or-collision listener(s) on 7891 (PID $pids)."
    exit 3
  }
  try { $inspection = Get-NextLabFullInspection $state } catch { Write-Host 'State: managed lifecycle inspection failed closed'; exit 3 }
  $stateProcessId = Get-NextLabStateProperty $state 'pid'
  Write-Host "PID: $stateProcessId"
  Write-Host "Identity: $($inspection.Reason)"
  Write-Host 'Endpoint: http://127.0.0.1:7891'
  if (-not $inspection.Verified) { Write-Host 'Health: managed lifecycle validation failed'; exit 3 }
  Write-Host 'Health: managed and identity-verified'
  exit 0
} finally {
  Exit-NextLabLifecycleMutex $mutex
}
