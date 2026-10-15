# Cloud validation — 15 October 2026

## Verified

- Supabase project `lxwogpxjfdckrfdicpyp` was restored to `ACTIVE_HEALTHY`.
- All 11 application tables retain Row-Level Security and the profile, detection and tariff seeds.
- Explicit server-role grants now allow telemetry SELECT/INSERT and ingestion-batch SELECT/INSERT/UPDATE.
- Anonymous telemetry INSERT remains denied.
- Client EXECUTE privileges on the platform's `rls_auto_enable()` event-trigger function were removed.
- Security advisors retain only the expected informational notice for the server-only ingestion-batch table.
- `ingest-telemetry` uses custom `x-device-token` authentication with platform JWT checking disabled.
- Missing and invalid device tokens return HTTP 401.
- The private device token and server key are stored only in the Git-ignored local `.env` file and the relevant cloud secrets.

## Synthetic end-to-end test

Test device: `sim-cloud-573deef52fa8`. Firmware identifier: `sim-cycle-0.1.0`.
These are synthetic readings, not hardware evidence or training data.

| Check | Result |
| --- | --- |
| Normal scenario | 36 five-second samples, six upload batches |
| Ingestion | All six batches completed; 36 samples accepted |
| Replay | HTTP 200 with `replayed=true`; no extra stored rows |
| Invalid voltage | 999 V rejected with HTTP 422 `VALIDATION_ERROR` |
| Cloud sync | 36 samples imported into an ignored local validation cache |
| Repeat sync | Zero additional samples |
| Cycle detection | One completed cycle |
| Heating-sample span | 145 seconds between first and last of 30 heating samples |
| Meter energy | 0.085417 kWh, including the increment from the idle predecessor |
| Integrated energy | 0.082569444 kWh over the 145-second sampled span |
| Dashboard rendering | Live and Cycles passed Streamlit AppTest; three live charts |
| Gross charge range | RM0.0380–RM0.0465, partial estimate |

Duration is the observed first-to-last heating sample span. The simulator accumulates 30
five-second heating intervals, so meter energy represents 150 seconds at 2,050 W. This
sampling-boundary convention must be checked during real-data calibration.

## Remaining work

- Configure the public Streamlit deployment with server-side Supabase secrets and change
  `ENERGY_MONITOR_SOURCE` from `demo` to `supabase`, then verify the hosted interface.
- Test real ESP32 telemetry separately before claiming a working physical monitor.
- Review cloud-sync handling of delayed readings, equal timestamps and history pagination.
- Manual cycle records and custom labels currently remain in the dashboard's local SQLite
  database; hosted restarts do not provide durable storage for those edits.
- October 2026 AFA is absent from the current tariff catalog; household usage is also unknown.
  The charge is therefore a partial range, not a validated household bill.
- Hardware-calibrated detection, labelled datasets, Isolation Forest and agentic reporting remain pending.

Private credentials are deliberately excluded from this evidence record.
