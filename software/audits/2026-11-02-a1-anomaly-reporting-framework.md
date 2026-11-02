# A1 — Isolation Forest and Codex reporting framework

Date: 2 November 2026, Asia/Kuala_Lumpur.
Base: `7dcb408` (verified U1–U3). Branch: `codex/a1-anomaly-reporting-framework`.
Verdict: **PASS for A1's implementation-framework / integration-contract scope.**
No real-data training, model-performance evaluation or live AI result is claimed.

## Actual implemented flow

```text
Local ingestion commits telemetry and detects completed cycles
  -> sends the existing telemetry receipt (report work cannot delay it)
  -> handle_completed_cycle, numerical-only by default
  -> existing cycle summary + saved raw trace -> validated cycle features
  -> optional explicitly configured, profile/device-scoped trained model
       absent/insufficient/test-only/unsuitable -> score unavailable
       eligible unseen sensor cycle -> raw decision, percentile, classification, comparisons
  -> existing immutable numerical report/history
  -> optional explicitly authorized provider via existing worker lock/quota/reservation
       unavailable/failure/uncertain outcome -> numerical report survives
  -> saved Codex review or durable held/failure state
  -> Streamlit reads saved metrics, plots, predictions and interpretation
```

The cloud path remains ESP32 -> Supabase -> SQLite cache -> cycle detection. A1
provides a reusable completed-cycle handler; the local development ingestion API
actually invokes it for completed cycles. No new Supabase webhook, listener daemon,
cloud execution service or always-on runtime was created. Cloud processing still
has the existing explicit laptop catch-up path. Connecting its future continuous
cycle-event runtime is a remaining deployment/integration task, not evidence of
an already-live cloud-triggered AI pipeline.

The existing Windows schedule was neither run nor reconfigured. It remains a
catch-up mechanism; batches of exactly 5/10/20 remain explicit user choices.
Public page views/refreshes never call a provider, fit a model or score raw data.
Numerical generation and scoring occur in generation/orchestration entry points.

## Reuse and changes

Reused:
- Cycle detector and CycleSummary, telemetry/provenance models, raw-reading lookup.
- SQLite/Supabase report save/history, deterministic hashes and identities.
- Existing numerical reports, electrical cards/plots and exact 5/10/20 selection.
- Existing Codex subprocess isolation, login checks, bounded ID-selection output.
- Existing shared worker lock, durable pre-call reservations, expiry and both
  usage-window guards; unknown quota or <=5% remaining still blocks AI.

New production module:
- `software/src/energy_monitor/anomaly.py`: feature/prediction/training contracts,
  extraction/quality gates, model fit/save/load, normalization and report evidence.

Modified production/configuration files:
- `software/src/energy_monitor/reporting.py`: optional model attachment and v4
  snapshots/rendering/validation; old default untrained v1–v3 history retained.
- `software/src/energy_monitor/report_narrative.py`: v4 source support, structured
  minimized model/measurement context and bounded comparison/hypothesis catalog.
- `software/src/energy_monitor/report_worker.py`: completed-cycle entry point and
  replaceable provider callable; explicit authorization before existing guards.
- `software/src/energy_monitor/api.py`: numerical-only completion hook after ACK.
- `software/dashboard/components.py`, `software/dashboard/app.py`: saved score,
  classification, provenance, comparisons and unavailable states; no page redesign.
- `software/pyproject.toml`: one necessary direct dependency, scikit-learn >=1.6,<2.
  Standard library alone and the previous dependencies did not implement Isolation
  Forest. SciPy/joblib/threadpoolctl and related packages are transitive dependencies.
- `software/README.md`: activation/trust/score/orchestration boundaries.

New test files:
- `software/tests/test_anomaly.py`
- `software/tests/test_a1_reporting.py`

No firmware file, database schema/table, authentication configuration, secret,
Windows task, backend deployment or OLED code was changed.

## Features and data-quality gates

The ordered v1 feature vector contains duration, meter energy, average/peak active
power, average current, voltage span, average power factor, frequency span,
sampling-gap count and invalid-reading fraction. Missing raw frequency is
unavailable, never invented. Raw frequency variation comes from saved readings.

Completed cycles require raw sample-count coverage, valid finite measurements in
existing telemetry bounds, timezone-aware increasing timestamps, no >7.5-second
raw interval, no counter rollback and no summary missing/invalid/gap counters.
A >20% meter/integrated-energy cross-check warning is excluded from model input.
Sensor-origin snapshots also require PZEM-mode trace provenance. These are
conservative engineering gates for this five-second profile, not physical
calibration evidence or final experimentally calibrated thresholds.

Production training selects only distinct, reviewed, normal, training-split,
sensor-origin, quality-eligible cycles from one profile/device. Held-out,
unreviewed, manual, simulated, mixed, unknown and unsuitable cycles are excluded.
Fewer than 30 eligible cycles, duplicate IDs or wholly constant vectors produce
an insufficient-data state, not a score. Thirty is a minimum software gate, not
proof that a dataset is statistically sufficient. Review attests comparable
conditions and measurement validity; provenance strings alone cannot prove it.

Training-only cycle IDs are retained, and production prediction refuses to
present those cycles as independent evaluations. Training artifacts include the
feature version/order, model version, training IDs/count/origins, review status,
held-out IDs, dataset manifest hash, baseline medians and reference decisions.
Test fitting requires `test_only=True`; such artifacts are blocked from normal
saved-report scores. `testing=True` predictions exist only for unit fixtures.
No production model or model artifact was fitted/saved from demonstration data.

The current gate is profile/device scoped. It does not infer water volume,
starting temperature or other operating conditions. Comparable-condition/load
labels and an evaluated baseline policy must be agreed before real activation.

## Score and classification contract

Isolation Forest uses 100 trees, random_state 42, n_jobs 1 and provisional
contamination 0.05. The raw decision is scikit-learn `decision_function`.
Classification is anomalous below zero and normal at/above zero. The provisional
5% threshold is an engineering choice awaiting held-out real-data evaluation;
normal does not mean proven healthy and anomalous does not mean faulty.

For decision `d` and training-reference decisions `r`:

```text
score = 100 * (count(r > d) + 0.5 * count(r == d)) / count(r)
```

The score is an empirical upper-tail unusualness percentile: lower raw normality
means higher score. It is bounded 0–100 and handles ties explicitly. It is NOT a
failure probability; classification is NOT score >50. A normal-only baseline
with a 5% threshold commonly has its anomaly cutoff near the upper tail, not at
50. Small datasets yield coarse percentile resolution and finite-baseline tails
saturate. The UI and saved report retain these qualifications.

Comparisons are measured values versus training medians plus absolute/percentage
differences. Zero-baseline percentage differences are unavailable. They describe
differences, not causal attribution or model feature-importance explanations.

Official implementation references:
- https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html
- https://scikit-learn.org/stable/model_persistence.html

## Persistence and activation boundary

A model is an atomically replaced local ZIP containing JSON metadata and a
pickled estimator. SHA-256 verifies payload corruption; metadata checks feature
schema, training provenance and matching Python major/minor + scikit-learn
version before use. Save models under immutable versioned filenames to retain
history; activation is a separate operator choice.

Pickle can execute code. Loading requires explicit `trusted_local=True`; an
integrity hash is not an authenticity signature. Never accept browser-uploaded,
emailed or otherwise untrusted model artifacts. Future artifact sharing needs an
independent authenticity mechanism or a safer format; A1 adds no upload flow.

`ENERGY_MONITOR_ANOMALY_MODEL` is an optional operator-owned trusted local path.
No such model was configured or activated by this work. Missing configuration
retains the old untrained report policy. An unreadable/incompatible configured
artifact yields unavailable scoring; it never silently trains a replacement.
Python/scikit-learn versions must match across training and deployed inference.

Programmatic use after a reviewed genuine dataset exists:

```python
features = extract_cycle_features(cycle_row, stored_readings)
# Construct TrainingCase objects from a separately reviewed, frozen split manifest.
model = train_model(reviewed_cases)  # test_only defaults to False
if model.status == "trained":
    model.save(versioned_local_path)
# Activate only after held-out evaluation and review; never fit from a page view.
```

## AI provider contract and safety

`provider(catalog) -> {"observations": [eligible IDs], "actions": [eligible IDs]}`
is the existing replaceable callable contract. The new v4 catalog includes a
structured `context` with measurements, model findings, relevant comparisons and
limitations. Device/cycle IDs, user notes/labels and raw traces stay out. Context
is bounded for a 20-cycle report; detailed comparisons focus on at most three
relevant cycles while all cycles retain compact findings/measurements.

Codex selects supported statements; Python supplies every number and displayed
sentence. Catalog statements distinguish measurements, learned-baseline findings,
hypotheses and practical checks. Potential explanations (load/start conditions,
control behaviour or measurement drift) are clearly unconfirmed possibilities,
not automated diagnoses. A future server provider can return the same bounded
selection contract; no API client/key/subscription or unrestricted text generation
was introduced. Real runtime authorization and provider availability are still
required; A1 verification calls only mocks, never live inference.

Every numerical report is saved before AI. Identical completion events reuse the
same evidence identity. Before any inference, the existing durable reservation
and shared state-directory lock prevent duplicate attempts. Saved failure results
are reused. Unknown/failed attempts remain held for human review; a process crash
never automatically authorizes an uncontrolled second call. Use the same state
directory for manual/event/scheduled entry points. Changed evidence/model versions
create new immutable analysis snapshots rather than rewriting saved reports.

## Verification performed

Test-first failures were observed for the absent anomaly module, unsupported
model-report argument, missing UI training state, missing completion-event report,
out-of-range raw frequency acceptance and score-bearing unavailable predictions.
Each was fixed and verified. A blocking-report test also demonstrated the initial
local ACK delay; moving report work after the receipt made the test pass.

Fresh final verification before this documentation checkpoint:
- Full regression run: **250 Python tests passed** (226 existing + 24 A1 tests),
  66.05 seconds.
- Additional focused test: **1 passed**, validating a scored 20-cycle fixture
  catalog with all comparison branches stays under the bounded provider input.
  Total verified coverage: **251 cases** (the final extra test was not presented
  as part of the earlier full-suite run).
- **8 existing upstream Streamlit/NumPy timedelta deprecation warnings**.
- Ruff: clean across `src`, `dashboard`, `tests`.
- `compileall` for `src`/`dashboard`: pass.
- `pip check`: no broken requirements.
- Installed test runtime: Python 3.13, scikit-learn 1.9.1; no cross-runtime
  deployment or different-Python execution was claimed.
- `git diff 7dcb408 -- software/firmware`: empty, preserving verified U1–U3.
- No ESP32 connection, flashing, mains action, live AI call, schema change,
  external deployment, push or merge was performed.

Coverage includes feature validation, insufficient training, unsuitable origins,
review/split/duplicate gates, test-only fit/inference, score direction/ties,
versioned persistence/trust/corruption, scoped predictions, saved model evidence,
mock provider success/failure/uncertainty, quota cutoff, duplicate events,
automatic numerical completion, ACK responsiveness, 5/10/20 exact batches,
legacy history, scored/unavailable saved UI and no inference on refresh.

## Acceptance and remaining work

| A1 requirement | Verdict / actual boundary |
|---|---|
| Isolation Forest framework and versioned prediction contract | PASS, fixture-tested; no real trained model |
| Validated features and genuine-training gates | PASS, conservative software gates; physical calibration still required |
| Model versioned save/load and unavailable states | PASS, trusted-local artifacts only |
| Structured interchangeable Codex provider interface | PASS, bounded catalog; mocked verification only |
| Completed-cycle numerical orchestration and history | PASS, local API event hook plus reusable cloud-runtime entry point |
| Duplicate/failure/interruption protection | PASS, existing guards retained and integration-tested |
| Exact manual comparisons and dashboard compatibility | PASS, 5/10/20 and legacy snapshots retained |
| Live cloud-triggered AI execution / laptop-off generation | NOT IMPLEMENTED/ACTIVATED; outside A1 framework verification |
| Real training, held-out model performance and fault diagnosis | NOT DONE; no score/accuracy credit |

Next checkpoint should be the reviewed genuine-cycle dataset and evaluation
protocol (A2), followed by the approved cycle-event executor/provider activation.
Keep OLED paused until its separate checkpoint is reviewed. Firmware U3 approval
is not inferred from this software-framework work.

For genuine PZEM training: first verify real end-to-end readings and calibration,
collect complete cycles under recorded comparable loads/starting conditions,
review normal labels and freeze a split by recording session/time. Keep held-out
normal and safely obtained unusual cases entirely out of fitting and threshold
selection. Train only the eligible normal training partition, evaluate false
alerts on held-out normal cycles and detection on the separately labelled cases,
record sample counts/uncertainty and tune the provisional threshold without
reusing the final test set. Store the frozen split/model/runtime versions and
review results before model activation. Do not intentionally create dangerous
appliance faults or infer a fault cause from a score.

Progress recommendation: the supplied unified software 71% / overall 76% remain
provisional. This checkpoint supports credit for the framework/contracts,
integration and automated-test rubric items only. The detailed unified weights
were not supplied, so no numerical increase is invented here. Central review can
map this evidence to its rubric. The independent software-only milestone tracker
remains 60%; real-data/ML evaluation and final reporting/documentation milestones
must not be marked complete merely because framework code now exists.


## Reproduction

From `software`, use its existing virtual environment and dashboard import path:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src') + [IO.Path]::PathSeparator + $PWD
./.venv/Scripts/python.exe -m pytest -o addopts='' -q --tb=short
./.venv/Scripts/ruff.exe check src dashboard tests
./.venv/Scripts/python.exe -m compileall -q src dashboard
./.venv/Scripts/python.exe -m pip check
```

Tests train only explicitly test-only fixture models and use mocked providers.
The scored UI/comparison examples are output doubles in temporary test stores,
not genuine sensor measurements or production model performance.
