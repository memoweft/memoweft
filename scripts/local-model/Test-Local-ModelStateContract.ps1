[CmdletBinding()]
param(
    [switch]$LockContentionProbe,
    [switch]$AbandonLockProbe
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

if ($LockContentionProbe) {
    try {
        $probeLock = Enter-LocalModelLifecycleLock -TimeoutSeconds 1
        Exit-LocalModelLifecycleLock -Lock $probeLock
        exit 0
    }
    catch {
        exit 17
    }
}

if ($AbandonLockProbe) {
    $null = Enter-LocalModelLifecycleLock -TimeoutSeconds 5
    # Exiting without ReleaseMutex deliberately exercises abandoned recovery.
    exit 0
}

function Assert-StateContract {
    param([Parameter(Mandatory = $true)][bool]$Condition, [Parameter(Mandatory = $true)][string]$Message)
    if (-not $Condition) { throw "Local-model state contract failed: $Message" }
}

$logs = [pscustomobject]@{
    Stdout = Join-Path $script:LogRoot 'contract.stdout.log'
    Stderr = Join-Path $script:LogRoot 'contract.stderr.log'
}
$validV2 = [pscustomobject][ordered]@{
    schemaVersion = $script:StateSchemaVersion
    pid = 123
    processStartTicks = '638000000000000000'
    mode = 'background'
    executablePath = $script:ServerExecutable
    modelPath = $script:ModelPath
    bindAddress = $script:BindAddress
    port = $script:ServerPort
    alias = $script:ModelAlias
    argv = @($script:ServerArguments)
    stdoutLog = $logs.Stdout
    stderrLog = $logs.Stderr
}
Assert-StateContract ($null -eq (Test-LocalModelStateShape -State $validV2)) 'strict v2 shape must pass'

$extraField = $validV2 | Select-Object *
$extraField | Add-Member -NotePropertyName unexpected -NotePropertyValue $true
Assert-StateContract ((Test-LocalModelStateShape -State $extraField) -eq 'unexpected state fields') 'extra v2 fields must fail closed'

$wrongTicks = $validV2 | Select-Object *
$wrongTicks.processStartTicks = 'not-ticks'
Assert-StateContract ((Test-LocalModelStateShape -State $wrongTicks) -eq 'invalid process start ticks') 'invalid creation ticks must fail closed'

$validV1 = [pscustomobject][ordered]@{
    schemaVersion = 1
    pid = 123
    mode = 'background'
    startedAt = '2026-08-10T02:26:49.3845077Z'
    executablePath = $script:ServerExecutable
    modelPath = $script:ModelPath
    bindAddress = $script:BindAddress
    port = $script:ServerPort
    alias = $script:ModelAlias
    stdoutLog = $logs.Stdout
    stderrLog = $logs.Stderr
}
Assert-StateContract ($null -eq (Test-LocalModelStateShape -State $validV1)) 'exact legacy v1 shape must remain readable'

$currentProcess = Get-Process -Id $PID -ErrorAction Stop
$mismatch = Get-LocalModelProcessInspection -TargetProcessId $PID -ExpectedProcessStartTicks '1'
Assert-StateContract ($mismatch.ProcessExists -and -not $mismatch.Verified) 'creation mismatch must preserve process-exists truth'
Assert-StateContract ($mismatch.Reason -eq 'process-creation-time-mismatch') 'creation mismatch must have a stable reason'

$quotedCommand = (ConvertTo-NativeArgument -Argument $script:ServerExecutable) + ' ' + (Get-ServerArgumentString)
$parsedCommand = Get-LocalModelCommandLineArguments -CommandLine $quotedCommand
Assert-StateContract (
    $parsedCommand.Count -ge 1 -and
    (Test-LocalModelStringArrayEqual -Left @($parsedCommand | Select-Object -Skip 1) -Right @($script:ServerArguments))
) 'exact native argv round-trip must pass'

$hostExecutable = $currentProcess.Path
$probeArguments = @('-NoLogo','-NoProfile','-File',$PSCommandPath,'-LockContentionProbe')
$parentLock = Enter-LocalModelLifecycleLock -TimeoutSeconds 5
try {
    $contention = Start-Process -FilePath $hostExecutable -ArgumentList (($probeArguments | ForEach-Object { ConvertTo-NativeArgument -Argument ([string]$_) }) -join ' ') -WindowStyle Hidden -Wait -PassThru
    Assert-StateContract ($contention.ExitCode -eq 17) 'a concurrent lifecycle actor must time out instead of entering'
}
finally {
    Exit-LocalModelLifecycleLock -Lock $parentLock
}

$abandonArguments = @('-NoLogo','-NoProfile','-File',$PSCommandPath,'-AbandonLockProbe')
$abandon = Start-Process -FilePath $hostExecutable -ArgumentList (($abandonArguments | ForEach-Object { ConvertTo-NativeArgument -Argument ([string]$_) }) -join ' ') -WindowStyle Hidden -Wait -PassThru
Assert-StateContract ($abandon.ExitCode -eq 0) 'abandon probe must acquire the mutex before exiting'
$recoveredLock = Enter-LocalModelLifecycleLock -TimeoutSeconds 5
Exit-LocalModelLifecycleLock -Lock $recoveredLock

Write-Host 'Local-model state, argv, creation-time, contention, and abandoned-lock contracts passed.'
