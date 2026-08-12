Set-StrictMode -Version Latest

$script:ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$script:LocalRoot = Join-Path $script:ProjectRoot '.local'
$script:RuntimeRoot = Join-Path $script:LocalRoot 'runtime\llama-cpp'
$script:ServerExecutable = Join-Path $script:RuntimeRoot 'llama-server.exe'
$script:ModelPath = Join-Path $script:LocalRoot 'models\Qwen3-14B-Q5_K_M.gguf'
$script:LogRoot = Join-Path $script:LocalRoot 'logs'
$script:StateRoot = Join-Path $script:LocalRoot 'state'
$script:StateFile = Join-Path $script:StateRoot 'server.json'
$script:BindAddress = '127.0.0.1'
$script:ServerPort = 8012
$script:ModelAlias = 'qwen3-14b-local'
$script:HealthUrl = "http://$($script:BindAddress):$($script:ServerPort)/health"
$script:ModelsUrl = "http://$($script:BindAddress):$($script:ServerPort)/v1/models"
$script:ChatUrl = "http://$($script:BindAddress):$($script:ServerPort)/v1/chat/completions"
$script:ServerArguments = @(
    '--model', $script:ModelPath,
    '--host', $script:BindAddress,
    '--port', [string]$script:ServerPort,
    '--alias', $script:ModelAlias,
    '--parallel', '1',
    '--ctx-size', '32768',
    '--n-gpu-layers', '99',
    '--cache-type-k', 'q8_0',
    '--cache-type-v', 'q8_0',
    '--flash-attn', 'on',
    '--jinja'
)

function Initialize-LocalModelDirectories {
    foreach ($path in @($script:RuntimeRoot, (Split-Path -Parent $script:ModelPath), $script:LogRoot, $script:StateRoot)) {
        New-Item -ItemType Directory -Force -Path $path | Out-Null
    }
}

function ConvertTo-NativeArgument {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Argument)

    if ($Argument.Length -gt 0 -and $Argument -notmatch '[\s"]') {
        return $Argument
    }

    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Argument.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes++
            continue
        }

        if ($character -eq '"') {
            [void]$builder.Append(('\' * (($backslashes * 2) + 1)))
            [void]$builder.Append('"')
        }
        else {
            if ($backslashes -gt 0) {
                [void]$builder.Append(('\' * $backslashes))
            }
            [void]$builder.Append($character)
        }
        $backslashes = 0
    }

    if ($backslashes -gt 0) {
        [void]$builder.Append(('\' * ($backslashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Get-ServerArgumentString {
    return (($script:ServerArguments | ForEach-Object { ConvertTo-NativeArgument -Argument ([string]$_) }) -join ' ')
}

function New-LocalModelLogSet {
    Initialize-LocalModelDirectories
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    return [pscustomobject]@{
        Stdout = Join-Path $script:LogRoot "llama-server-$stamp.stdout.log"
        Stderr = Join-Path $script:LogRoot "llama-server-$stamp.stderr.log"
    }
}

function Write-LocalModelState {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][ValidateSet('background', 'foreground')][string]$Mode,
        [Parameter(Mandatory = $true)]$Logs
    )

    Initialize-LocalModelDirectories
    $state = [ordered]@{
        schemaVersion = 1
        pid = $Process.Id
        mode = $Mode
        startedAt = (Get-Date).ToUniversalTime().ToString('o')
        executablePath = $script:ServerExecutable
        modelPath = $script:ModelPath
        bindAddress = $script:BindAddress
        port = $script:ServerPort
        alias = $script:ModelAlias
        stdoutLog = $Logs.Stdout
        stderrLog = $Logs.Stderr
    }
    $temporaryPath = "$($script:StateFile).tmp.$PID"
    $state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $temporaryPath -Encoding utf8
    Move-Item -Force -LiteralPath $temporaryPath -Destination $script:StateFile
    return [pscustomobject]$state
}

function Get-LocalModelState {
    if (-not (Test-Path -LiteralPath $script:StateFile -PathType Leaf)) {
        return $null
    }

    try {
        return (Get-Content -Raw -LiteralPath $script:StateFile | ConvertFrom-Json)
    }
    catch {
        throw "Local model state file is unreadable: $($script:StateFile). $($_.Exception.Message)"
    }
}

function Remove-LocalModelState {
    param([Nullable[int]]$ExpectedProcessId = $null)

    if (-not (Test-Path -LiteralPath $script:StateFile -PathType Leaf)) {
        return
    }
    if ($null -ne $ExpectedProcessId) {
        $state = Get-LocalModelState
        if ($null -ne $state -and [int]$state.pid -ne [int]$ExpectedProcessId) {
            return
        }
    }
    Remove-Item -LiteralPath $script:StateFile -Force
}

function Get-LocalModelProcessInspection {
    param([Parameter(Mandatory = $true)][int]$TargetProcessId)

    # Win32_Process can briefly return a stale row after process termination.
    # Confirm the PID is live through the process table before trusting CIM for
    # the executable path and command-line identity signature.
    $managedProcess = Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue
    if ($null -eq $managedProcess) {
        return [pscustomobject]@{
            ProcessExists = $false
            Verified = $false
            ProcessId = $TargetProcessId
            ExecutableMatches = $false
            CommandLineMatches = $false
            Reason = 'process-not-found'
        }
    }

    $nativeProcess = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $TargetProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $nativeProcess) {
        return [pscustomobject]@{
            ProcessExists = $false
            Verified = $false
            ProcessId = $TargetProcessId
            ExecutableMatches = $false
            CommandLineMatches = $false
            Reason = 'process-not-found'
        }
    }

    $expectedExecutable = [System.IO.Path]::GetFullPath($script:ServerExecutable)
    $actualExecutable = if ([string]::IsNullOrWhiteSpace([string]$nativeProcess.ExecutablePath)) {
        ''
    }
    else {
        [System.IO.Path]::GetFullPath([string]$nativeProcess.ExecutablePath)
    }
    $executableMatches = [System.StringComparer]::OrdinalIgnoreCase.Equals($expectedExecutable, $actualExecutable)

    $commandLine = [string]$nativeProcess.CommandLine
    $hasModel = $commandLine.IndexOf($script:ModelPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    $hasAlias = $commandLine -match '(?i)(?:^|\s)--alias(?:=|\s+)["]?qwen3-14b-local["]?(?:\s|$)'
    $hasHost = $commandLine -match '(?i)(?:^|\s)--host(?:=|\s+)["]?127\.0\.0\.1["]?(?:\s|$)'
    $hasPort = $commandLine -match '(?i)(?:^|\s)--port(?:=|\s+)["]?8012["]?(?:\s|$)'
    $commandLineMatches = $hasModel -and $hasAlias -and $hasHost -and $hasPort

    $reason = if (-not $executableMatches) {
        'executable-path-mismatch'
    }
    elseif (-not $commandLineMatches) {
        'command-line-signature-mismatch'
    }
    else {
        'verified'
    }

    return [pscustomobject]@{
        ProcessExists = $true
        Verified = ($executableMatches -and $commandLineMatches)
        ProcessId = $TargetProcessId
        ExecutableMatches = $executableMatches
        CommandLineMatches = $commandLineMatches
        ActualExecutablePath = $actualExecutable
        Reason = $reason
    }
}

function Test-LocalModelPortInUse {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $asyncResult = $client.BeginConnect($script:BindAddress, $script:ServerPort, $null, $null)
        if (-not $asyncResult.AsyncWaitHandle.WaitOne(500)) {
            return $false
        }
        $client.EndConnect($asyncResult)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Get-LocalModelListenerSnapshot {
    [CmdletBinding()]
    param([object[]]$Connections)

    if (-not $PSBoundParameters.ContainsKey('Connections')) {
        $Connections = @(
            Get-NetTCPConnection `
                -LocalAddress $script:BindAddress `
                -LocalPort $script:ServerPort `
                -State Listen `
                -ErrorAction SilentlyContinue
        )
    }
    else {
        $Connections = @($Connections)
    }

    if ($Connections.Count -eq 0) {
        return [pscustomobject]@{
            Kind = 'none'
            ListenerCount = 0
            OwnerProcessIds = @()
            Inspections = @()
            CollisionReason = $null
        }
    }

    $ownerProcessIds = [System.Collections.Generic.List[int]]::new()
    $invalidOwner = $false
    foreach ($connection in $Connections) {
        $ownerProperty = $connection.PSObject.Properties['OwningProcess']
        $parsedOwner = 0
        if (
            $null -eq $ownerProperty -or
            $null -eq $ownerProperty.Value -or
            -not [int]::TryParse([string]$ownerProperty.Value, [ref]$parsedOwner) -or
            $parsedOwner -le 0
        ) {
            $invalidOwner = $true
            continue
        }
        $ownerProcessIds.Add($parsedOwner)
    }

    $uniqueOwnerProcessIds = @($ownerProcessIds | Sort-Object -Unique)
    $inspections = @(
        foreach ($ownerProcessId in $uniqueOwnerProcessIds) {
            Get-LocalModelProcessInspection -TargetProcessId $ownerProcessId
        }
    )

    $collisionReason = if ($Connections.Count -ne 1) {
        'multiple-listeners'
    }
    elseif ($invalidOwner) {
        'invalid-owner-pid'
    }
    elseif ($uniqueOwnerProcessIds.Count -ne 1) {
        'ambiguous-listener-owner'
    }
    elseif ($inspections.Count -ne 1 -or -not $inspections[0].ProcessExists) {
        'listener-owner-process-not-found'
    }
    else {
        $null
    }

    return [pscustomobject]@{
        Kind = if ($null -eq $collisionReason) { 'single' } else { 'collision' }
        ListenerCount = $Connections.Count
        OwnerProcessIds = $uniqueOwnerProcessIds
        Inspections = $inspections
        CollisionReason = $collisionReason
    }
}

function Write-LocalModelUnmanagedListenerDiagnostics {
    param(
        [Parameter(Mandatory = $true)]$Snapshot,
        [Parameter(Mandatory = $true)]$Health
    )

    if ($Snapshot.Kind -eq 'collision') {
        Write-Host 'State: collision (unmanaged listener set; no trusted state)'
        Write-Host "Listener count: $($Snapshot.ListenerCount)"
        Write-Host "Collision: $($Snapshot.CollisionReason)"
    }
    else {
        Write-Host 'State: unmanaged listener (no trusted state)'
    }

    if ($Snapshot.OwnerProcessIds.Count -eq 0) {
        Write-Host 'Listener PID: invalid or unavailable'
        Write-Host 'Identity: mismatch (listener owner is not trustworthy)'
    }
    else {
        foreach ($ownerProcessId in $Snapshot.OwnerProcessIds) {
            Write-Host "Listener PID: $ownerProcessId"
            $inspection = $Snapshot.Inspections | Where-Object { $_.ProcessId -eq $ownerProcessId } | Select-Object -First 1
            if ($null -ne $inspection -and $inspection.Verified) {
                Write-Host 'Identity: match (expected executable and launch signature)'
            }
            elseif ($null -ne $inspection) {
                Write-Host "Identity: mismatch ($($inspection.Reason))"
            }
            else {
                Write-Host 'Identity: mismatch (owner inspection unavailable)'
            }
        }
    }

    if ($Health.Healthy) {
        Write-Host "Health: healthy (HTTP $($Health.StatusCode))"
    }
    elseif ($null -ne $Health.StatusCode) {
        Write-Host "Health: loading or unavailable (HTTP $($Health.StatusCode))"
    }
    else {
        Write-Host 'Health: loading or unavailable'
    }
    Write-Host 'Management: no trusted state; listener was not adopted or stopped'
}

function Get-LocalModelHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $script:HealthUrl -Method Get -TimeoutSec 2
        return [pscustomobject]@{
            Healthy = ([int]$response.StatusCode -eq 200)
            StatusCode = [int]$response.StatusCode
            Body = [string]$response.Content
        }
    }
    catch {
        $statusCode = $null
        $responseProperty = $_.Exception.PSObject.Properties['Response']
        if ($null -ne $responseProperty -and $null -ne $responseProperty.Value) {
            $statusProperty = $responseProperty.Value.PSObject.Properties['StatusCode']
            if ($null -ne $statusProperty -and $null -ne $statusProperty.Value) {
                $statusCode = [int]$statusProperty.Value
            }
        }
        return [pscustomobject]@{
            Healthy = $false
            StatusCode = $statusCode
            Body = $_.Exception.Message
        }
    }
}

function Wait-LocalModelHealth {
    param(
        [Parameter(Mandatory = $true)][int]$TargetProcessId,
        [ValidateRange(1, 1800)][int]$TimeoutSeconds = 300
    )

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    while ($stopwatch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        $inspection = Get-LocalModelProcessInspection -TargetProcessId $TargetProcessId
        if (-not $inspection.ProcessExists) {
            throw "llama-server exited before becoming healthy (PID $TargetProcessId)."
        }
        if (-not $inspection.Verified) {
            throw "llama-server identity validation failed during startup: $($inspection.Reason)."
        }

        $health = Get-LocalModelHealth
        if ($health.Healthy) {
            return $health
        }
        Start-Sleep -Milliseconds 500
    }
    throw "llama-server did not become healthy within $TimeoutSeconds seconds."
}

function Stop-VerifiedLocalModelProcess {
    param([Parameter(Mandatory = $true)][int]$TargetProcessId)

    $inspection = Get-LocalModelProcessInspection -TargetProcessId $TargetProcessId
    if (-not $inspection.ProcessExists) {
        Remove-LocalModelState -ExpectedProcessId $TargetProcessId
        return [pscustomobject]@{ Stopped = $true; AlreadyExited = $true; ProcessId = $TargetProcessId }
    }
    if (-not $inspection.Verified) {
        throw "Refusing to stop PID $TargetProcessId because identity validation failed: $($inspection.Reason). Actual executable: $($inspection.ActualExecutablePath)"
    }

    Stop-Process -Id $TargetProcessId -ErrorAction Stop
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    do {
        $remaining = Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue
        if ($null -eq $remaining) {
            break
        }
        Start-Sleep -Milliseconds 200
    } while ($stopwatch.Elapsed.TotalSeconds -lt 20)

    if ($null -ne (Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue)) {
        throw "PID $TargetProcessId did not exit after Stop-Process. State was retained for safe manual diagnosis."
    }
    Remove-LocalModelState -ExpectedProcessId $TargetProcessId
    return [pscustomobject]@{ Stopped = $true; AlreadyExited = $false; ProcessId = $TargetProcessId }
}

function Assert-LocalModelInstallation {
    if (-not (Test-Path -LiteralPath $script:ServerExecutable -PathType Leaf)) {
        throw "llama-server is not installed at $($script:ServerExecutable). Run scripts\local-model\Install-Local-Model.ps1 first."
    }
    if (-not (Test-Path -LiteralPath $script:ModelPath -PathType Leaf)) {
        throw "Model is not installed at $($script:ModelPath). Run scripts\local-model\Install-Local-Model.ps1 first."
    }
}
