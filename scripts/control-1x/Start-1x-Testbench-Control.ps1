[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Write-Warning 'The writable testbench is no longer a pristine 1.x-only control. It is the original 1.0 console upgraded in place with the MemoWeft 2.0 bridge.'
Write-Host 'Starting the unified MemoWeft workbench. Any separate immutable v1.0.0 reference is left untouched.'
& (Join-Path $repoRoot 'Start-MemoWeft-Workbench.cmd') @RemainingArgs
exit $LASTEXITCODE
