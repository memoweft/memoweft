[CmdletBinding()]
param([switch]$SkipLocalModel)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

$mutex = [Threading.Mutex]::new($false, 'Local\MemoWeft-Workbench-7888-Start')
$lockTaken = $false
try {
  $lockTaken = $mutex.WaitOne([TimeSpan]::FromSeconds(30))
  if (-not $lockTaken) { throw 'Timed out waiting for the MemoWeft workbench startup lock.' }

  Initialize-WorkbenchDirectories
  $state = Get-WorkbenchState
  if ($null -ne $state) {
    $inspection = Test-WorkbenchState $state
    if ($inspection.Verified) {
      $http = Test-WorkbenchHttp $state
      if (-not $http.Healthy) { throw "Managed workbench process exists but its HTTP identity/dependencies are not healthy: $($http.Reason)." }
      Write-Host "MemoWeft workbench is already running at http://127.0.0.1:7888 (PID $($state.pid))."
      return
    }
    if (@(Get-WorkbenchListeners).Count -ne 0) { throw "Refusing to replace stale state while port 7888 is occupied: $($inspection.Reason)." }
    $stateToken = $null
    if ($state.PSObject.Properties.Match('instanceToken').Count -eq 1) { $stateToken = [string]$state.instanceToken }
    Remove-WorkbenchState -ExpectedInstanceToken $stateToken
  }
  if (@(Get-WorkbenchListeners).Count -ne 0) { throw 'Port 7888 already has an unmanaged listener; refusing to start.' }

  # Collision decisions happen before dependencies: a known-bad UI state must
  # never start or perturb the Next backend or local model.
  if (-not $SkipLocalModel) {
    & (Join-Path $script:ProjectRoot 'Start-Local-Model.cmd')
    if ($LASTEXITCODE -ne 0) { throw 'Local model startup failed.' }
  }
  & (Join-Path $script:ProjectRoot 'Start-Next-Lab.cmd')
  if ($LASTEXITCODE -ne 0) { throw 'MemoWeft Next backend startup failed.' }

  $node = Get-CurrentWorkbenchNodeExecutable
  $token = New-WorkbenchInstanceToken
  $argv = Get-WorkbenchExpectedArguments $token
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
  $logs = @{ stdout = (Join-Path $script:WorkbenchRoot "workbench-$stamp.stdout.log"); stderr = (Join-Path $script:WorkbenchRoot "workbench-$stamp.stderr.log") }
  $saved = @{}
  $names = @(
    'MEMOWEFT_TESTBENCH_PORT', 'MEMOWEFT_TESTBENCH_PROFILE_EVERY_TURN', 'MEMOWEFT_TESTBENCH_MEMORY_AUTHORITY', 'MEMOWEFT_NEXT_LAB_URL', 'MEMOWEFT_WORKBENCH_INSTANCE_TOKEN',
    'MEMOWEFT_LLM_BASE_URL', 'MEMOWEFT_LLM_API_KEY', 'MEMOWEFT_LLM_MODEL', 'MEMOWEFT_LLM_TIER', 'MEMOWEFT_LLM_TIMEOUT_MS',
    'MEMOWEFT_WRITE_LLM_BASE_URL', 'MEMOWEFT_WRITE_LLM_API_KEY', 'MEMOWEFT_WRITE_LLM_MODEL', 'MEMOWEFT_WRITE_LLM_TIER', 'MEMOWEFT_WRITE_LLM_TIMEOUT_MS',
    'MEMOWEFT_EXPERIENCE_UI'
  )
  foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
  $process = $null
  try {
    $env:MEMOWEFT_TESTBENCH_PORT = '7888'
    # The managed workbench keeps 1.x as Evidence-only compatibility storage.
    # Accepted 2.0 world records are the sole durable memory authority, so no
    # native 1.x profile update may be scheduled after a chat turn.
    $env:MEMOWEFT_TESTBENCH_MEMORY_AUTHORITY = 'next'
    $env:MEMOWEFT_TESTBENCH_PROFILE_EVERY_TURN = 'off'
    $env:MEMOWEFT_NEXT_LAB_URL = 'http://127.0.0.1:7891'
    $env:MEMOWEFT_WORKBENCH_INSTANCE_TOKEN = $token
    $env:MEMOWEFT_EXPERIENCE_UI = 'on'
    $envFile = Join-Path $script:ProjectRoot '.env'
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf) -and [string]::IsNullOrWhiteSpace($env:MEMOWEFT_LLM_BASE_URL)) {
      $env:MEMOWEFT_LLM_BASE_URL = 'http://127.0.0.1:8012/v1'; $env:MEMOWEFT_LLM_API_KEY = 'local'; $env:MEMOWEFT_LLM_MODEL = 'qwen3-14b-local'; $env:MEMOWEFT_LLM_TIER = 'local'; $env:MEMOWEFT_LLM_TIMEOUT_MS = '300000'
      $env:MEMOWEFT_WRITE_LLM_BASE_URL = 'http://127.0.0.1:8012/v1'; $env:MEMOWEFT_WRITE_LLM_API_KEY = 'local'; $env:MEMOWEFT_WRITE_LLM_MODEL = 'qwen3-14b-local'; $env:MEMOWEFT_WRITE_LLM_TIER = 'local'; $env:MEMOWEFT_WRITE_LLM_TIMEOUT_MS = '300000'
    }
    $process = Start-Process -FilePath $node -ArgumentList (Get-WorkbenchNativeArgumentString $argv) -WorkingDirectory $script:ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $logs.stdout -RedirectStandardError $logs.stderr -PassThru -ErrorAction Stop
  } finally {
    foreach ($name in $names) { if ($null -eq $saved[$name]) { Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue } else { [Environment]::SetEnvironmentVariable($name, [string]$saved[$name], 'Process') } }
  }

  $launchIdentity = [pscustomobject]@{ ProcessId=[int]$process.Id; ProcessStartTicks=(Get-WorkbenchProcessStartTicks $process); Argv=$argv; InstanceToken=$token }
  $state = Write-WorkbenchState -Process $process -Logs $logs -InstanceToken $token
  try {
    $deadline = (Get-Date).AddSeconds(25)
    while ((Get-Date) -lt $deadline) {
      if ($null -eq (Get-Process -Id ([int]$process.Id) -ErrorAction SilentlyContinue)) { throw "Workbench exited during startup. Inspect $($logs.stderr)" }
      $inspection = Test-WorkbenchState $state
      if ($inspection.Verified) {
        $http = Test-WorkbenchHttp $state
        if ($http.Healthy) { Write-Host "MemoWeft workbench started: http://127.0.0.1:7888 (PID $($process.Id))"; return }
      }
      Start-Sleep -Milliseconds 250
    }
    throw "Workbench did not become healthy within 25 seconds. Inspect $($logs.stderr)"
  } catch {
    # The launch identity is established independently of listener readiness, so
    # a failed Node start cannot leave a matching process behind just because it
    # never bound 7888.
    try { [void](Stop-WorkbenchLaunchProcess $launchIdentity) } catch { Write-Warning "Failed to clean up newly launched workbench PID $($launchIdentity.ProcessId): $($_.Exception.Message)" }
    Remove-WorkbenchState -ExpectedInstanceToken $token
    throw
  }
} finally {
  if ($lockTaken) { $mutex.ReleaseMutex() | Out-Null }
  $mutex.Dispose()
}
