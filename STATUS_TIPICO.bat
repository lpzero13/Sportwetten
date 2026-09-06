@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tipico_runtime.ps1" -Action status
pause
endlocal
