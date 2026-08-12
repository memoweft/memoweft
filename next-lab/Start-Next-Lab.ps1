[CmdletBinding()]
param([string]$PythonExe = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

$mutex = Enter-NextLabLifecycleMutex
try {
  Initialize-NextLabDirectories
  $state = Get-NextLabState
  if ($null -ne $state) {
    $inspection = Get-NextLabFullInspection $state
    if ($inspection.Exists) {
      if ($inspection.Verified) { Write-Host "Next Lab is already running at http://127.0.0.1:7891 (PID $($inspection.Process.ProcessId))."; exit 0 }
      throw "Refusing to overwrite managed Next Lab state: $($inspection.Reason)."
    }
    if (@(Get-NextLabListeners).Count -ne 0) { throw 'Refusing to remove stale Next Lab state while port 7891 has a listener.' }
    Remove-NextLabState ([int](Get-NextLabStateProperty $state 'pid'))
  } elseif (@(Get-NextLabListeners).Count -ne 0) {
    throw 'Port 7891 already has an unmanaged or colliding listener; refusing to start.'
  }

  $pythonCandidate = if (-not [string]::IsNullOrWhiteSpace($PythonExe)) {
    $PythonExe
  } elseif (-not [string]::IsNullOrWhiteSpace($env:MEMOWEFT_NEXT_PYTHON)) {
    $env:MEMOWEFT_NEXT_PYTHON
  } else {
    Join-Path $script:ProjectRoot 'py\.venv\Scripts\python.exe'
  }
  if (-not (Test-Path -LiteralPath $pythonCandidate -PathType Leaf)) {
    throw "MemoWeft Next Python is unavailable at '$pythonCandidate'. Create py\.venv, set MEMOWEFT_NEXT_PYTHON, or pass -PythonExe."
  }
  $python = (Resolve-Path -LiteralPath $pythonCandidate).Path
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
  $logs = [ordered]@{ stdout = (Join-Path $script:LabRoot "next-lab-$stamp.stdout.log"); stderr = (Join-Path $script:LabRoot "next-lab-$stamp.stderr.log") }
  $token = [guid]::NewGuid().ToString('N')
  $launchProcess = $null
  $boundState = $null
  try {
    $launchProcess = Start-Process -FilePath $python -ArgumentList @($script:ServerScript,'--host',$script:BindAddress,'--port',[string]$script:LabPort,'--state-dir',$script:LabRoot,'--instance-token',$token) -WorkingDirectory $script:ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $logs.stdout -RedirectStandardError $logs.stderr -PassThru -ErrorAction Stop
    $boundState = Write-NextLabState -ProcessId ([int]$launchProcess.Id) -Logs $logs -InstanceToken $token
    $until = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $until) {
      $listeners = @(Get-NextLabListeners)
      if ($listeners.Count -eq 1) {
        $listener = $listeners[0]
        if ($listener.LocalAddress -ne $script:BindAddress) { throw 'A non-loopback listener appeared on port 7891 during startup.' }
        $listenerPid = [int]$listener.OwningProcess
        if (-not (Test-NextLabProcessOwnedBy -CandidateProcessId $listenerPid -RootProcessId ([int]$launchProcess.Id))) { throw 'An unowned listener appeared on port 7891 during startup.' }
        $boundState = Write-NextLabState -ProcessId $listenerPid -Logs $logs -InstanceToken $token
        $full = Get-NextLabFullInspection $boundState
        if (-not $full.Verified) { throw "Next Lab startup identity validation failed: $($full.Reason)." }
        Write-Host "Next Lab started: http://127.0.0.1:7891 (PID $listenerPid)"
        exit 0
      }
      if ($listeners.Count -gt 1) { throw 'Multiple listeners appeared on port 7891 during startup.' }
      if ($null -eq (Get-Process -Id ([int]$launchProcess.Id) -ErrorAction SilentlyContinue)) { throw "Next Lab exited before accepting requests. Inspect $($logs.stderr)" }
      Start-Sleep -Milliseconds 200
    }
    throw "Next Lab did not become managed within 15 seconds. Inspect $($logs.stderr)"
  } catch {
    if ($null -ne $launchProcess) { Stop-NextLabOwnedLaunchProcess ([int]$launchProcess.Id) }
    if ($null -ne $boundState -and @(Get-NextLabListeners).Count -eq 0) { Remove-NextLabState ([int](Get-NextLabStateProperty $boundState 'pid')) }
    throw
  }
} finally {
  Exit-NextLabLifecycleMutex $mutex
}
