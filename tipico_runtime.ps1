[CmdletBinding()]
param(
    [ValidateSet('start', 'stop', 'status')][string]$Action = 'start',
    [int]$Port = 8506,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$taskRoot = $PSScriptRoot
$taskMutex = $null
if ($Action -eq 'start') {
    # Serialize setup too: two first-time clicks must not create/install into
    # the same virtual environment at once.
    $taskHasher = [Security.Cryptography.SHA256]::Create()
    try {
        $taskHash = [BitConverter]::ToString($taskHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskRoot.ToUpperInvariant()))).Replace('-', '')
    } finally { $taskHasher.Dispose() }
    $taskMutex = New-Object System.Threading.Mutex($false, "Local\TipicoStart-$taskHash")
    try { $taskAcquired = $taskMutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $taskAcquired = $true }
    if (-not $taskAcquired) {
        $taskMutex.Dispose()
        Write-Host 'Der gemeinsame Start wird bereits ausgefuehrt. Bitte das erste Startfenster abwarten.'
        exit 0
    }
}
try {
$taskPython = $null
foreach ($relative in @('work\v01-venv\Scripts\python.exe', '.venv\Scripts\python.exe', 'venv\Scripts\python.exe')) {
    $candidate = Join-Path $taskRoot $relative
    if (Test-Path -LiteralPath $candidate) { $taskPython = $candidate; break }
}
if (-not $taskPython -and $Action -eq 'start') {
    Write-Host 'Python-Umgebung wird eingerichtet (nur beim ersten Start).'
    $taskVenv = Join-Path $taskRoot '.venv'
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        & py.exe -3.12 -m venv $taskVenv
    } elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
        & python.exe -m venv $taskVenv
    } else {
        throw 'Python 3.12 fehlt. Bitte installieren und START_TIPICO.bat erneut oeffnen.'
    }
    if ($LASTEXITCODE -ne 0) { throw 'Die Python-Umgebung konnte nicht angelegt werden.' }
    $taskPython = Join-Path $taskVenv 'Scripts\python.exe'
}
if (-not $taskPython) { throw 'Keine Projekt-Python-Umgebung vorhanden. Zuerst START_TIPICO.bat verwenden.' }
if ($Action -eq 'start') {
    & $taskPython (Join-Path $taskRoot 'scripts\check_runtime.py')
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Projekt-Abhaengigkeiten werden installiert.'
        & $taskPython -m pip install -r (Join-Path $taskRoot 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Installation fehlgeschlagen. Bitte die Meldung oben pruefen.' }
    }
}
$taskArguments = @((Join-Path $taskRoot 'scripts\local_runtime.py'), $Action, '--root', $taskRoot, '--port', "$Port")
if ($NoBrowser) { $taskArguments += '--no-browser' }
& $taskPython @taskArguments
exit $LASTEXITCODE
} finally {
    if ($taskMutex) { $taskMutex.ReleaseMutex(); $taskMutex.Dispose() }
}
