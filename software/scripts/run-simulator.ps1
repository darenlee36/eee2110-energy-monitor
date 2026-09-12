param(
    [ValidateRange(1, 10000)]
    [int]$Readings = 72
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$arguments = @(
    "-m", "energy_monitor.simulator",
    "--url", "http://127.0.0.1:8000/api/v1/telemetry/batches",
    "--count", $Readings
)

Set-Location -LiteralPath $projectRoot
& $python @arguments
