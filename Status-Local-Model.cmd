@echo off
setlocal
where pwsh.exe >nul 2>nul
if errorlevel 1 (
  set "MEMOWEFT_POWERSHELL=powershell.exe"
) else (
  set "MEMOWEFT_POWERSHELL=pwsh.exe"
)
"%MEMOWEFT_POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\local-model\Status-Local-Model.ps1" %*
exit /b %ERRORLEVEL%
