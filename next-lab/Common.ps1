Set-StrictMode -Version Latest

$script:ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$script:LabRoot = Join-Path $script:ProjectRoot '.local\next-lab'
$script:StateFile = Join-Path $script:LabRoot 'server.json'
$script:ServerScript = (Resolve-Path -LiteralPath (Join-Path $script:ProjectRoot 'next-lab\next_lab_server.py')).Path
$script:BindAddress = '127.0.0.1'
$script:LabPort = 7891
$script:LifecycleMutexName = 'Global\MemoWeftNextLabLifecycle-v1'

function Initialize-NextLabDirectories {
  New-Item -ItemType Directory -Force -Path $script:LabRoot | Out-Null
}

function Enter-NextLabLifecycleMutex([int]$TimeoutMilliseconds = 30000) {
  $mutex = [Threading.Mutex]::new($false, $script:LifecycleMutexName)
  try {
    try { $acquired = $mutex.WaitOne($TimeoutMilliseconds) } catch [Threading.AbandonedMutexException] { $acquired = $true }
    if (-not $acquired) { throw 'Timed out waiting for the Next Lab lifecycle lock.' }
    return $mutex
  } catch {
    $mutex.Dispose()
    throw
  }
}

function Exit-NextLabLifecycleMutex($Mutex) {
  if ($null -eq $Mutex) { return }
  try { $Mutex.ReleaseMutex() } catch [ApplicationException] { } finally { $Mutex.Dispose() }
}

function Get-NextLabState {
  if (-not (Test-Path -LiteralPath $script:StateFile -PathType Leaf)) { return $null }
  return (Get-Content -Raw -LiteralPath $script:StateFile -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop)
}

function Get-NextLabStateProperty($State, [string]$Name) {
  if ($null -eq $State) { return $null }
  if ($State -is [Collections.IDictionary]) {
    if (-not $State.Contains($Name)) { return $null }
    return $State[$Name]
  }
  $property = $State.PSObject.Properties[$Name]
  if ($null -eq $property) { return $null }
  return $property.Value
}

function Get-NextLabFullPath([string]$Path) {
  if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
  try { return [IO.Path]::GetFullPath($Path) } catch { return '' }
}

function Test-NextLabPathEqual([string]$Actual, [string]$Expected) {
  return [StringComparer]::OrdinalIgnoreCase.Equals((Get-NextLabFullPath $Actual), (Get-NextLabFullPath $Expected))
}

function ConvertTo-NextLabUtc($Value) {
  if ($Value -is [datetime]) { return $Value.ToUniversalTime() }
  $text = [string]$Value
  if ([string]::IsNullOrWhiteSpace($text)) { throw 'Missing process creation time.' }
  if ($text -match '^\d{14}\.\d{6}[+-]\d{3}$') {
    return [Management.ManagementDateTimeConverter]::ToDateTime($text).ToUniversalTime()
  }
  return [DateTimeOffset]::Parse($text, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeUniversal).UtcDateTime
}

function Test-NextLabStateSchema($State) {
  $required = @('schemaVersion','pid','pythonPath','processCreationTime','serverScript','bindAddress','port','stateDir','instanceToken','fixedArgs','logs')
  foreach ($name in $required) {
    if ($null -eq (Get-NextLabStateProperty $State $name)) { return [pscustomobject]@{ Valid=$false; Reason="state-missing-$name" } }
  }
  try { $stateProcessId = [int](Get-NextLabStateProperty $State 'pid') } catch { return [pscustomobject]@{ Valid=$false; Reason='state-invalid-pid' } }
  if ($stateProcessId -le 0) { return [pscustomobject]@{ Valid=$false; Reason='state-invalid-pid' } }
  try {
    $schemaVersion = [int](Get-NextLabStateProperty $State 'schemaVersion')
    $statePort = [int](Get-NextLabStateProperty $State 'port')
  } catch { return [pscustomobject]@{ Valid=$false; Reason='state-invalid-schema-or-port' } }
  if ($schemaVersion -ne 3) { return [pscustomobject]@{ Valid=$false; Reason='state-schema-version-mismatch' } }
  if (-not (Test-NextLabPathEqual ([string](Get-NextLabStateProperty $State 'serverScript')) $script:ServerScript)) { return [pscustomobject]@{ Valid=$false; Reason='state-server-script-mismatch' } }
  if (-not (Test-NextLabPathEqual ([string](Get-NextLabStateProperty $State 'stateDir')) $script:LabRoot)) { return [pscustomobject]@{ Valid=$false; Reason='state-directory-mismatch' } }
  if ([string](Get-NextLabStateProperty $State 'bindAddress') -ne $script:BindAddress -or $statePort -ne $script:LabPort) { return [pscustomobject]@{ Valid=$false; Reason='state-bind-or-port-mismatch' } }
  if ([string]::IsNullOrWhiteSpace([string](Get-NextLabStateProperty $State 'pythonPath')) -or [string]::IsNullOrWhiteSpace([string](Get-NextLabStateProperty $State 'instanceToken'))) { return [pscustomobject]@{ Valid=$false; Reason='state-missing-process-identity' } }
  try { [void](ConvertTo-NextLabUtc (Get-NextLabStateProperty $State 'processCreationTime')) } catch { return [pscustomobject]@{ Valid=$false; Reason='state-invalid-process-creation-time' } }
  $logs = Get-NextLabStateProperty $State 'logs'
  if ($null -eq (Get-NextLabStateProperty $logs 'stdout') -or $null -eq (Get-NextLabStateProperty $logs 'stderr')) { return [pscustomobject]@{ Valid=$false; Reason='state-invalid-logs' } }
  $expectedArgs = @($script:ServerScript,'--host',$script:BindAddress,'--port',[string]$script:LabPort,'--state-dir',$script:LabRoot,'--instance-token',[string](Get-NextLabStateProperty $State 'instanceToken'))
  $fixedArgs = @(Get-NextLabStateProperty $State 'fixedArgs')
  if ($fixedArgs.Count -ne $expectedArgs.Count) { return [pscustomobject]@{ Valid=$false; Reason='state-fixed-args-mismatch' } }
  for ($index = 0; $index -lt $expectedArgs.Count; $index++) {
    $equal = if ($index -eq 0 -or $index -eq 6) { Test-NextLabPathEqual ([string]$fixedArgs[$index]) $expectedArgs[$index] } else { [string]$fixedArgs[$index] -ceq [string]$expectedArgs[$index] }
    if (-not $equal) { return [pscustomobject]@{ Valid=$false; Reason='state-fixed-args-mismatch' } }
  }
  return [pscustomobject]@{ Valid=$true; Reason='state-schema-verified'; ProcessId=$stateProcessId; ExpectedArgs=$expectedArgs }
}

function ConvertFrom-NextLabCommandLine([string]$CommandLine) {
  if ([string]::IsNullOrWhiteSpace($CommandLine)) { return @() }
  if ($null -eq ('MemoWeftNextLab.CommandLineNative' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace MemoWeftNextLab {
  public static class CommandLineNative {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)] public static extern IntPtr CommandLineToArgvW(string commandLine, out int argc);
    [DllImport("kernel32.dll")] public static extern IntPtr LocalFree(IntPtr hMem);
  }
}
'@
  }
  $count = 0
  $pointer = [MemoWeftNextLab.CommandLineNative]::CommandLineToArgvW($CommandLine, [ref]$count)
  if ($pointer -eq [IntPtr]::Zero) { throw 'Unable to parse the process command line.' }
  try {
    $arguments = @()
    for ($index = 0; $index -lt $count; $index++) {
      $argumentPointer = [Runtime.InteropServices.Marshal]::ReadIntPtr($pointer, $index * [IntPtr]::Size)
      $arguments += [Runtime.InteropServices.Marshal]::PtrToStringUni($argumentPointer)
    }
    return $arguments
  } finally {
    [void][MemoWeftNextLab.CommandLineNative]::LocalFree($pointer)
  }
}

function Get-NextLabInspection($State) {
  $schema = Test-NextLabStateSchema $State
  if (-not $schema.Valid) { return [pscustomobject]@{ Exists=$false; Verified=$false; Reason=$schema.Reason; StateValid=$false } }
  $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($schema.ProcessId)" -ErrorAction SilentlyContinue
  if ($null -eq $process) { return [pscustomobject]@{ Exists=$false; Verified=$false; Reason='process-not-found'; StateValid=$true } }
  $actualPath = Get-NextLabFullPath ([string]$process.ExecutablePath)
  $pathMatches = Test-NextLabPathEqual $actualPath ([string](Get-NextLabStateProperty $State 'pythonPath'))
  try {
    $expectedStarted = ConvertTo-NextLabUtc (Get-NextLabStateProperty $State 'processCreationTime')
    $actualStarted = ConvertTo-NextLabUtc $process.CreationDate
    $creationMatches = [Math]::Abs(($actualStarted - $expectedStarted).TotalSeconds) -le 2
  } catch { $creationMatches = $false }
  try { $actualArgs = @(ConvertFrom-NextLabCommandLine ([string]$process.CommandLine)) } catch { $actualArgs = @() }
  $expectedArgs = @($schema.ExpectedArgs)
  $commandMatches = $actualArgs.Count -eq ($expectedArgs.Count + 1)
  if ($commandMatches) {
    for ($index = 0; $index -lt $expectedArgs.Count; $index++) {
      $actual = [string]$actualArgs[$index + 1]
      $expected = [string]$expectedArgs[$index]
      $equal = if ($index -eq 0 -or $index -eq 6) { Test-NextLabPathEqual $actual $expected } else { $actual -ceq $expected }
      if (-not $equal) { $commandMatches = $false; break }
    }
  }
  $reason = if (-not $pathMatches) { 'executable-path-mismatch' } elseif (-not $creationMatches) { 'process-creation-time-mismatch' } elseif (-not $commandMatches) { 'command-argument-vector-mismatch' } else { 'process-identity-verified' }
  return [pscustomobject]@{ Exists=$true; Verified=($pathMatches -and $creationMatches -and $commandMatches); Reason=$reason; StateValid=$true; ProcessId=$schema.ProcessId; ExecutableMatches=$pathMatches; CreationMatches=$creationMatches; CommandLineMatches=$commandMatches }
}

function Get-NextLabListeners {
  return @(Get-NetTCPConnection -LocalPort $script:LabPort -State Listen -ErrorAction SilentlyContinue)
}

function Get-NextLabTransportInspection($State) {
  $process = Get-NextLabInspection $State
  if (-not $process.Verified) { return [pscustomobject]@{ Exists=$process.Exists; Verified=$false; Reason=$process.Reason; Process=$process; Listener=$null } }
  $listeners = @(Get-NextLabListeners)
  if ($listeners.Count -ne 1) { return [pscustomobject]@{ Exists=$true; Verified=$false; Reason='listener-count-mismatch'; Process=$process; Listener=$null } }
  $listener = $listeners[0]
  if ($listener.LocalAddress -ne $script:BindAddress -or [int]$listener.LocalPort -ne $script:LabPort -or [int]$listener.OwningProcess -ne [int]$process.ProcessId) {
    return [pscustomobject]@{ Exists=$true; Verified=$false; Reason='listener-identity-mismatch'; Process=$process; Listener=$listener }
  }
  return [pscustomobject]@{ Exists=$true; Verified=$true; Reason='managed-transport-verified'; Process=$process; Listener=$listener }
}

function Get-NextLabListener {
  $listeners = @(Get-NextLabListeners | Where-Object { $_.LocalAddress -eq $script:BindAddress })
  if ($listeners.Count -eq 1) { return $listeners[0] }
  return $null
}

function Test-NextLabHttpIdentity($State) {
  try {
    $response = Invoke-RestMethod -Uri "http://$($script:BindAddress):$($script:LabPort)/api/status" -TimeoutSec 2 -ErrorAction Stop
    if ($response.serverIdentity -ne 'MemoWeftNextLab/1') { return [pscustomobject]@{ Verified=$false; Reason='http-server-identity-mismatch' } }
    if ($response.instanceToken -ne (Get-NextLabStateProperty $State 'instanceToken')) { return [pscustomobject]@{ Verified=$false; Reason='http-instance-token-mismatch' } }
    return [pscustomobject]@{ Verified=$true; Reason='http-identity-verified' }
  } catch { return [pscustomobject]@{ Verified=$false; Reason='http-status-unavailable' } }
}

function Get-NextLabFullInspection($State) {
  $transport = Get-NextLabTransportInspection $State
  if (-not $transport.Verified) { return [pscustomobject]@{ Exists=$transport.Exists; Verified=$false; Reason=$transport.Reason; Process=$transport.Process; Listener=$transport.Listener; Http=$null } }
  $http = Test-NextLabHttpIdentity $State
  if (-not $http.Verified) { return [pscustomobject]@{ Exists=$true; Verified=$false; Reason=$http.Reason; Process=$transport.Process; Listener=$transport.Listener; Http=$http } }
  return [pscustomobject]@{ Exists=$true; Verified=$true; Reason='managed-lifecycle-verified'; Process=$transport.Process; Listener=$transport.Listener; Http=$http }
}

function Write-NextLabState([int]$ProcessId, $Logs, [string]$InstanceToken) {
  Initialize-NextLabDirectories
  $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
  if ($null -eq $process -or [string]::IsNullOrWhiteSpace([string]$process.ExecutablePath)) { throw "Cannot record identity for Next Lab PID $ProcessId." }
  $pythonPath = Get-NextLabFullPath ([string]$process.ExecutablePath)
  $creationTime = (ConvertTo-NextLabUtc $process.CreationDate).ToString('o')
  $state = [ordered]@{
    schemaVersion = 3
    pid = $ProcessId
    pythonPath = $pythonPath
    processCreationTime = $creationTime
    serverScript = $script:ServerScript
    bindAddress = $script:BindAddress
    port = $script:LabPort
    stateDir = $script:LabRoot
    instanceToken = $InstanceToken
    fixedArgs = @($script:ServerScript,'--host',$script:BindAddress,'--port',[string]$script:LabPort,'--state-dir',$script:LabRoot,'--instance-token',$InstanceToken)
    logs = [ordered]@{ stdout = [string](Get-NextLabStateProperty $Logs 'stdout'); stderr = [string](Get-NextLabStateProperty $Logs 'stderr') }
  }
  $tmp = "$script:StateFile.tmp.$([guid]::NewGuid().ToString('N'))"
  $backup = "$script:StateFile.backup.$([guid]::NewGuid().ToString('N'))"
  try {
    $state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $tmp -Encoding utf8 -NoNewline -ErrorAction Stop
    if (Test-Path -LiteralPath $script:StateFile -PathType Leaf) {
      # File.Replace is atomic on the local NTFS volume and is available in .NET Framework 4.8.
      [IO.File]::Replace($tmp, $script:StateFile, $backup)
    } else {
      # The two-argument overload is supported by both Windows PowerShell 5.1 and pwsh.
      [IO.File]::Move($tmp, $script:StateFile)
    }
  } finally {
    if (Test-Path -LiteralPath $tmp -PathType Leaf) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $backup -PathType Leaf) { Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue }
  }
  return [pscustomobject]$state
}

function Remove-NextLabState([int]$ExpectedPid) {
  if (-not (Test-Path -LiteralPath $script:StateFile -PathType Leaf)) { return }
  $state = Get-NextLabState
  if ($null -eq $state -or [int](Get-NextLabStateProperty $state 'pid') -eq $ExpectedPid) {
    Remove-Item -LiteralPath $script:StateFile -Force -ErrorAction Stop
  }
}

function Test-NextLabProcessOwnedBy([int]$CandidateProcessId, [int]$RootProcessId) {
  $seen = [Collections.Generic.HashSet[int]]::new()
  $current = $CandidateProcessId
  while ($current -gt 0 -and $seen.Add($current)) {
    if ($current -eq $RootProcessId) { return $true }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $current" -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $false }
    $current = [int]$process.ParentProcessId
  }
  return $false
}

function Stop-NextLabOwnedLaunchProcess([int]$RootProcessId) {
  $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
  $owned = [Collections.Generic.List[int]]::new()
  $owned.Add($RootProcessId)
  for ($index = 0; $index -lt $owned.Count; $index++) {
    $parent = $owned[$index]
    foreach ($child in $all | Where-Object { [int]$_.ParentProcessId -eq $parent }) { $owned.Add([int]$child.ProcessId) }
  }
  for ($index = $owned.Count - 1; $index -ge 0; $index--) {
    $process = Get-Process -Id $owned[$index] -ErrorAction SilentlyContinue
    if ($null -ne $process) { try { Stop-Process -InputObject $process -ErrorAction Stop } catch { } }
  }
}
