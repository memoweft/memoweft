[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

$state = Get-WorkbenchState
if ($null -eq $state) {
  if (@(Get-WorkbenchListeners).Count -eq 0) { Write-Host 'MemoWeft workbench is stopped.'; exit 1 }
  Write-Host 'Port 7888 is occupied by an unmanaged process.'; exit 2
}
$inspection = Test-WorkbenchState $state
if (-not $inspection.Verified) { Write-Host "MemoWeft workbench state is not verified: $($inspection.Reason)."; exit 2 }
$http = Test-WorkbenchHttp $state
if (-not $http.Healthy) { Write-Host "MemoWeft workbench PID $($state.pid) is verified, but HTTP identity or Next backend identity failed: $($http.Reason)."; exit 2 }
Write-Host "MemoWeft workbench is healthy at http://127.0.0.1:7888 (PID $($state.pid)); original 1.0 console + Next memory bridge are both reachable."
exit 0
