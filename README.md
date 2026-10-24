# EEE2110 Multi-Purpose Energy Monitor

Software-first prototype for an ESP32 appliance energy monitor. Current implementation provides
five-second electrical telemetry, automatic load-cycle detection, cycle energy and qualified TNB
cost estimates, manual test records, user-defined labels, and a Streamlit dashboard with saved reports.

The household kettle is the first validation load. Kettle results do not establish accuracy for
every appliance.

## Repository layout

- [`software/`](software/) contains dashboard, local API, ESP32 firmware, Supabase schema,
  simulations, tests, and detailed setup instructions.
- [`docs/`](docs/) contains design specifications, implementation plans, and supporting research.

Start with [`software/README.md`](software/README.md).

## Deploy the dashboard

This repository is ready for Streamlit Community Cloud. Create an app with:

- Repository: `darenlee36/eee2110-energy-monitor`
- Branch: `main`
- Entrypoint: `software/dashboard/app.py`
- Python: `3.12`

The dashboard runs locally without cloud credentials. Add Supabase secrets in Streamlit's
Advanced settings only when the Supabase project is ready; never commit them to Git.
For an isolated demonstration, set `ENERGY_MONITOR_SOURCE = "demo"` in Streamlit secrets to show a clearly marked,
per-session simulated snapshot.

## Current status

- Local simulated telemetry path: implemented and tested.
- ESP32 PZEM-004T reader path: compile-verified; physical pins remain unassigned.
- Supabase ingestion, durable reports and dashboard synchronization: deployed and verified with
  synthetic telemetry. A physical ESP32 has sent synthetic readings; real PZEM readings are unvalidated.
- Individual-cycle and exact 5/10/20-cycle reports: implemented, including electrical charts,
  explicit data origin, qualified cost and an honestly unavailable untrained anomaly score.
- Codex-selected insights: bounded application-owned observation/action selection. A guarded
  30-minute laptop worker can save one pending AI review per pass; public visitors cannot invoke it.
- Hardware calibration, supervised mains-side validation, labelled real-data collection,
  Isolation Forest evaluation, off-laptop reporting and final software evaluation remain pending.

## Release notes

### v0.2.0 — 25 October 2026

- Added saved per-cycle reports and exact 5/10/20-cycle summaries with durable cloud history.
- Added average/peak electrical metrics, saved-reading charts, reset controls and readable insights.
- Added clearly separated illustrative score preview; actual scores stay unavailable until evaluated.
- Added bounded Codex-selected insights and a quota-guarded, duplicate-resistant laptop schedule.
- Preserved public read-only access, server-only cloud writes and explicit synthetic-data labelling.

This is a reporting release, not proof of real-sensor accuracy or a trained anomaly detector.

## Safety boundary

This repository does not provide exposed-mains wiring instructions. Physical integration requires
the confirmed ESP32 board, approved UART pins, an enclosed isolation arrangement, and supervised
lab testing.
