[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

$state = Get-LocalModelState
if ($null -eq $state) {
    $listenerSnapshot = Get-LocalModelListenerSnapshot
    if ($listenerSnapshot.Kind -eq 'none') {
        Write-Host 'Local model is already stopped (no state-managed PID or loopback listener).'
        exit 0
    }

    $health = Get-LocalModelHealth
    Write-LocalModelUnmanagedListenerDiagnostics -Snapshot $listenerSnapshot -Health $health
    Write-Host 'Action: Refusing to stop unmanaged listener without trusted state.'
    $identityMatches = (
        $listenerSnapshot.Kind -eq 'single' -and
        $listenerSnapshot.Inspections.Count -eq 1 -and
        $listenerSnapshot.Inspections[0].Verified
    )
    if ($identityMatches) { exit 2 }
    exit 3
}

$result = Stop-VerifiedLocalModelProcess -TargetProcessId ([int]$state.pid)
if ($result.AlreadyExited) {
    Write-Host "Removed stale state for exited PID $($result.ProcessId)."
}
else {
    Write-Host "Stopped verified local model PID $($result.ProcessId)."
}
