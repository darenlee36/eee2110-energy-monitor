$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$database = Join-Path $projectRoot "data\telemetry.db"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found. Run: py -m venv .venv; .\.venv\Scripts\pip install -e '.[dev]'"
}

Set-Location -LiteralPath $projectRoot
& $python -m energy_monitor.api --host 127.0.0.1 --port 8000 --database $database
