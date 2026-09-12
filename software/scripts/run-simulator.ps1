$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found. Run: py -m venv .venv; .\.venv\Scripts\pip install -e '.[dev]'"
}

Set-Location -LiteralPath $projectRoot
& $python -m energy_monitor.simulator --scenario normal @args
exit $LASTEXITCODE
