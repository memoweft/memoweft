[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$script:WorkbenchRoot = Join-Path $script:ProjectRoot '.local\workbench'
$script:StateFile = Join-Path $script:WorkbenchRoot 'server.json'
$script:ServerScript = Join-Path $script:ProjectRoot 'testbench\server.mjs'
$script:NextLabStateFile = Join-Path $script:ProjectRoot '.local\next-lab\server.json'
$script:BindAddress = '127.0.0.1'
$script:WorkbenchPort = 7888
$script:StateSchemaVersion = 2
$script:WorkbenchIdentityKind = 'memoweft-workbench'
$script:MinimumWorkbenchNodeMajor = 24

function Initialize-WorkbenchDirectories {
  New-Item -ItemType Directory -Force -Path $script:WorkbenchRoot | Out-Null
}

function Get-WorkbenchListeners {
  return @(Get-NetTCPConnection -State Listen -LocalPort $script:WorkbenchPort -ErrorAction SilentlyContinue)
}

function Get-CurrentWorkbenchNodeExecutable {
  return [IO.Path]::GetFullPath((Get-Command node.exe -ErrorAction Stop).Source)
}

function Get-ValidatedWorkbenchNodeExecutable {
  try {
    $node = Get-CurrentWorkbenchNodeExecutable
  } catch {
    throw "MemoWeft workbench requires Node.js $script:MinimumWorkbenchNodeMajor or newer, but node.exe was not found on PATH. $($_.Exception.Message)"
  }

  $versionOutput = @(& $node --version 2>&1)
  $exitCode = $LASTEXITCODE
  $versionText = (($versionOutput | ForEach-Object { [string]$_ }) -join "`n").Trim()
  if ($exitCode -ne 0) {
    throw "Unable to validate the Node.js $script:MinimumWorkbenchNodeMajor+ requirement: '$node --version' exited with code $exitCode."
  }

  $match = [regex]::Match($versionText, '^v(?<major>\d+)\.\d+\.\d+(?:[-+].*)?$')
  if (-not $match.Success) {
    throw "Unable to validate the Node.js $script:MinimumWorkbenchNodeMajor+ requirement: '$node --version' returned '$versionText'."
  }
  if ([int64]$match.Groups['major'].Value -lt $script:MinimumWorkbenchNodeMajor) {
    throw "MemoWeft workbench requires Node.js $script:MinimumWorkbenchNodeMajor or newer because its source testbench imports .ts files directly and uses node:sqlite. Found $versionText at $node."
  }

  return $node
}

function Get-WorkbenchProcessStartTicks {
  param([Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process)
  return $Process.StartTime.ToUniversalTime().Ticks.ToString([Globalization.CultureInfo]::InvariantCulture)
}

function New-WorkbenchInstanceToken {
  return [guid]::NewGuid().ToString('N')
}

function Get-WorkbenchExpectedArguments {
  param([Parameter(Mandatory = $true)][string]$InstanceToken)
  return @($script:ServerScript, '--workbench-instance-token', $InstanceToken)
}

function ConvertTo-WorkbenchNativeArgument {
  param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Argument)
  if ($Argument.Length -gt 0 -and $Argument -notmatch '[\s"]') { return $Argument }
  $builder = [Text.StringBuilder]::new()
  [void]$builder.Append('"')
  $backslashes = 0
  foreach ($character in $Argument.ToCharArray()) {
    if ($character -eq '\') { $backslashes++; continue }
    if ($character -eq '"') {
      [void]$builder.Append(('\' * (($backslashes * 2) + 1)))
      [void]$builder.Append('"')
    } else {
      if ($backslashes -gt 0) { [void]$builder.Append(('\' * $backslashes)) }
      [void]$builder.Append($character)
    }
    $backslashes = 0
  }
  if ($backslashes -gt 0) { [void]$builder.Append(('\' * ($backslashes * 2))) }
  [void]$builder.Append('"')
  return $builder.ToString()
}

function Get-WorkbenchNativeArgumentString {
  param([Parameter(Mandatory = $true)][string[]]$Arguments)
  return (($Arguments | ForEach-Object { ConvertTo-WorkbenchNativeArgument -Argument ([string]$_) }) -join ' ')
}

function Initialize-WorkbenchCommandLineParser {
  if ($null -ne ('MemoWeft.Workbench.NativeCommandLine' -as [type])) { return }
  Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace MemoWeft.Workbench {
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

function Get-WorkbenchCommandLineArguments {
  param([Parameter(Mandatory = $true)][string]$CommandLine)
  Initialize-WorkbenchCommandLineParser
  return @([MemoWeft.Workbench.NativeCommandLine]::Parse($CommandLine))
}

function Test-WorkbenchStringArrayEqual {
  param([Parameter(Mandatory = $true)][string[]]$Left, [Parameter(Mandatory = $true)][string[]]$Right)
  if ($Left.Count -ne $Right.Count) { return $false }
  for ($index = 0; $index -lt $Left.Count; $index++) {
    if (-not [string]::Equals($Left[$index], $Right[$index], [StringComparison]::Ordinal)) { return $false }
  }
  return $true
}

function Test-WorkbenchStateShape {
  param([Parameter(Mandatory = $true)]$State)
  $required = @('schemaVersion','pid','processStartTicks','executable','serverScript','bindAddress','port','argv','instanceToken','stdout','stderr')
  $properties = @($State.PSObject.Properties | ForEach-Object { $_.Name })
  if ($properties.Count -ne $required.Count -or @($properties | Where-Object { $required -notcontains $_ }).Count -ne 0) { return 'unexpected state fields' }
  foreach ($name in @('schemaVersion','pid','port')) {
    if ($State.$name -isnot [ValueType] -or [string]$State.$name -notmatch '^-?\d+$') { return "invalid $name type" }
  }
  foreach ($name in @('processStartTicks','executable','serverScript','bindAddress','instanceToken','stdout','stderr')) {
    if ($State.$name -isnot [string] -or [string]::IsNullOrWhiteSpace($State.$name)) { return "invalid $name type" }
  }
  if ($State.argv -isnot [System.Array] -or @($State.argv).Count -ne 3 -or @($State.argv | Where-Object { $_ -isnot [string] }).Count -ne 0) { return 'invalid argv type' }
  if ([int64]$State.schemaVersion -ne $script:StateSchemaVersion) { return 'unsupported schema version' }
  if ([int64]$State.pid -le 0 -or [int64]$State.port -ne $script:WorkbenchPort) { return 'invalid state numeric value' }
  if ($State.processStartTicks -notmatch '^\d+$') { return 'invalid process start ticks' }
  if ($State.instanceToken -notmatch '^[a-f0-9]{32}$') { return 'invalid instance token' }
  return $null
}

function Get-WorkbenchState {
  if (-not (Test-Path -LiteralPath $script:StateFile -PathType Leaf)) { return $null }
  try {
    return Get-Content -LiteralPath $script:StateFile -Raw | ConvertFrom-Json
  } catch { throw "Workbench state is unreadable: $script:StateFile. $($_.Exception.Message)" }
}

function Write-WorkbenchState {
  param(
    [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
    [Parameter(Mandatory = $true)][hashtable]$Logs,
    [Parameter(Mandatory = $true)][string]$InstanceToken
  )
  Initialize-WorkbenchDirectories
  $payload = [ordered]@{
    schemaVersion = $script:StateSchemaVersion
    pid = [int]$Process.Id
    processStartTicks = Get-WorkbenchProcessStartTicks $Process
    executable = Get-CurrentWorkbenchNodeExecutable
    serverScript = $script:ServerScript
    bindAddress = $script:BindAddress
    port = $script:WorkbenchPort
    argv = Get-WorkbenchExpectedArguments $InstanceToken
    instanceToken = $InstanceToken
    stdout = $Logs.stdout
    stderr = $Logs.stderr
  }
  $temporary = "$script:StateFile.$([guid]::NewGuid().ToString('N')).tmp"
  [IO.File]::WriteAllText($temporary, ($payload | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
  if (Test-Path -LiteralPath $script:StateFile) {
    $backup = "$script:StateFile.$([guid]::NewGuid().ToString('N')).bak"
    [IO.File]::Replace($temporary, $script:StateFile, $backup)
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
  } else { [IO.File]::Move($temporary, $script:StateFile) }
  return [pscustomobject]$payload
}

function Remove-WorkbenchState {
  param([string]$ExpectedInstanceToken)
  if (-not (Test-Path -LiteralPath $script:StateFile -PathType Leaf)) { return }
  if (-not [string]::IsNullOrWhiteSpace($ExpectedInstanceToken)) {
    try {
      $current = Get-WorkbenchState
      if ($null -eq $current -or $current.instanceToken -ne $ExpectedInstanceToken) { return }
    } catch { return }
  }
  Remove-Item -LiteralPath $script:StateFile -Force
}

function Test-WorkbenchProcessIdentity {
  param(
    [Parameter(Mandatory = $true)][int]$ProcessId,
    [Parameter(Mandatory = $true)][string]$ProcessStartTicks,
    [Parameter(Mandatory = $true)][string[]]$ExpectedArguments
  )
  try {
    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    if ((Get-WorkbenchProcessStartTicks $process) -ne $ProcessStartTicks) { return [pscustomobject]@{ Verified=$false; Reason='creation time mismatch'; Process=$process } }
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
    $expectedNode = Get-CurrentWorkbenchNodeExecutable
    $actualNode = [IO.Path]::GetFullPath([string]$cim.ExecutablePath)
    if (-not [string]::Equals($actualNode, $expectedNode, [StringComparison]::OrdinalIgnoreCase)) { return [pscustomobject]@{ Verified=$false; Reason='executable mismatch'; Process=$process } }
    $actualCommand = Get-WorkbenchCommandLineArguments ([string]$cim.CommandLine)
    if ($actualCommand.Count -lt 1 -or -not (Test-WorkbenchStringArrayEqual -Left @($actualCommand | Select-Object -Skip 1) -Right $ExpectedArguments)) { return [pscustomobject]@{ Verified=$false; Reason='argv mismatch'; Process=$process } }
    return [pscustomobject]@{ Verified=$true; Reason='verified'; Process=$process }
  } catch { return [pscustomobject]@{ Verified=$false; Reason='managed process unavailable'; Process=$null } }
}

function Test-WorkbenchState {
  param([Parameter(Mandatory = $true)]$State)
  $shapeError = Test-WorkbenchStateShape $State
  if ($null -ne $shapeError) { return [pscustomobject]@{ Verified=$false; Reason=$shapeError; Process=$null } }
  if (-not [string]::Equals([string]$State.executable, (Get-CurrentWorkbenchNodeExecutable), [StringComparison]::OrdinalIgnoreCase)) { return [pscustomobject]@{ Verified=$false; Reason='state executable does not match current node.exe'; Process=$null } }
  if ($State.serverScript -ne $script:ServerScript -or $State.bindAddress -ne $script:BindAddress -or [int64]$State.port -ne $script:WorkbenchPort) { return [pscustomobject]@{ Verified=$false; Reason='state fixed field mismatch'; Process=$null } }
  $expectedArguments = Get-WorkbenchExpectedArguments $State.instanceToken
  if (-not (Test-WorkbenchStringArrayEqual -Left @($State.argv) -Right $expectedArguments)) { return [pscustomobject]@{ Verified=$false; Reason='state argv mismatch'; Process=$null } }
  $identity = Test-WorkbenchProcessIdentity -ProcessId ([int]$State.pid) -ProcessStartTicks $State.processStartTicks -ExpectedArguments $expectedArguments
  if (-not $identity.Verified) { return $identity }
  $listeners = @(Get-WorkbenchListeners)
  if ($listeners.Count -ne 1 -or [int]$listeners[0].OwningProcess -ne [int]$State.pid -or $listeners[0].LocalAddress -ne $script:BindAddress) { return [pscustomobject]@{ Verified=$false; Reason='listener ownership mismatch'; Process=$identity.Process } }
  return $identity
}

function Get-NextLabInstanceToken {
  if (-not (Test-Path -LiteralPath $script:NextLabStateFile -PathType Leaf)) { return $null }
  try {
    $state = Get-Content -LiteralPath $script:NextLabStateFile -Raw | ConvertFrom-Json
    if ($state.PSObject.Properties.Match('instanceToken').Count -ne 1 -or $state.instanceToken -isnot [string] -or $state.instanceToken -notmatch '^[a-f0-9]{32}$') { return $null }
    return $state.instanceToken
  } catch { return $null }
}

function Test-WorkbenchHttp {
  param([Parameter(Mandatory = $true)]$State)
  try {
    $identity = Invoke-RestMethod -Method Get -Uri "http://$script:BindAddress`:$script:WorkbenchPort/api/workbench-identity" -TimeoutSec 5
    if ($identity.kind -ne $script:WorkbenchIdentityKind -or $identity.instanceToken -ne $State.instanceToken) { return [pscustomobject]@{ Healthy=$false; Reason='workbench HTTP identity mismatch'; Health=$null; Next=$null } }
    $health = Invoke-RestMethod -Method Get -Uri "http://$script:BindAddress`:$script:WorkbenchPort/api/health" -TimeoutSec 5
    $next = Invoke-RestMethod -Method Get -Uri "http://$script:BindAddress`:$script:WorkbenchPort/api/next/status" -TimeoutSec 8
    $nextToken = Get-NextLabInstanceToken
    if ($null -eq $nextToken -or $next.serverIdentity -ne 'MemoWeftNextLab/1' -or $next.instanceToken -ne $nextToken) { return [pscustomobject]@{ Healthy=$false; Reason='Next server identity mismatch'; Health=$health; Next=$next } }
    return [pscustomobject]@{ Healthy=($null -ne $health); Reason='healthy'; Health=$health; Next=$next }
  } catch { return [pscustomobject]@{ Healthy=$false; Reason='HTTP unavailable'; Health=$null; Next=$null } }
}

function Stop-WorkbenchLaunchProcess {
  param([Parameter(Mandatory = $true)]$LaunchIdentity)
  $identity = Test-WorkbenchProcessIdentity -ProcessId $LaunchIdentity.ProcessId -ProcessStartTicks $LaunchIdentity.ProcessStartTicks -ExpectedArguments $LaunchIdentity.Argv
  if (-not $identity.Verified) { return $false }
  Stop-Process -InputObject $identity.Process -ErrorAction Stop
  try { Wait-Process -Id $LaunchIdentity.ProcessId -Timeout 15 -ErrorAction Stop } catch {}
  return ($null -eq (Get-Process -Id $LaunchIdentity.ProcessId -ErrorAction SilentlyContinue))
}

function Stop-VerifiedWorkbenchProcess {
  param([Parameter(Mandatory = $true)]$State)
  # Action-time verification prevents a state that was valid at status time from
  # authorizing a later stop after a PID or listener changed.
  $inspection = Test-WorkbenchState $State
  if (-not $inspection.Verified) { throw "Refusing to stop unverified workbench process: $($inspection.Reason)." }
  $process = $inspection.Process
  Stop-Process -InputObject $process -ErrorAction Stop
  try { Wait-Process -Id ([int]$process.Id) -Timeout 15 -ErrorAction Stop } catch {}
  if ($null -ne (Get-Process -Id ([int]$process.Id) -ErrorAction SilentlyContinue)) { throw 'Workbench process did not exit.' }
  if (@(Get-WorkbenchListeners).Count -ne 0) { throw 'Port 7888 is still occupied after the managed process exited.' }
  Remove-WorkbenchState -ExpectedInstanceToken $State.instanceToken
}
