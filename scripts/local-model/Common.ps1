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
$script:StateSchemaVersion = 2
$script:LifecycleMutexName = 'Local\MemoWeft-LocalModel-8012-Lifecycle-v2'
$script:LegacyStartTimeToleranceTicks = [TimeSpan]::FromSeconds(10).Ticks
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

function Enter-LocalModelLifecycleLock {
    param([ValidateRange(1, 300)][int]$TimeoutSeconds = 30)

    $mutex = [Threading.Mutex]::new($false, $script:LifecycleMutexName)
    $taken = $false
    try {
        try {
            $taken = $mutex.WaitOne([TimeSpan]::FromSeconds($TimeoutSeconds))
        }
        catch [Threading.AbandonedMutexException] {
            # WaitOne grants ownership when it reports an abandoned mutex.
            $taken = $true
        }
        if (-not $taken) {
            throw "Timed out waiting for the local-model lifecycle lock after $TimeoutSeconds seconds."
        }
        return [pscustomobject]@{ Mutex = $mutex; Taken = $true }
    }
    catch {
        if (-not $taken) { $mutex.Dispose() }
        throw
    }
}

function Exit-LocalModelLifecycleLock {
    param([Parameter(Mandatory = $true)]$Lock)
    try {
        if ($Lock.Taken) { $Lock.Mutex.ReleaseMutex() }
    }
    finally {
        $Lock.Mutex.Dispose()
    }
}

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

function Initialize-LocalModelCommandLineParser {
    if ($null -ne ('MemoWeft.LocalModel.NativeCommandLine' -as [type])) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace MemoWeft.LocalModel {
  public static class NativeCommandLine {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CommandLineToArgvW(string commandLine, out int argc);
    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr memory);

    public static string[] Parse(string commandLine) {
      if (String.IsNullOrEmpty(commandLine)) return new string[0];
      int argc;
      IntPtr argv = CommandLineToArgvW(commandLine, out argc);
      if (argv == IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
      try {
        var result = new string[argc];
        for (int index = 0; index < argc; index++) {
          IntPtr item = Marshal.ReadIntPtr(argv, index * IntPtr.Size);
          result[index] = Marshal.PtrToStringUni(item);
        }
        return result;
      } finally {
        LocalFree(argv);
      }
    }
  }
}
'@
}

function Get-LocalModelCommandLineArguments {
    param([Parameter(Mandatory = $true)][string]$CommandLine)
    Initialize-LocalModelCommandLineParser
    return @([MemoWeft.LocalModel.NativeCommandLine]::Parse($CommandLine))
}

function Test-LocalModelStringArrayEqual {
    param(
        [Parameter(Mandatory = $true)][string[]]$Left,
        [Parameter(Mandatory = $true)][string[]]$Right
    )
    if ($Left.Count -ne $Right.Count) { return $false }
    for ($index = 0; $index -lt $Left.Count; $index++) {
        if (-not [string]::Equals($Left[$index], $Right[$index], [StringComparison]::Ordinal)) {
            return $false
        }
    }
    return $true
}

function Get-LocalModelProcessStartTicks {
    param([Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process)
    return $Process.StartTime.ToUniversalTime().Ticks.ToString([Globalization.CultureInfo]::InvariantCulture)
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
        schemaVersion = $script:StateSchemaVersion
        pid = [int]$Process.Id
        processStartTicks = Get-LocalModelProcessStartTicks -Process $Process
        mode = $Mode
        executablePath = $script:ServerExecutable
        modelPath = $script:ModelPath
        bindAddress = $script:BindAddress
        port = $script:ServerPort
        alias = $script:ModelAlias
        argv = @($script:ServerArguments)
        stdoutLog = $Logs.Stdout
        stderrLog = $Logs.Stderr
    }
    $temporaryPath = "$($script:StateFile).$([guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText(
        $temporaryPath,
        ($state | ConvertTo-Json -Depth 5),
        [Text.UTF8Encoding]::new($false)
    )
    if (Test-Path -LiteralPath $script:StateFile -PathType Leaf) {
        $backupPath = "$($script:StateFile).$([guid]::NewGuid().ToString('N')).bak"
        [IO.File]::Replace($temporaryPath, $script:StateFile, $backupPath)
        Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
    }
    else {
        [IO.File]::Move($temporaryPath, $script:StateFile)
    }
    return [pscustomobject]$state
}

function Test-LocalModelStateShape {
    param([Parameter(Mandatory = $true)]$State)

    $schemaProperty = $State.PSObject.Properties['schemaVersion']
    if ($null -eq $schemaProperty -or $schemaProperty.Value -isnot [ValueType] -or [string]$schemaProperty.Value -notmatch '^\d+$') {
        return 'invalid schemaVersion type'
    }
    $schemaVersion = [int64]$schemaProperty.Value
    $required = if ($schemaVersion -eq 1) {
        @('schemaVersion','pid','mode','startedAt','executablePath','modelPath','bindAddress','port','alias','stdoutLog','stderrLog')
    }
    elseif ($schemaVersion -eq $script:StateSchemaVersion) {
        @('schemaVersion','pid','processStartTicks','mode','executablePath','modelPath','bindAddress','port','alias','argv','stdoutLog','stderrLog')
    }
    else {
        return 'unsupported schema version'
    }

    $properties = @($State.PSObject.Properties | ForEach-Object { $_.Name })
    if ($properties.Count -ne $required.Count -or @($properties | Where-Object { $required -notcontains $_ }).Count -ne 0) {
        return 'unexpected state fields'
    }
    foreach ($name in @('schemaVersion','pid','port')) {
        if ($State.$name -isnot [ValueType] -or [string]$State.$name -notmatch '^\d+$') {
            return "invalid $name type"
        }
    }
    foreach ($name in @('mode','executablePath','modelPath','bindAddress','alias','stdoutLog','stderrLog')) {
        if ($State.$name -isnot [string] -or [string]::IsNullOrWhiteSpace([string]$State.$name)) {
            return "invalid $name type"
        }
    }
    if ([int64]$State.pid -le 0 -or [int64]$State.port -ne $script:ServerPort) {
        return 'invalid state numeric value'
    }
    if (@('background','foreground') -notcontains [string]$State.mode) { return 'invalid mode' }
    if (-not [string]::Equals([IO.Path]::GetFullPath([string]$State.executablePath), [IO.Path]::GetFullPath($script:ServerExecutable), [StringComparison]::OrdinalIgnoreCase)) { return 'executable path mismatch' }
    if (-not [string]::Equals([IO.Path]::GetFullPath([string]$State.modelPath), [IO.Path]::GetFullPath($script:ModelPath), [StringComparison]::OrdinalIgnoreCase)) { return 'model path mismatch' }
    if ($State.bindAddress -ne $script:BindAddress -or $State.alias -ne $script:ModelAlias) { return 'fixed service field mismatch' }

    if ($schemaVersion -eq 1) {
        if ($State.startedAt -isnot [string] -and $State.startedAt -isnot [DateTime]) { return 'invalid startedAt type' }
        if ($State.startedAt -is [string]) {
            if ([string]::IsNullOrWhiteSpace([string]$State.startedAt)) { return 'invalid startedAt type' }
            $legacyTimestamp = [DateTimeOffset]::MinValue
            if (-not [DateTimeOffset]::TryParse([string]$State.startedAt, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind, [ref]$legacyTimestamp)) { return 'invalid startedAt value' }
        }
        return $null
    }

    if ($State.processStartTicks -isnot [string] -or $State.processStartTicks -notmatch '^\d+$') { return 'invalid process start ticks' }
    if ($State.argv -isnot [System.Array] -or @($State.argv | Where-Object { $_ -isnot [string] }).Count -ne 0) { return 'invalid argv type' }
    if (-not (Test-LocalModelStringArrayEqual -Left @($State.argv) -Right @($script:ServerArguments))) { return 'argv mismatch' }
    return $null
}

function Get-LegacyLocalModelStartTimestamp {
    param([Parameter(Mandatory = $true)]$Value)
    if ($Value -is [DateTime]) {
        return [DateTimeOffset]::new(([DateTime]$Value).ToUniversalTime())
    }
    return [DateTimeOffset]::Parse(
        [string]$Value,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind
    )
}

function ConvertFrom-LegacyLocalModelState {
    param([Parameter(Mandatory = $true)]$State)

    $legacyTimestamp = Get-LegacyLocalModelStartTimestamp -Value $State.startedAt
    $process = Get-Process -Id ([int]$State.pid) -ErrorAction SilentlyContinue
    $processStartTicks = $legacyTimestamp.UtcDateTime.Ticks
    if ($null -ne $process) {
        $actualTicks = [int64](Get-LocalModelProcessStartTicks -Process $process)
        if ([Math]::Abs($actualTicks - $processStartTicks) -gt $script:LegacyStartTimeToleranceTicks) {
            throw 'Legacy local-model state does not match the live process creation time; refusing to trust the reused PID.'
        }
        $processStartTicks = $actualTicks
    }

    return [pscustomobject][ordered]@{
        schemaVersion = $script:StateSchemaVersion
        pid = [int]$State.pid
        processStartTicks = $processStartTicks.ToString([Globalization.CultureInfo]::InvariantCulture)
        mode = [string]$State.mode
        executablePath = [string]$State.executablePath
        modelPath = [string]$State.modelPath
        bindAddress = [string]$State.bindAddress
        port = [int]$State.port
        alias = [string]$State.alias
        argv = @($script:ServerArguments)
        stdoutLog = [string]$State.stdoutLog
        stderrLog = [string]$State.stderrLog
    }
}

function Get-RawLocalModelState {
    if (-not (Test-Path -LiteralPath $script:StateFile -PathType Leaf)) { return $null }
    try {
        return (Get-Content -Raw -LiteralPath $script:StateFile | ConvertFrom-Json)
    }
    catch {
        throw "Local model state file is unreadable: $($script:StateFile). $($_.Exception.Message)"
    }
}

function Get-LocalModelState {
    $state = Get-RawLocalModelState
    if ($null -eq $state) { return $null }
    $shapeError = Test-LocalModelStateShape -State $state
    if ($null -ne $shapeError) { throw "Local model state is not trustworthy ($shapeError): $($script:StateFile)" }
    if ([int64]$state.schemaVersion -eq 1) { return ConvertFrom-LegacyLocalModelState -State $state }
    return $state
}

function Remove-LocalModelState {
    param(
        [Nullable[int]]$ExpectedProcessId = $null,
        [string]$ExpectedProcessStartTicks
    )

    if (-not (Test-Path -LiteralPath $script:StateFile -PathType Leaf)) {
        return
    }
    $rawState = Get-RawLocalModelState
    $shapeError = Test-LocalModelStateShape -State $rawState
    if ($null -ne $shapeError) { throw "Refusing to remove untrusted local-model state ($shapeError)." }
    if ($null -ne $ExpectedProcessId -and [int]$rawState.pid -ne [int]$ExpectedProcessId) { return }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedProcessStartTicks)) {
        if ([int64]$rawState.schemaVersion -eq $script:StateSchemaVersion) {
            if ([string]$rawState.processStartTicks -ne $ExpectedProcessStartTicks) { return }
        }
        else {
            $legacyTimestamp = Get-LegacyLocalModelStartTimestamp -Value $rawState.startedAt
            if ([Math]::Abs([int64]$ExpectedProcessStartTicks - $legacyTimestamp.UtcDateTime.Ticks) -gt $script:LegacyStartTimeToleranceTicks) { return }
        }
    }
    Remove-Item -LiteralPath $script:StateFile -Force
}

function Get-LocalModelProcessInspection {
    param(
        [Parameter(Mandatory = $true)][int]$TargetProcessId,
        [string]$ExpectedProcessStartTicks
    )

    # Win32_Process can briefly return a stale row after process termination.
    # Confirm the PID is live through the process table before trusting CIM for
    # the executable path and command-line identity signature.
    $managedProcess = Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue
    if ($null -eq $managedProcess) {
        return [pscustomobject]@{
            ProcessExists = $false
            Verified = $false
            ProcessId = $TargetProcessId
            Process = $null
            ProcessStartTicks = $null
            CreationTimeMatches = $false
            ExecutableMatches = $false
            CommandLineMatches = $false
            Reason = 'process-not-found'
        }
    }

    $actualProcessStartTicks = Get-LocalModelProcessStartTicks -Process $managedProcess
    $creationTimeMatches = (
        [string]::IsNullOrWhiteSpace($ExpectedProcessStartTicks) -or
        $actualProcessStartTicks -eq $ExpectedProcessStartTicks
    )
    if (-not $creationTimeMatches) {
        return [pscustomobject]@{
            ProcessExists = $true
            Verified = $false
            ProcessId = $TargetProcessId
            Process = $managedProcess
            ProcessStartTicks = $actualProcessStartTicks
            CreationTimeMatches = $false
            ExecutableMatches = $false
            CommandLineMatches = $false
            ActualExecutablePath = $managedProcess.Path
            Reason = 'process-creation-time-mismatch'
        }
    }

    $nativeProcess = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $TargetProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $nativeProcess) {
        return [pscustomobject]@{
            ProcessExists = $true
            Verified = $false
            ProcessId = $TargetProcessId
            Process = $managedProcess
            ProcessStartTicks = $actualProcessStartTicks
            CreationTimeMatches = $true
            ExecutableMatches = $false
            CommandLineMatches = $false
            ActualExecutablePath = $managedProcess.Path
            Reason = 'process-metadata-unavailable'
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
    try {
        $actualCommand = Get-LocalModelCommandLineArguments -CommandLine $commandLine
        $commandLineMatches = (
            $actualCommand.Count -ge 1 -and
            (Test-LocalModelStringArrayEqual -Left @($actualCommand | Select-Object -Skip 1) -Right @($script:ServerArguments))
        )
    }
    catch {
        $commandLineMatches = $false
    }

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
        Verified = ($creationTimeMatches -and $executableMatches -and $commandLineMatches)
        ProcessId = $TargetProcessId
        Process = $managedProcess
        ProcessStartTicks = $actualProcessStartTicks
        CreationTimeMatches = $creationTimeMatches
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
        [Parameter(Mandatory = $true)][string]$ExpectedProcessStartTicks,
        [ValidateRange(1, 1800)][int]$TimeoutSeconds = 300
    )

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    while ($stopwatch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        $inspection = Get-LocalModelProcessInspection -TargetProcessId $TargetProcessId -ExpectedProcessStartTicks $ExpectedProcessStartTicks
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
    param([Parameter(Mandatory = $true)]$State)

    $TargetProcessId = [int]$State.pid
    $expectedProcessStartTicks = [string]$State.processStartTicks

    $inspection = Get-LocalModelProcessInspection -TargetProcessId $TargetProcessId -ExpectedProcessStartTicks $expectedProcessStartTicks
    if (-not $inspection.ProcessExists) {
        Remove-LocalModelState -ExpectedProcessId $TargetProcessId -ExpectedProcessStartTicks $expectedProcessStartTicks
        return [pscustomobject]@{ Stopped = $true; AlreadyExited = $true; ProcessId = $TargetProcessId }
    }
    if (-not $inspection.Verified) {
        throw "Refusing to stop PID $TargetProcessId because identity validation failed: $($inspection.Reason). Actual executable: $($inspection.ActualExecutablePath)"
    }

    $listener = Get-LocalModelListenerSnapshot
    if ($listener.Kind -eq 'collision') {
        throw "Refusing to stop PID $TargetProcessId because the loopback listener set is ambiguous."
    }
    if (
        $listener.Kind -eq 'single' -and
        ($listener.OwnerProcessIds.Count -ne 1 -or [int]$listener.OwnerProcessIds[0] -ne $TargetProcessId)
    ) {
        throw "Refusing to stop PID $TargetProcessId because the loopback listener ownership is not exact."
    }

    # Re-read the process identity immediately before the destructive action.
    $actionInspection = Get-LocalModelProcessInspection -TargetProcessId $TargetProcessId -ExpectedProcessStartTicks $expectedProcessStartTicks
    if (-not $actionInspection.Verified) {
        throw "Refusing to stop PID $TargetProcessId because action-time identity validation failed: $($actionInspection.Reason)."
    }
    Stop-Process -InputObject $actionInspection.Process -ErrorAction Stop
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
    Remove-LocalModelState -ExpectedProcessId $TargetProcessId -ExpectedProcessStartTicks $expectedProcessStartTicks
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
