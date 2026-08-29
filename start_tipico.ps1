[CmdletBinding()]
param(
    [int]$Port = 8506
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
$AppPath = Join-Path $ProjectRoot "app.py"
$LogDirectory = Join-Path $ProjectRoot "logs"
$StdOutLog = Join-Path $LogDirectory "streamlit.out.log"
$StdErrLog = Join-Path $LogDirectory "streamlit.err.log"
$PidFile = Join-Path $LogDirectory "streamlit.pid"
$Url = "http://127.0.0.1:$Port"

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null

function Test-TipicoReady {
    param([string]$Endpoint)

    try {
        $response = Invoke-WebRequest -Uri $Endpoint -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

function Get-TipicoProcessIds {
    $appPattern = [WildcardPattern]::Escape($AppPath)
    $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -like "*streamlit*" -and
            $_.CommandLine -like "*$appPattern*"
        }
    return @($processes | Select-Object -ExpandProperty ProcessId)
}

if (-not (Test-Path -LiteralPath $AppPath)) {
    throw "app.py wurde nicht gefunden: $AppPath"
}

# Mehrfaches Klicken startet keinen zweiten Server, sondern oeffnet nur den
# bereits laufenden lokalen Observer.
if (Test-TipicoReady -Endpoint $Url) {
    Start-Process $Url
    Write-Host "Tipico Live Observer laeuft bereits: $Url"
    exit 0
}

$pythonCandidates = @(
    (Join-Path $ProjectRoot "work\v01-venv\Scripts\python.exe"),
    (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
    (Join-Path $ProjectRoot "venv\Scripts\python.exe")
)
$PythonExe = $null

foreach ($candidate in $pythonCandidates) {
    if (Test-Path -LiteralPath $candidate) {
        $PythonExe = $candidate
        break
    }
}

if (-not $PythonExe) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $PythonExe = $pythonCommand.Source
    }
}

if (-not $PythonExe) {
    throw "Keine Python-Installation gefunden. Bitte Python 3.12 installieren oder die virtuelle Umgebung unter work\v01-venv anlegen."
}

$argumentLine = "-m streamlit run `"$AppPath`" --server.headless true --server.port $Port --browser.gatherUsageStats false"
$process = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $argumentLine `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdOutLog `
    -RedirectStandardError $StdErrLog `
    -PassThru

$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    $process.Refresh()
    if ($process.HasExited) {
        throw "Streamlit wurde beendet (Exit-Code $($process.ExitCode)). Details stehen in $StdErrLog"
    }

    if (Test-TipicoReady -Endpoint $Url) {
        $tipicoProcessIds = @(Get-TipicoProcessIds)
        if ($tipicoProcessIds.Count -eq 0) {
            $tipicoProcessIds = @($process.Id)
        }
        Set-Content -LiteralPath $PidFile -Value ($tipicoProcessIds -join [Environment]::NewLine) -Encoding ascii
        Start-Process $Url
        Write-Host "Tipico Live Observer ist bereit: $Url"
        exit 0
    }

    Start-Sleep -Milliseconds 500
}

throw "Der Tipico Live Observer wurde nicht innerhalb von 60 Sekunden bereit. Details stehen in $StdErrLog"
