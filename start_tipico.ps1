[CmdletBinding()]
param([int]$Port = 8506, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'tipico_runtime.ps1') -Action start -Port $Port -NoBrowser:$NoBrowser
exit $LASTEXITCODE
