[CmdletBinding()]
param([int]$Port = 8506)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'tipico_runtime.ps1') -Action stop -Port $Port
exit $LASTEXITCODE
