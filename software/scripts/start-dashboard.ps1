$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$streamlit = Join-Path $projectRoot ".venv\Scripts\streamlit.exe"
$env:ENERGY_MONITOR_DB = Join-Path $projectRoot "data\telemetry.db"

if (-not (Test-Path -LiteralPath $streamlit)) {
    throw "Streamlit environment not found. Run: py -m venv .venv; .\.venv\Scripts\pip install -e '.[dev]'"
}

Set-Location -LiteralPath $projectRoot
& $streamlit run dashboard\app.py `
    --server.address 127.0.0.1 `
    --server.port 8501 `
    --server.headless true `
    --browser.gatherUsageStats false
