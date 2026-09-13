# EEE2110 Multi-Purpose Energy Monitor

This is the software-first vertical slice of the proposed appliance energy monitor.

Production flow:

```text
ESP32 -> authenticated Supabase Edge Function -> Supabase telemetry
    -> Streamlit server sync -> local analysis cache -> cycle processor -> dashboard
```

Development flow:

```text
simulated ESP32 readings -> ingestion API -> raw telemetry
    -> automatic cycle processor -> cycle summaries -> Live/Cycles dashboard
```

One five-second record is one sample. A typical two-to-three-minute boil therefore contains
roughly 24–36 samples, which the backend combines into one kettle cycle. Raw telemetry remains
the source of truth and reprocessing the same data does not duplicate the cycle.

## Included

- ESP32/PlatformIO firmware with switchable simulated and PZEM-004T v3 readers.
- Valid-clock gating, reboot-safe sample sequences, bounded buffering, stable retry batches,
  and exponential upload backoff.
- Local Python API with idempotent batches of up to six readings.
- SQLite development storage and a local analytical cache for Supabase telemetry.
- Supabase-compatible PostgreSQL schema with row-level security.
- Server-side Supabase dashboard sync; the service-role key is never sent to the browser.
- Automatic normal, interrupted, and short-spike simulation scenarios.
- Streamlit `Live` and `Cycles` views with five-second refresh, pause, manual refresh, cycle
  history, a rolling active-power chart, measured cycle-energy accumulation, quality evidence,
  reusable user-defined labels, and clearly sourced manual cycle records.
- Versioned TNB Domestic General RP4 tariff components and September 2026 AFA provenance.
- Python, contract, API, storage, dashboard, simulator, and native firmware tests.

## Cost and tariff meaning

The local stack is free and open source. A Supabase free project is sufficient for early
development; no paid Codex plugin or third-party subscription is required for this build.

The dashboard uses versioned official TNB Domestic General RP4 components. It reports a gross
variable cycle-energy estimate, not a complete household bill. If monthly household usage is
unknown, it shows a qualified range because the applicable energy tier, AFA eligibility, and
energy-efficiency incentive cannot be inferred safely.

The estimate excludes the monthly retail charge, energy-efficiency incentive, taxes, fund
contributions, rebates, and whole-bill rounding. Set optional household context before launching
the dashboard only when the value is known:

```powershell
$env:ENERGY_MONITOR_MONTHLY_KWH = "300"
```

There is intentionally no free-form RM/kWh control in the interface.

## First-time setup

From PowerShell in this directory:

```powershell
py -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
```

## Run the local development flow

Open three PowerShell terminals in this directory.

Terminal 1 — start the ingestion API:

```powershell
.\scripts\start-local-api.ps1
```

Terminal 2 — upload one deterministic normal kettle cycle:

```powershell
.\scripts\run-simulator.ps1
```

The scenario has 3 idle readings, 30 heating readings at 2,050 W, and 3 trailing idle readings.
It crosses multiple upload batches but is stored as one completed cycle. Alternative development
fixtures are available with `--scenario incomplete_gap`, `--scenario short_spike`, or
`--scenario stream`.

Terminal 3 — start the dashboard:

```powershell
.\scripts\start-dashboard.ps1
```

Open <http://127.0.0.1:8501>. Use `Live` for instantaneous readings and active-cycle values marked
`so far`. Its five-minute active-power trace shows the start threshold and detected-cycle window;
its cycle-energy trace shows the active cycle or the latest completed cycle when idle. Use
`Cycles` for completed/incomplete cycle receipts, selection, filtering, tariff details, and
reusable user-defined cycle labels. Removed labels disappear from future choices while past cycle
records keep their audit history; adding the same label restores it. The Live view also charts
measured frequency and power factor. Every graph includes a `Reset view` control that restores its
original zoom and scale without changing the stored telemetry. Cycle-history rows use compact
Malaysia-time values; the detailed receipt retains the exact UTC audit time and measurement
precision.

Manual cycle records require a past Malaysia start time, duration, entered energy, and label.
They are marked `Manual`, retain optional notes, have no invented telemetry graph, and remain
excluded from automatic anomaly comparison or future model-training data unless validated later.

## Verify the build

```powershell
.\scripts\test-all.ps1
.\scripts\build-firmware.ps1
```

The firmware build script redirects generated compiler artifacts to `C:\pio-build` because the
current Windows project path contains spaces. Source files remain in this project.

## Use Supabase as the production source

The cloud schema is in `supabase/migrations`, and the authenticated ingestion endpoint is in
`supabase/functions/ingest-telemetry`. Apply the migrations in filename order, deploy the Edge
Function, and configure its device-ingestion token. Then start the dashboard with server-only
credentials:

```powershell
$env:ENERGY_MONITOR_SOURCE = "supabase"
$env:SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
$env:SUPABASE_SECRET_KEY = "YOUR_SERVER_ONLY_KEY"
.\scripts\start-dashboard.ps1
```

The dashboard pulls only newer telemetry into `data/supabase-cache.db`, then performs cycle
detection locally. Never put the service-role key in ESP32 firmware, browser code, or source
control. Legacy projects may use `SUPABASE_SERVICE_ROLE_KEY` instead. Manual records and user
labels remain in the selected local dashboard database.

For a new hosted project, use the validated CLI flow below. Review the dry run before applying
the migrations:

```powershell
npx --yes supabase@2.117.0 login
npx --yes supabase@2.117.0 link --project-ref YOUR_PROJECT_REF
npx --yes supabase@2.117.0 db push --dry-run
npx --yes supabase@2.117.0 db push
Copy-Item supabase\functions\.env.example supabase\functions\.env
# Replace CHANGE_ME inside the ignored .env file before continuing.
npx --yes supabase@2.117.0 secrets set --env-file supabase\functions\.env
npx --yes supabase@2.117.0 functions deploy ingest-telemetry --use-api
```

`supabase/config.toml` disables platform JWT verification only for the ingestion function. The
function still requires its private `x-device-token`, validates every reading, limits request
size, and performs idempotent storage.

## Firmware modes

The default `esp32dev` environment uses deterministic simulated measurements. Copy
`firmware/include/local_secrets.example.h` to `local_secrets.h`, fill the Wi-Fi credentials,
Edge Function URL, device token, device ID, and profile ID, then build or flash that environment.

The physical `esp32dev-pzem` environment uses the PZEM-004T v3 reader. Only after the actual ESP32
board, supervised UART wiring, isolation, and enclosure are confirmed, copy
`local_hardware.example.h` to `local_hardware.h`, enter the approved RX/TX pins, and run:

```powershell
.\.venv\Scripts\platformio.exe run -d firmware -e esp32dev-pzem
```

The official PZEM library is pinned to the compatible `1.2.x` release line. Battery voltage is
optional because the PZEM does not provide it.

## Delivery behaviour

- One reading is sampled every five seconds and up to six readings are sent per request.
- A fixed 120-reading RAM queue buffers about ten minutes of readings.
- A failed request retains the same batch ID and retries with one-to-sixty-second backoff.
- The ESP32 reserves non-overlapping sequence-number blocks in non-volatile storage, preventing
  reboot reuse without writing flash for every sample.
- Readings are not created until NTP supplies a plausible UTC clock.
- When the queue is full, new samples are deferred until delivery resumes. Flash-backed telemetry
  buffering can be added later if field testing requires outages longer than ten minutes.

## Deferred work

Water-volume model training, Isolation Forest validation, agentic reporting, cloud deployment,
real UART pin selection, and physical hardware validation remain outside this software-dashboard
build. Manual labels are collected now so a later volume model can be trained on validated cycles.

## Hardware boundary

No PZEM-004T pins are assigned and no mains-voltage procedure is included. The real-reader build
fails deliberately until a local, ignored hardware file supplies confirmed pins. Hardware
integration should begin only after the board, UART pins, isolation/enclosure approach, and
supervised lab procedure are confirmed.
