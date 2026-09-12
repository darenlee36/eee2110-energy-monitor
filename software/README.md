# EEE2110 Household Kettle Cycle Monitor

This is the software-first vertical slice of the proposed appliance energy monitor:

```text
simulated ESP32 readings -> ingestion API -> raw telemetry
    -> automatic cycle processor -> cycle summaries -> Live/Cycles dashboard
```

One five-second record is one sample. A typical two-to-three-minute boil therefore contains
roughly 24–36 samples, which the backend combines into one kettle cycle. Raw telemetry remains
the source of truth and reprocessing the same data does not duplicate the cycle.

## Included

- ESP32/PlatformIO firmware with a safe simulated-reading mode.
- Local Python API with idempotent batches of up to six readings.
- SQLite raw telemetry, cycle summaries, and auditable volume labels.
- Supabase-compatible PostgreSQL schema with row-level security.
- Automatic normal, interrupted, and short-spike simulation scenarios.
- Streamlit `Live` and `Cycles` views with five-second refresh, pause, manual refresh, cycle
  history, one-cycle chart, quality evidence, and optional volume labels.
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

## Run the local prototype

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
`so far`; use `Cycles` for completed/incomplete cycle receipts, selection, filtering, tariff
details, and manual volume labels.

## Verify the build

```powershell
.\scripts\test-all.ps1
.\scripts\build-firmware.ps1
```

The firmware build script redirects generated compiler artifacts to `C:\pio-build` because the
current Windows project path contains spaces. Source files remain in this project.

## Connect Supabase later

The cloud schema is in `supabase/migrations`, and the authenticated ingestion endpoint is in
`supabase/functions/ingest-telemetry`. Deployment needs a Supabase project URL, project
credentials, and a chosen device-ingestion token. Keep the service-role key inside the Edge
Function environment; never put it in ESP32 firmware or the browser.

## Deferred work

Water-volume model training, Isolation Forest validation, agentic reporting, real UART mapping,
and physical hardware integration remain outside this software-dashboard build. Manual labels
are collected now so a later four-class volume model can be trained on validated cycles.

## Hardware boundary

The firmware is intentionally compiled with `TELEMETRY_SIMULATED=1`. No PZEM-004T pins are
assigned and no mains-voltage procedure is included. Hardware integration should begin only after
the board, UART pins, isolation/enclosure approach, and supervised lab procedure are confirmed.
