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
- Streamlit Community Cloud now uses server-only Supabase credentials and
  `ENERGY_MONITOR_SOURCE=supabase`. The public Live view displays the cloud cycle.

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
| Dashboard rendering | Live and Cycles passed Streamlit AppTest and hosted-browser inspection |
| Gross charge range | RM0.0380–RM0.0465, partial estimate |

Duration is the observed first-to-last heating sample span. The simulator accumulates 30
five-second heating intervals, so meter energy represents 150 seconds at 2,050 W. This
sampling-boundary convention must be checked during real-data calibration.

## Remaining work

- Test real ESP32 telemetry separately before claiming a working physical monitor.
- October 2026 AFA is absent from the current tariff catalog; household usage is also unknown.
  The charge is therefore a partial range, not a validated household bill.
- Hardware-calibrated detection, labelled datasets, Isolation Forest and agentic reporting remain pending.

Private credentials are deliberately excluded from this evidence record.

## Reliability and durable-edit release

- Cloud sync now uses a persisted `(received_at, id)` cursor instead of device timestamps.
  Tests cover delayed device timestamps, equal arrival times, mixed devices, pagination,
  process restart, malformed pages, and failed cycle processing without cursor advancement.
- A fresh cache recovered the actual 36 cloud test rows in pages `[7,7,7,7,7,1,0]`.
- The additive `durable_dashboard_edits` migration was applied to the hosted project.
  It reuses existing tables; no new service, dependency, or application table was added.
- Owner writes now use server-only, transactional RPCs. A live HTTP add/archive round trip
  survived a fresh local cache. The explicitly synthetic validation label remains archived.
- Manual insertion and label reassignment passed database assertions inside a transaction
  that was rolled back. No fake manual record was left in the shared dataset.
- Both `anon` and `authenticated` roles lack RPC execution privileges; only the server role
  can use dashboard edit RPCs. Security advisors show no warning/error findings; the
  ingestion-batch table's no-public-policy informational notice is intentional.
- Cloud edits require an owner session. Wrong passwords fail, lock/unlock works, and absent
  owner configuration fails closed. A separate random owner password was generated into the
  Git-ignored local `.env`; adding it to Streamlit Secrets still requires the owner.
- Firmware/device-derived provenance now distinguishes simulated, sensor, mixed, and unknown
  cycles. The actual cloud test cycle is `Simulated`; unknown legacy summaries remain
  unverified. Sensor mode alone is not calibration evidence. Only sensor-origin, normal,
  completed peers participate in duration comparisons; no model trainer exists yet.
- Final local regression run: **109 passed**, eight upstream Streamlit/NumPy deprecation
  warnings; Ruff clean. This is a fresh run, not the historical 93/96-test result.

Deployment checks: local UI tests and hosted database checks passed. Publish via the existing
GitHub/Streamlit path and inspect both hosted views. If either view fails or public write
controls appear without owner authorization, revert the release commit and redeploy; retain
the additive migration and data. No destructive database rollback is needed.

Known limits: the polling cursor is not a realtime stream or a general CDC system. Concurrent
transactions committing with an earlier arrival timestamp need separate stress testing if
multi-device concurrency grows. The shared owner password is a single-owner gate, not
individual user accounts. The real-data and tariff-calibration gaps above remain open.

## Progress checkpoint

Milestones 1–6 of the agreed ten-item software checklist are complete: 60%.
Real-device integration, real-data validation, Isolation Forest and reporting remain pending.
The software-only daily checker was recreated at 10:00 AM Asia/Kuala_Lumpur under automation
ID `daily-fyp-software-progress-checker`, targeting the FYP Software Development task.
