[CmdletBinding()]
param(
    [int]$Port = 8506
)

$ErrorActionPreference = "SilentlyContinue"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
$PidFile = Join-Path (Join-Path $ProjectRoot "logs") "streamlit.pid"

if (-not (Test-Path -LiteralPath $PidFile)) {
    Write-Host "Kein Tipico-Server-PID gefunden."
    exit 0
}

$candidatePids = @()
foreach ($pidLine in (Get-Content -LiteralPath $PidFile)) {
    $candidatePid = 0
    if ([int]::TryParse($pidLine.Trim(), [ref]$candidatePid) -and $candidatePid -gt 0) {
        $candidatePids += $candidatePid
    }
}

# Falls eine alte Version nur den Watcher gespeichert hat, ergänzen wir den
# Prozess, der den lokalen Streamlit-Port tatsächlich abhört.
$connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($connection in $connections) {
    $candidatePids += [int]$connection.OwningProcess
}

$stopped = $false
foreach ($candidatePid in ($candidatePids | Select-Object -Unique)) {
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$candidatePid"
    if ($processInfo -and $processInfo.CommandLine -like "*streamlit*" -and $processInfo.CommandLine -like "*app.py*") {
        Stop-Process -Id $candidatePid -Force
        $stopped = $true
    }
}

if ($stopped) {
    Write-Host "Tipico Live Observer wurde beendet."
}
else {
    Write-Host "Der gespeicherte Prozess ist nicht mehr der Tipico Live Observer."
}

Remove-Item -LiteralPath $PidFile -Force
