# Dashboard MYT and provenance verification - 3 November 2026

Branch: codex/dashboard-myt-provenance. Base: b89a445.

## Scope and authority

User requested display-only Malaysian time, an investigation of contradictory live/cycle values, and a local commit. Follow-up explicitly approved publishing verified accumulated updates to GitHub main and Streamlit. User chose separate source labels rather than deleting simulated history. No mains operation, firmware edit/flash, credential change, backend/schema change, inference or model training was performed in this checkpoint.

## Verified cause

Read-only Supabase SELECTs on lxwogpxjfdckrfdicpyp returned 511 telemetry records. A local copy reproduced the public display exactly with the previous query implementation:

- Latest stream: sim-esp32-u3-20261102-a / tefal-kettle, pzem-fw-0.2.5.
- 102 sensor-mode records from 2026-11-03 04:55:18 UTC to 05:03:43 UTC. All had 0 W and a constant 0.001 kWh cumulative counter.
- Screenshot at 04:58:43 UTC matches a stored 244.9 V, 50 Hz, 0 W record received at 04:58:50.066218 UTC. Its correct display is 12:58:43 MYT.
- The 25-second, 0.017082 kWh active cycle belonged to sim-esp32-03b248, starting 20 October at 15:41:42 UTC. Its six samples were simulated.
- The old Live query selected the newest telemetry globally, but an active cycle globally and independently. Consequently one monitor's sensor-mode values appeared beside another monitor's old simulated cycle.
- The copied data produced 13 analytical cycles, all simulated; no sensor-mode load cycle was detected. Public appliance_cycles was empty: automatic cycles are reconstructed in SQLite, which is separate from durable raw telemetry.

The voltage/frequency are persisted cloud records, not demonstration-mode seeding. Firmware marks them pzem mode. The same device ID was previously used for sim-fw-0.2.4, and firmware-mode classification alone is not independent proof of physical acquisition, wiring, calibration or successful genuine kettle operation. No raw UART capture/reference measurement was obtained for that historical interval in this checkpoint. A value of 0 W cannot establish that an appliance was actually heating.

## Changes

- One ZoneInfo Asia/Kuala_Lumpur presentation helper handles aware timestamps and legacy UTC-naive inputs. UTC storage, schemas, APIs, calculations and saved evidence remain unchanged.
- Live command bar, cycle history/receipt, report selectors/coverage/reading tables consistently use MYT. Existing chart conversion is retained exactly once. Raw audit JSON is explicitly labelled UTC.
- Live readings, graph overlays and cycle totals match device, appliance profile and origin. Unrelated or mixed-origin cycles do not become current sensor cycles.
- Stale/offline views say Waiting for fresh readings and label values Last recorded. No energy is extrapolated. This does not establish whether ESP32, PZEM or Wi-Fi is off.
- Source filter: Sensor-mode records, Simulated test records, Manual entries, Unverified / mixed, All records. Hosted default is Sensor-mode records; simulation history remains available and intact. Cycle status and assessment filters remain independent.
- Changing source clears hidden selected cycle receipts. Saved reports follow the chosen source; mixed-source batch generation requires explicitly choosing All records. Individual and exact 5/10/20 reporting engines remain unchanged.
- Local snapshot, database and temporary verification files are ignored. Only credential-free summary evidence is committed.

## Verification

Nine new regression cases failed before the fixes and passed afterwards. Further tests cover source switching without deleting records, report dates/tables across midnight, unchanged UTC timestamps and chart times without a second offset. Existing sensor integration checks now correctly expect Last recorded labels for old fixtures.

Final verification:

- Full Python suite: 310 passed, 10 upstream Streamlit/NumPy deprecation warnings, 152.99 seconds.
- Additional source-filtered batch-generation guard test added afterwards: 1 passed, 38 deselected, 4.75 seconds. Total verified cases across both runs: 311.
- Ruff check software: clean. git diff --check: clean.
- Existing exact 5/10/20-cycle report regressions passed in the full run.
- Local browser: Live showed 13:03:43 MYT, zero last-recorded power, explicit reused monitor-ID caveat and Waiting for fresh readings; no simulated cycle card. Sensor-mode Cycles showed no matching cycles.
- Public pre-update browser independently reproduced Offline with Active load detected and the old simulated 0.01708 kWh card. Cloud count remained 511; latest stored sample remained 05:03:43 UTC.

Physical PZEM verification and a genuine loaded appliance cycle remain pending. No fresh firmware tests/build were necessary for this display-only diff; historical firmware results remain historical.

## Reproduction evidence

See 2026-11-03-dashboard-myt-provenance.json for the snapshot hash, before/after query result, analytical cycle identities and screenshot-matched record. Production was queried read-only; replay and cycle reconstruction occurred in an ignored local database only.
