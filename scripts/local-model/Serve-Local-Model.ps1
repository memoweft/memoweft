[CmdletBinding()]
param(
    [ValidateRange(1, 1800)][int]$WaitSeconds = 300
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

# Do not inherit a possibly unrelated llama.cpp API key. Never inspect or print it.
Remove-Item Env:LLAMA_API_KEY -ErrorAction SilentlyContinue

Assert-LocalModelInstallation
Initialize-LocalModelDirectories

$launchLock = Enter-LocalModelLifecycleLock
try {
    $existingState = Get-LocalModelState
    if ($null -ne $existingState) {
        $existingInspection = Get-LocalModelProcessInspection -TargetProcessId ([int]$existingState.pid) -ExpectedProcessStartTicks ([string]$existingState.processStartTicks)
        if ($existingInspection.ProcessExists) {
            throw "A state-managed process already exists at PID $($existingState.pid). Use Stop-Local-Model.cmd first."
        }
        Remove-LocalModelState -ExpectedProcessId ([int]$existingState.pid) -ExpectedProcessStartTicks ([string]$existingState.processStartTicks)
    }
    if (Test-LocalModelPortInUse) {
        throw "Port $($script:ServerPort) on $($script:BindAddress) is already in use."
    }

    $logs = New-LocalModelLogSet
    $process = Start-Process `
        -FilePath $script:ServerExecutable `
        -ArgumentList (Get-ServerArgumentString) `
        -WorkingDirectory $script:RuntimeRoot `
        -NoNewWindow `
        -RedirectStandardOutput $logs.Stdout `
        -RedirectStandardError $logs.Stderr `
        -PassThru
    $state = Write-LocalModelState -Process $process -Mode foreground -Logs $logs
}
finally {
    Exit-LocalModelLifecycleLock -Lock $launchLock
}

Write-Host "Starting local model in foreground (PID $($state.pid))."
Write-Host "Live server output is recorded under $($script:LogRoot)."
Write-Host 'Press Ctrl+C to stop this foreground session.'

try {
    Wait-LocalModelHealth -TargetProcessId $process.Id -ExpectedProcessStartTicks ([string]$state.processStartTicks) -TimeoutSeconds $WaitSeconds | Out-Null
    Write-Host "Healthy: $($script:HealthUrl)"
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        $remainingState = Get-LocalModelState
        if ($null -eq $remainingState) {
            Write-Host 'Foreground server was stopped by the safe stop command.'
        }
        else {
            throw "llama-server exited with code $($process.ExitCode). See $($logs.Stderr)."
        }
    }
}
finally {
    $cleanupLock = Enter-LocalModelLifecycleLock
    try {
        if (-not $process.HasExited) {
            Stop-VerifiedLocalModelProcess -State $state | Out-Null
        }
        else {
            Remove-LocalModelState -ExpectedProcessId $process.Id -ExpectedProcessStartTicks ([string]$state.processStartTicks)
        }
    }
    finally {
        Exit-LocalModelLifecycleLock -Lock $cleanupLock
    }
}
