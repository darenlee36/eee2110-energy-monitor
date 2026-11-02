# A3 — offline end-to-end integration rehearsal

Date: 2 November 2026, Asia/Kuala_Lumpur.
Base: `bd30942` (A2). Branch: `codex/a3-offline-integration-rehearsal`.
Verdict: **PASS for the requested offline integration scope.**
Freeze assessment: the implemented offline features are a candidate for the
8 November feature freeze. Installed unattended reporting is NOT signed off:
the existing Windows task has a broken configured Codex executable (details below).
Real sensor/model-performance/live-provider acceptance remains separate.

## Actual offline route verified

```text
Synthetic telemetry batches -> existing SQLite ingestion -> cycle detection
 -> A2 collection manifest and quality checks
 -> test-only model state / actual cycle-condition binding
 -> numerical report saved before provider work
 -> explicitly authorized mocked Codex selection under existing quota/reservations
 -> saved history -> fresh Python process -> Streamlit runtime display/rerun
```

The rehearsal fixture records 60 simulated normal cycles across six sessions,
reuses A2's chronological plan and fits only an explicitly test-only A1 model.
One further test uses a fresh localhost HTTP ingestion server/cache with the
fixture manifest/model configured temporarily. A full cycle produces one saved
context-bound numerical report; replay returns HTTP 200 and no duplicate report.
No real ESP32, PZEM, Supabase service or Codex inference was used in these checks.
The network boundary is tested with deterministic mocked failures/receipts.

## Integration defects fixed

1. **A2 context stopped before report generation.** Report generation and the
   completed-cycle handler now accept an A2 `PreparedDataset`. The optional
   operator path `ENERGY_MONITOR_DATASET_MANIFEST` provides the same manifest to
   existing owner-generation/local-ingestion/catch-up entry points. It is not
   configured in the user's environment or hosted secrets by this work.
2. **Scoring could not receive actual volume/condition context.** A report binds
   its cycle to the matching annotation, not the model's own scope. Source device/
   profile, UTC boundaries, A1 feature vector, raw-reading fingerprint, detector/
   firmware versions and quality counters must match the saved measurements.
   Missing/stale/unreviewed/excluded context blocks scoring but retains numerical
   evidence. Only a bound context is passed to A2-scoped prediction.
3. **Session annotation errors could label bad context as bound.** A future
   session start or inconsistent start times for the same device/profile/session
   now yield a mismatch fallback. Numerical reporting remains valid. The test
   first reproduced an incorrect `bound` status, then passed after the fix.
4. **A cloud failure could lose a newly generated numerical report locally.**
   Completed-cycle orchestration now caches that validated numerical report in
   SQLite if Supabase save confirmation fails. Result explicitly says
   `local_only_cloud_unconfirmed` / `network_unavailable`, and no provider call
   starts. Replay with the same evidence reuses the identity and confirms cloud
   storage after recovery, including a mocked commit-before-lost-receipt case.
   Direct Supabase save/authentication semantics are unchanged; owner permission
   failures are not bypassed by the fallback.
5. **Unavailable model diagnostics were too generic.** A configured model that
   cannot load now supplies a fixed warning in saved predictions. An unavailable
   manifest gets a separate unscored context fallback. Display never exposes the
   underlying path/error/credentials.

No services, tables, dependencies, reporting schedule, dashboard redesign,
firmware, backend deployment or production-model activation were introduced.

## Binding and saved display

The optional `cycle-context-v1` snapshot is an additive template-v4 field; existing
v1–v4/Codex history remains readable. No-context legacy generation keeps its old
policy. A2-bound models still fail closed when context is absent. Legacy A1
unscoped model interfaces remain compatible; production activation should use
reviewed A2-scoped artifacts and the collection protocol.

Saved report context includes:
- Cycle ID, source-manifest fingerprint/kind and actual session ID/start.
- Owner-recorded volume, starting-water band, operating condition and group.
- Actual device/profile, detector and firmware scope.
- Review state, fixed quality reasons/counters and raw/feature fingerprints.

Operator names, free-text notes and independent-label evidence remain in the
local dataset manifest and are not copied into public report context. Model/AI
input receives only compact safe condition/status fields, measurements and
existing bounded catalog IDs; it excludes session/device/cycle identifiers,
private notes, operator values and raw traces. The manifest hash is an integrity
link, not an authenticity or confidentiality certificate. Human annotations
remain human observations; no water volume is inferred from electrical readings.

Streamlit shows the recorded volume/session/conditions, explicit synthetic-source
qualification and context warnings. Actual scores stay Unavailable for the
rehearsal's test-only model. One separate temporary contract test injects a mocked
trained prediction to verify the scored path receives the actual matching scope;
no production estimator is fitted/saved/activated by that test.

Snapshots validate against their saved reading traces, so cached reports remain
readable after restart without invoking a model/provider. Opening/rerunning the
Reports page never initiates AI. Manual exact 5/10/20 reports retain independent
per-cycle context and existing electrical plots, totals and history.

## Duplicate, interruption and network behaviour

- Same readings/batch IDs replay without duplicate telemetry/cycles.
- Identical numerical evidence/context/model identity reuses the report ID.
- A second completion event after restart reuses the saved Codex review: one
  mocked provider call total. A fresh Python subprocess reads the same validated
  context/history from disk, followed by Streamlit startup/rerun.
- Provider missing/timeout/invalid selection preserves the numerical report and
  produces the existing explicit unavailable/failure state.
- An uncertain provider exception leaves the durable reservation held. Restart
  does not automatically make a second inference attempt.
- Offline numerical buffering does not claim remote confirmation. An explicit
  replay, or a recovered catch-up pass using the same configured manifest/model,
  can confirm it idempotently. The existing catch-up path must use the same
  manifest/model and shared reservation directory as event/manual entry points.
- Existing public cached-history fallback remains available during network loss.
- Public/read-only stores cannot gain write permission via the cache fallback.

Changed evidence or reviewed annotations create a new immutable analysis snapshot,
not an accidental duplicate of the old snapshot. No new outbox/retry daemon was
added, and no exactly-once guarantee for arbitrary independent machines/state
directories is asserted. Preserve reservation files and retain the laptop cache.

## Fresh verification

- **291 Python tests passed**, 131.01 seconds: 270 baseline/A2 + 21 A3 cases.
- **8 existing upstream Streamlit/NumPy timedelta deprecation warnings**.
- Ruff across src/dashboard/tests: clean.
- Python compileall for src/dashboard: pass.
- pip check: no broken requirements; no new dependencies.
- Git diff from A2 for `software/firmware`: empty; no firmware rerun was needed.

A3 checks cover full fixture cycle/context/report/display; fresh process/cache
recovery; duplicate events; exact 5/10/20 batches; stale measurement/quality/
cycle association; missing, unreviewed and inconsistent-session context;
configured model/manifest absence; scoped mocked prediction; provider absence,
timeout and malformed output; uncertain AI interruption; network outage and
commit-before-lost-receipt recovery; and preservation of owner permissions.

Only test-only synthetic estimators and explicit temporary prediction/provider
doubles were used. No synthetic production training, actual appliance anomaly
score, real model accuracy or physical measurements are claimed.

Reproduce from `software`:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src') + [IO.Path]::PathSeparator + $PWD
./.venv/Scripts/python.exe -m pytest tests/test_offline_rehearsal.py -q --tb=short
./.venv/Scripts/python.exe -m pytest -o addopts='' -q --tb=short
./.venv/Scripts/ruff.exe check src dashboard tests
./.venv/Scripts/python.exe -m compileall -q src dashboard
./.venv/Scripts/python.exe -m pip check
```

## Installed reporting task — remaining operational blocker

A targeted read-only native check on 2 November observed:
- `EnergyMonitor-CycleReporting` last native result: **2**.
- Recorded last run: **2 November 2026, 17:40:46 MYT**.
- Action executable exists, but its configured `--codex-executable` file does not.

This is the pre-existing installed launch-path fault, not a failure of the A3
mocked provider/report integration. No native task was run, repaired, disabled or
reconfigured here. Repairing/reactivating the existing inference-enabled task
could permit live Codex calls; that is outside this request's no-inference scope.
The launcher needs an approved repair and no-inference operational check before
unattended reporting can be called healthy. Do not rely on an old success JSON.

## Freeze readiness and remaining gates

| Area | State |
|---|---|
| Offline feature integration and regression gate | PASS; candidate for feature freeze |
| Model/context unavailable behaviour and simulated evidence boundaries | PASS |
| Existing manual comparisons/history/security safeguards | PASS |
| Installed unattended reporting launcher | BLOCKED as observed above; not changed |
| Genuine PZEM data, calibration, real held-out performance | Pending planned physical collection; no credit from fixtures |
| Real provider/live cloud operational acceptance | Not exercised in this offline checkpoint |
| Push/merge/deploy | Not performed; approval remains required |

Before the 8 November freeze: review this checkpoint and close the installed
launcher/no-inference operational check. Freeze the verified interfaces rather
than add features. Genuine collection/model evaluation follows the A2 protocol;
laptop-off generation and OLED remain outside A3. Code readiness is not a claim
of real anomaly-detection performance or a healthy installed AI schedule.

Independent software-only score remains **60%**. Unified software/overall values
remain the supplied provisional baseline pending central rubric review; this
checkpoint supports offline integration credit, not real-data/accuracy credit.

## Modified files

- `software/src/energy_monitor/dataset.py`
- `software/src/energy_monitor/anomaly.py`
- `software/src/energy_monitor/reporting.py`
- `software/src/energy_monitor/report_narrative.py`
- `software/src/energy_monitor/report_worker.py`
- `software/dashboard/components.py`
- `software/tests/test_offline_rehearsal.py` (new)
- `software/README.md`
- `software/audits/2026-11-02-a3-offline-integration.md` (this handover)
