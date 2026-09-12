$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$platformio = Join-Path $projectRoot ".venv\Scripts\platformio.exe"

Set-Location -LiteralPath $projectRoot
& $python -m pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python -m ruff check . --no-cache
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Set-Location -LiteralPath (Join-Path $projectRoot "firmware")
& $platformio test -e native
$testExitCode = $LASTEXITCODE
Set-Location -LiteralPath $projectRoot
exit $testExitCode
