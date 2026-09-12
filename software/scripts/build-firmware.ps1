$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$platformio = Join-Path $projectRoot ".venv\Scripts\platformio.exe"
$firmwareRoot = Join-Path $projectRoot "firmware"

if (-not (Test-Path -LiteralPath $platformio)) {
    throw "PlatformIO environment not found. Run: py -m venv .venv; .\.venv\Scripts\pip install -e '.[dev]'"
}

# The bundled ESP32 GCC toolchain cannot reliably create dependency files when
# the project path contains spaces. Only generated artifacts are redirected.
$env:PLATFORMIO_BUILD_DIR = Join-Path $env:SystemDrive "pio-build\eee2110-energy-monitor-release"

Set-Location -LiteralPath $firmwareRoot
& $platformio run -e esp32dev -t clean
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $platformio run -e esp32dev -j 1
exit $LASTEXITCODE
