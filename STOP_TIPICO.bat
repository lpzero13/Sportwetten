@echo off
setlocal

rem Beendet den vom Ein-Klick-Start gestarteten lokalen Observer.
set "PROJECT_ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%stop_tipico.ps1"
pause
endlocal
