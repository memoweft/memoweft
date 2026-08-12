@echo off
setlocal
where pwsh.exe >nul 2>nul
if errorlevel 1 (set "MEMOWEFT_POWERSHELL=powershell.exe") else (set "MEMOWEFT_POWERSHELL=pwsh.exe")
"%MEMOWEFT_POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0next-lab\Stop-Next-Lab.ps1" %*
exit /b %ERRORLEVEL%
