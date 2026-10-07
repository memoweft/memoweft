[CmdletBinding()]
param([switch]$StopDependencies, [switch]$KeepDependencies)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

if ($StopDependencies -and $KeepDependencies) { throw 'Use either -StopDependencies or -KeepDependencies, not both.' }
$state = Get-WorkbenchState
if ($null -ne $state) {
  Stop-VerifiedWorkbenchProcess $state
  Write-Host 'MemoWeft workbench UI stopped.'
} elseif (@(Get-WorkbenchListeners).Count -ne 0) {
  throw 'Port 7888 is occupied without trusted workbench state; refusing to stop it.'
} else {
  Write-Host 'MemoWeft workbench UI is already stopped.'
}

if ($StopDependencies) {
  & (Join-Path $script:ProjectRoot 'Stop-Next-Lab.cmd')
  if ($LASTEXITCODE -ne 0) { throw 'Next backend stop did not complete cleanly.' }
  & (Join-Path $script:ProjectRoot 'Stop-Local-Model.cmd')
  if ($LASTEXITCODE -ne 0) { throw 'Local model stop did not complete cleanly.' }
}
