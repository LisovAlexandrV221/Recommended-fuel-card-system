param(
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = Join-Path $root "app\backend\server.ps1"

if (-not (Test-Path -LiteralPath $server)) {
    throw "Server script not found: $server"
}

& $server -Port $Port
