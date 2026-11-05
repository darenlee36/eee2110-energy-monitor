# Energy Monitor — software closure handover

Prepared 5 November 2026. Feature-freeze target: Friday 6 November.
Slides start Saturday 7 November. Presentation expected 12–13 November.
Software milestone tracker: 70%; experimental models do not complete validation.

## Release boundaries

| Area | Checkpoint | Status |
|---|---|---|
| Production filtering | 71c37416fedb43d67be78c39c1be524214b4a183 | Published and hosted-verified |
| Cadence quality | 6d3bf66, codex/cadence-aware-quality | Tested, remote backup; separate deployment approval required |
| Baseline/IF workflow | 6a9064575f038e900610a0e13ea2547218a487ce, codex/pilot-baseline-expansion | Tested, remote backup; outside production |
| Firmware recovery | 4ae8620383088f92eea9c63f888f0cea2c947193, codex/firmware-retry-recovery | Local 0.2.8 candidate; NOT flashed |
| Last physically verified firmware | 5e14563, pzem-fw-0.2.7 | Historical real three-second telemetry; long-run reliability unproven |

## A — Published Cycles filtering

All dates is the temporal default. Today, Yesterday, Last 7 days (today plus
previous six MYT dates) and inclusive custom calendar dates use recorded cycle
start time in Asia/Kuala_Lumpur. UTC storage remains unchanged; naive legacy
timestamps are interpreted as UTC.

Optional minute-level start-time window includes its start and excludes its end.
Overnight/equal endpoints show a clear restriction; use whole day or separate
same-day searches. Filters combine with status, label and existing source choice.
Reset clears temporal/status/label filters AND selects All records. It does not
change annotations. Source default remains the existing Sensor-mode records.

History query removes the old 100/10,000 truncations, reads metadata without raw
cycle traces, orders newest first and reuses the tariff catalog during list
construction. Selected details remain if the cycle still matches. A new result-
set table identity prevents stale row offsets selecting the wrong cycle.

Verification: 175 relevant distinct checks passed across the broad/targeted runs,
including 16 new filtering checks; Ruff clean. A test fixture's duplicate unique
sequence was corrected before release. Local and hosted 1440/390 CSS-pixel checks
showed no document overflow. The table retains intentional internal scrolling.
Hosted example: 5 Nov, 13:00–15:00 MYT, Normal + Complete = exactly six cycles.
Default sensor history = eight; Reset/All records exposes 21 available cache
cycles across sources. These display counts are not training eligibility.
Streamlit required a managed reboot to refresh imported query functions.

Rollback: ordinary revert of filtering release to a4b304a and managed restart;
never force-push or delete cloud records.

## B — Cadence correction, awaiting separate release approval

Five-second missing-slot arithmetic and fixed 7.5-second chart/feature checks
missed six-second gaps in three-second streams. Shared cadence/sequence evidence
now serves cycle quality, raw feature validation and report/live charts.
Known schedules use firmware metadata; unknown versions require repeated
consistent observed cadence. Arrival latency is separate from measurement gaps.
Historical snapshots without metadata retain compatible presentation fallback.
Power thresholds, segmentation times, energy arithmetic and saved reports remain.

371 full Python tests passed; Ruff clean; ten upstream deprecation warnings.
Frozen six-cycle replay: 431 readings, every summary field unchanged. Details:
audits/2026-11-05-cadence-quality.md and 2026-11-05-cadence-impact.json.
Genuinely gapped records can receive corrected quality counts after a future
approved recomputation. No production recomputation or migration was performed.

## C — Versioned baseline and Isolation Forest

72 workflow/pilot/dataset/anomaly checks passed; Ruff clean. Development branch
backed up normally, not merged. V1 remains pilot-deviation-v1-f1f5564e68e2.
New reviewed eligible cycles receive immutable v1 assessments before explicit v2
selection. Only independently reviewed normal training sessions may expand the
reference; validation/test sessions stay out. Duplicate saves reuse matching
evidence and reject conflicts. Artifacts load and unseen-cycle prediction passes.

Local IF uses duration + actual sampled mean power, 200 trees, seed 42, one vector
per cycle. Six to ten cycles are a prototype, not validated detection. Main ML
assessment remains Unavailable / Not Evaluated. Existing table assessment Normal
is a rule-based cycle assessment, not an independently validated IF label.
Public Complete annotations are not independently reviewed model-training labels.
V1 scores: 35.4, 5.2, 5.9, 20.5, 57.1, 16.5. Medians: 220.5 s, 0.112 kWh,
1801.30 W. Exact original volumes and temperatures remain unknown.

## Added firmware investigation

User confirmed the concern is the earlier recurring fault, not a new current
outage. Fresh server review ends at measurement 15:04:55 / receipt 15:05:15 MYT.
The returned log window contains one earlier 503 and no requests after 15:05.
Server evidence cannot identify faults that prevent the device reaching it.

A reproduced recovery-delay issue permits a 60-second retry pause, longer than
45-second freshness. Local 0.2.8 caps transient transport/5xx backoff at ten
seconds, retains 60 seconds for rejections/rate limits and adds retry-delay logs.
29 native tests and simulated/real PZEM OLED builds passed. No physical update.
This reduces scheduled recovery delay; initiating transport/heap/sensor fault is
still unconfirmed. More failed radio attempts are the trade-off. Do not reset
merely to clear OLED waiting; the 120-reading RAM queue is not power-loss storage.
See the separate firmware audit for evidence and flash/rollback requirements.

## Friday physical checklist — user participation, no automatic testing

1. Use the existing approved assembly/supervision procedure; no wiring changes.
   Confirm Wi-Fi and genuine voltage/current/power/energy/Hz/PF, current firmware,
   synchronized time and timestamps. Record OLED status.
2. Check idle -> ordinary phone charging -> idle: Ready -> Monitoring -> Ready
   on Live, with fresh acknowledged readings and responsive OLED.
3. During an approved >=15-minute observation, capture request duration/status,
   ACK batch IDs, TLS/RSSI diagnostics, queue/pending depth, retry delay and heap.
   A brief Wi-Fi interruption/recovery must retain/drain exact sequences without
   duplicate records. Capture pending RAM evidence before any approved shutdown.
4. If 0.2.8 is approved later, flash only with isolated USB under the established
   procedure. Re-verify genuine three-second cadence before whitelisting it in
   the collection workflow. Current 0.2.6/0.2.7 collection remains supported.
5. If safe/feasible, collect four additional normal kettle cycles. Record exact
   volume, observed starting conditions, genuine session ID/start, cycle ID,
   MYT timestamp, energy, duration, independent review and every quality problem.
   Do not fabricate independence by renaming adjacent cycles as sessions.
6. Verify each cycle finalizes after its post-load tail, persists with qualified
   cost, and has a retrievable numerical report after fresh-cache/app restart.
7. Score eligible new cycles against frozen v1 first. Keep unknown labels unknown;
   reserve genuinely later sessions for evaluation rather than fitting them.
8. Freeze the demonstrated build and limitations; start slides Saturday. If
   collection is limited, present the six-cycle pilot honestly instead of
   lowering validation gates or manufacturing accuracy.

## Architecture and presentation evidence

PZEM -> level-converted UART2 (RX14/TX27, 9600) -> independent ESP32 sampling +
bounded RAM queue -> Wi-Fi/HTTPS authenticated Edge ingestion -> durable Supabase
telemetry/cycle/cost/report tables -> SQLite analytical cache -> Streamlit.
OLED reflects sensor/connectivity/fresh ACK status. The deterministic six-cycle
preview is separate from the local two-feature IF experiment and A2 review/split.

Local closure-evidence folder contains genuine hosted filter desktop/mobile
captures, current Live Offline capture, saved Reports preview, six-row CSV,
standalone comparison chart/vector figure and architecture SVG. build_evidence.py
rebuilds charts/table from the approved public reference. Live Offline is a
current stored-data view, not evidence of a fresh physical test. All numerical
figures derive from the six genuine cycle summaries; no synthetic model accuracy.

Before/after production content hashes match: 3,344 readings, eight persisted
cycles and 17 reports, plus unchanged annotation and cost content. No security,
permissions, secrets or raw telemetry were changed by this software work.
At nominal one request per 3 seconds, continuous operation permits about 28,800
new-reading requests/day before retries; actual usage depends on operating time
and recovery batching. No claim of unlimited cloud quota is made.

Remaining release gates: cadence deployment approval; separately approved firmware
flash/physical retry verification; reviewed new data; independently held-out ML
evaluation. Costs remain qualified while AFA/household context is unresolved.
Calibration/raw Modbus provenance and long-duration reliability are separate
limitations. Slides can use the verified software now with these limits visible.
