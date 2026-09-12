# EEE2110 Smart Energy Monitor — Software Vertical Slice

This is a software-first prototype of the proposal flow:

`simulated ESP32 readings -> ingestion API -> telemetry database -> live dashboard`

The simulator and local database make the complete flow testable before any 230 V wiring or final pin mapping. The same telemetry contract is shared by the Python prototype, ESP32 firmware, database migration, and Supabase Edge Function.

## What is included

- ESP32/PlatformIO firmware with a safe simulated-reading mode
- Local Python ingestion API with idempotent 6-reading batch uploads
- SQLite development storage
- Supabase Postgres migration and Edge Function for the production adapter
- Streamlit dashboard with power, voltage, current, energy, estimated cost, device status, and alerts
- Contract, API, storage, dashboard, simulator, and native firmware tests

## Cost

Everything in this repository is free and open source. A Supabase free project is sufficient for early development. No paid plugin or third-party subscription is required for this stage.

## First-time setup

From PowerShell in this directory:

```powershell
py -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
```

## Run the local prototype

Open three PowerShell terminals in this directory.

Terminal 1 — ingestion API:

```powershell
.\scripts\start-local-api.ps1
```

Terminal 2 — create 72 synthetic readings immediately:

```powershell
.\scripts\run-simulator.ps1
```

The generated timestamps follow the planned 5-second sampling interval; upload is immediate so the demo is fast.

Terminal 3 — dashboard:

```powershell
.\scripts\start-dashboard.ps1
```

Open <http://127.0.0.1:8501>. The default RM0.60/kWh tariff is explicitly a development estimate and is adjustable in the sidebar.

## Verify the build

```powershell
.\scripts\test-all.ps1
.\scripts\build-firmware.ps1
```

The firmware build script redirects generated compiler artifacts to `C:\pio-build` because the current Windows project path contains spaces. Source files remain in this project.

## Connect Supabase later

The cloud schema is in `supabase/migrations` and the authenticated ingestion endpoint is in `supabase/functions/ingest-telemetry`. Deployment needs only a Supabase project URL, project credentials, and a chosen device-ingestion token. Keep the service-role key inside the Edge Function environment; never put it in ESP32 firmware or the browser.

## Hardware boundary

The firmware is intentionally compiled with `TELEMETRY_SIMULATED=1`. No PZEM-004T pins are assigned and no mains-voltage procedure is included. Hardware integration should begin only after the board, UART pins, isolation/enclosure approach, and supervised lab procedure are confirmed.
