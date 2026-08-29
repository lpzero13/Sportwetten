@echo off
setlocal

rem Ein-Klick-Start fuer den Tipico Live Observer.
set "PROJECT_ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%start_tipico.ps1"
if errorlevel 1 (
    echo.
    echo Der Tipico Live Observer konnte nicht gestartet werden.
    echo Details stehen in logs\streamlit.err.log.
    pause
)
endlocal
