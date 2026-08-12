[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

function Assert-LifecycleCondition {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if (-not $Condition) {
        throw "Lifecycle smoke assertion failed: $Message"
    }
}

function Invoke-LocalModelChildScript {
    param([Parameter(Mandatory = $true)][string]$ScriptName)

    $scriptPath = Join-Path $PSScriptRoot $ScriptName
    $powerShellPath = (Get-Process -Id $PID -ErrorAction Stop).Path
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $powerShellPath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in @('-NoLogo', '-NoProfile', '-File', $scriptPath)) {
        [void]$startInfo.ArgumentList.Add($argument)
    }

    $process = [System.Diagnostics.Process]::Start($startInfo)
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        Output = ($stdout + $stderr).Trim()
    }
}

if (Test-Path -LiteralPath $script:StateFile -PathType Leaf) {
    throw "Lifecycle smoke requires no managed state: $($script:StateFile)"
}
$existingListeners = @(Get-NetTCPConnection -LocalAddress $script:BindAddress -LocalPort $script:ServerPort -State Listen -ErrorAction SilentlyContinue)
if ($existingListeners.Count -ne 0) {
    throw "Lifecycle smoke requires free loopback port $($script:ServerPort)."
}

$assertionCount = 0
$stoppedStatus = Invoke-LocalModelChildScript -ScriptName 'Status-Local-Model.ps1'
Assert-LifecycleCondition ($stoppedStatus.ExitCode -eq 1) 'stopped Status must exit 1'
$assertionCount++
Assert-LifecycleCondition ($stoppedStatus.Output -match 'State: stopped') 'stopped Status must say stopped'
$assertionCount++

$stoppedStop = Invoke-LocalModelChildScript -ScriptName 'Stop-Local-Model.ps1'
Assert-LifecycleCondition ($stoppedStop.ExitCode -eq 0) 'idempotent Stop must exit 0 when no listener exists'
$assertionCount++

$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $script:ServerPort)
try {
    $listener.Start()
    $connection = Get-NetTCPConnection -LocalAddress $script:BindAddress -LocalPort $script:ServerPort -State Listen -ErrorAction Stop | Select-Object -First 1
    $listenerOwner = [int]$connection.OwningProcess

    $unmanagedStatus = Invoke-LocalModelChildScript -ScriptName 'Status-Local-Model.ps1'
    Assert-LifecycleCondition ($unmanagedStatus.ExitCode -eq 3) 'identity-mismatched unmanaged Status must exit 3'
    $assertionCount++
    Assert-LifecycleCondition ($unmanagedStatus.Output -match 'State: unmanaged listener') 'unmanaged Status must not say stopped'
    $assertionCount++
    Assert-LifecycleCondition ($unmanagedStatus.Output -match "Listener PID: $listenerOwner") 'unmanaged Status must print listener owner PID'
    $assertionCount++
    Assert-LifecycleCondition ($unmanagedStatus.Output -match 'Identity: mismatch') 'unmanaged Status must print identity mismatch'
    $assertionCount++
    Assert-LifecycleCondition ($unmanagedStatus.Output -match 'Health: loading or unavailable') 'unmanaged Status must print loading/unavailable health'
    $assertionCount++

    $unmanagedStop = Invoke-LocalModelChildScript -ScriptName 'Stop-Local-Model.ps1'
    Assert-LifecycleCondition ($unmanagedStop.ExitCode -eq 3) 'identity-mismatched unmanaged Stop must fail closed with exit 3'
    $assertionCount++
    Assert-LifecycleCondition ($unmanagedStop.Output -match 'Refusing to stop unmanaged listener') 'unmanaged Stop must explicitly refuse'
    $assertionCount++
    Assert-LifecycleCondition ($unmanagedStop.Output -match "Listener PID: $listenerOwner") 'unmanaged Stop must print listener owner PID'
    $assertionCount++
    Assert-LifecycleCondition ($null -ne (Get-Process -Id $listenerOwner -ErrorAction SilentlyContinue)) 'unmanaged Stop must not kill listener owner'
    $assertionCount++
}
finally {
    $listener.Stop()
}

$multipleListenerSnapshot = Get-LocalModelListenerSnapshot -Connections @(
    [pscustomobject]@{ OwningProcess = $PID },
    [pscustomobject]@{ OwningProcess = $PID }
)
Assert-LifecycleCondition ($multipleListenerSnapshot.Kind -eq 'collision') 'multiple listener rows must be a collision'
$assertionCount++
Assert-LifecycleCondition ($multipleListenerSnapshot.CollisionReason -eq 'multiple-listeners') 'multiple listener collision reason must be explicit'
$assertionCount++
$collisionOutput = (
    Write-LocalModelUnmanagedListenerDiagnostics `
        -Snapshot $multipleListenerSnapshot `
        -Health ([pscustomobject]@{ Healthy = $false; StatusCode = $null }) 6>&1 |
        Out-String
)
Assert-LifecycleCondition ($collisionOutput -match 'State: collision') 'multiple listeners must render an explicit collision state'
$assertionCount++
Assert-LifecycleCondition ($collisionOutput -match 'Collision: multiple-listeners') 'collision output must render its reason'
$assertionCount++

$invalidOwnerSnapshot = Get-LocalModelListenerSnapshot -Connections @(
    [pscustomobject]@{ OwningProcess = 0 }
)
Assert-LifecycleCondition ($invalidOwnerSnapshot.Kind -eq 'collision') 'invalid listener owner must be a collision'
$assertionCount++
Assert-LifecycleCondition ($invalidOwnerSnapshot.CollisionReason -eq 'invalid-owner-pid') 'invalid owner collision reason must be explicit'
$assertionCount++

$remainingListeners = @(Get-NetTCPConnection -LocalAddress $script:BindAddress -LocalPort $script:ServerPort -State Listen -ErrorAction SilentlyContinue)
Assert-LifecycleCondition ($remainingListeners.Count -eq 0) 'focused smoke must release its listener'
$assertionCount++

Write-Host "Focused local-model lifecycle smoke passed ($assertionCount assertions)."
