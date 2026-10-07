[CmdletBinding()]
param(
    [ValidateRange(1, 1800)][int]$WaitSeconds = 300
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

# llama.cpp maps LLAMA_API_KEY to --api-key. The launcher intentionally removes
# it from this short-lived PowerShell process so the child cannot inherit an
# unrelated external credential and unexpectedly require Authorization headers.
# The value is never read or printed.
Remove-Item Env:LLAMA_API_KEY -ErrorAction SilentlyContinue

$lifecycleLock = Enter-LocalModelLifecycleLock
try {
    Assert-LocalModelInstallation
    Initialize-LocalModelDirectories

    $existingState = Get-LocalModelState
    if ($null -ne $existingState) {
        $existingInspection = Get-LocalModelProcessInspection -TargetProcessId ([int]$existingState.pid) -ExpectedProcessStartTicks ([string]$existingState.processStartTicks)
        if ($existingInspection.ProcessExists) {
            if (-not $existingInspection.Verified) {
                throw "State PID $($existingState.pid) belongs to a different process. Refusing to start or overwrite state: $($existingInspection.Reason)."
            }
            $existingHealth = Get-LocalModelHealth
            if ($existingHealth.Healthy) {
                Write-Host "Local model is already healthy (PID $($existingState.pid), $($script:HealthUrl))."
                exit 0
            }
            throw "The verified llama-server PID $($existingState.pid) is running but is not healthy. Inspect $($existingState.stderrLog)."
        }
        Remove-LocalModelState -ExpectedProcessId ([int]$existingState.pid) -ExpectedProcessStartTicks ([string]$existingState.processStartTicks)
    }

    if (Test-LocalModelPortInUse) {
        throw "Port $($script:ServerPort) on $($script:BindAddress) is already in use. Refusing to start an ambiguous service."
    }

    $logs = New-LocalModelLogSet
    $process = Start-Process `
        -FilePath $script:ServerExecutable `
        -ArgumentList (Get-ServerArgumentString) `
        -WorkingDirectory $script:RuntimeRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $logs.Stdout `
        -RedirectStandardError $logs.Stderr `
        -PassThru
    $state = Write-LocalModelState -Process $process -Mode background -Logs $logs

    try {
        Wait-LocalModelHealth -TargetProcessId $process.Id -ExpectedProcessStartTicks ([string]$state.processStartTicks) -TimeoutSeconds $WaitSeconds | Out-Null
    }
    catch {
        try {
            Stop-VerifiedLocalModelProcess -State $state | Out-Null
        }
        catch {
            Write-Warning $_.Exception.Message
        }
        $tail = if (Test-Path -LiteralPath $logs.Stderr) {
            (Get-Content -LiteralPath $logs.Stderr -Tail 30 | Out-String).Trim()
        }
        else {
            '(stderr log was not created)'
        }
        throw "Local model startup failed. stderr tail:`n$tail"
    }
}
finally {
    Exit-LocalModelLifecycleLock -Lock $lifecycleLock
}

Write-Host "Local model is healthy."
Write-Host "PID: $($state.pid)"
Write-Host "Endpoint: http://$($script:BindAddress):$($script:ServerPort)/v1"
Write-Host "Model alias: $($script:ModelAlias)"
Write-Host "stderr log: $($logs.Stderr)"
