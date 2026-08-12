[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

$lifecycleLock = Enter-LocalModelLifecycleLock -TimeoutSeconds 10
try {
  $state = Get-LocalModelState
  if ($null -eq $state) {
    $listenerSnapshot = Get-LocalModelListenerSnapshot
    if ($listenerSnapshot.Kind -eq 'none') {
        Write-Host 'State: stopped (no managed PID or loopback listener)'
        Write-Host 'Health: unavailable'
        exit 1
    }

    $health = Get-LocalModelHealth
    Write-LocalModelUnmanagedListenerDiagnostics -Snapshot $listenerSnapshot -Health $health
    $identityMatches = (
        $listenerSnapshot.Kind -eq 'single' -and
        $listenerSnapshot.Inspections.Count -eq 1 -and
        $listenerSnapshot.Inspections[0].Verified
    )
    if ($identityMatches) { exit 2 }
    exit 3
  }

$health = Get-LocalModelHealth
$inspection = Get-LocalModelProcessInspection -TargetProcessId ([int]$state.pid) -ExpectedProcessStartTicks ([string]$state.processStartTicks)
Write-Host "PID: $($state.pid)"
Write-Host "Identity: $($inspection.Reason)"
Write-Host "Mode: $($state.mode)"
Write-Host "Endpoint: http://$($state.bindAddress):$($state.port)/v1"
Write-Host "Alias: $($state.alias)"
Write-Host "stderr log: $($state.stderrLog)"

if (-not $inspection.ProcessExists) {
    Write-Host 'Health: process has exited (state is stale)'
    exit 1
}
if (-not $inspection.Verified) {
    Write-Host 'Health: not queried as authoritative because PID identity validation failed'
    exit 3
}
if ($health.Healthy) {
    Write-Host "Health: HTTP $($health.StatusCode) $($health.Body)"
    exit 0
}
Write-Host "Health: unavailable or loading ($($health.StatusCode))"
exit 2
}
finally {
  Exit-LocalModelLifecycleLock -Lock $lifecycleLock
}
