@echo off
setlocal

rem Ein-Klick-Start fuer den Tipico Live Observer.
set "PROJECT_ROOT=%~dp0"
echo Tipico Live Observer wird gestartet ...
echo Bitte dieses Fenster bis zum Oeffnen der Oberflaeche offen lassen.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%start_tipico.ps1" %*
set "START_RESULT=%ERRORLEVEL%"
if not "%START_RESULT%"=="0" (
    echo.
    echo Der Tipico Live Observer konnte nicht gestartet werden.
    echo Details stehen in logs\local-runtime.err.log, collector.err.log und paper.err.log.
    echo STATUS_TIPICO.bat zeigt den Zustand aller drei Dienste.
    pause
)
endlocal & exit /b %START_RESULT%
