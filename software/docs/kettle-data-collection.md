# A2 kettle-cycle collection and evaluation — practical field sheet

Prepared 2 November 2026. Software feature-freeze target: Sunday, 8 November 2026.
This is a future collection procedure, not evidence that physical tests or real
model evaluation have happened. No hardware connection is required to prepare it.

## First objective: one dependable normal baseline

Use one meter, one kettle profile, one firmware/detector version and one recorded
set of operating conditions. Pick one primary water quantity inside the actual
kettle's MIN/MAX marks; 1,000 mL is an example only if the model permits it.
Record the exact volume rather than asking a model to guess it.

| Stage | Recommended eligible cycles | Purpose |
|---|---:|---|
| Pilot | 6–10, kept in a separate pilot group | Verify cycle boundaries, readings, quantities, timing, cache and annotation workflow. |
| First three baseline sessions | 10 + 10 + 10 = 30 | Normal training partition. A1 needs at least 30 distinct eligible reviewed normal cycles. |
| Fourth baseline session | 10 | Validation only: review false alerts and candidate thresholds. |
| Fifth and sixth baseline sessions | 10 + 10 = 20 | Final held-out normal test; do not tune on these results. |
| Optional other quantities | 5–10 at each of two other permitted levels | Characterize expected load changes; separate baseline groups, not fake anomalies. |

The main target is **60 eligible normal baseline cycles across six genuine
recording sessions**. Plan a few spare recorded cycles because exclusions may
reduce the usable count. Thirty eligible training cycles is an engineering gate,
not a statistical guarantee. If only a smaller pilot is practical, retain it and
report insufficient data; do not quietly weaken the split after seeing results.
Sixty cycles require several hours including filling, normal cooling/readiness
and logging. Spread sessions across suitable lab slots/days; do not rush cooling
or invent extra sessions by renaming neighbouring cycles.

Other example quantities are 500 and 1,500 mL **only if both lie inside this exact
kettle's permitted markings**. Different water quantities normally change energy
and duration. They are not ground-truth anomalies. A 5-cycle exploratory volume
group is not enough to fit a separate production baseline under the current gate.

## Before the first physical session next week

Use the already approved assembled meter and ordinary kettle operation under the
agreed lab/supervision procedure. This sheet does not authorise mains wiring or
hardware changes. Confirm the actual kettle model/manual and fill limits, the
measurement-check/calibration record, stable five-second telemetry, synchronized
time, an agreed cycle-detection version and adequate cloud/cache coverage.
Record any unresolved measurement/calibration issue; sensor-origin firmware alone
is not evidence of calibration. Resolve the pilot before trusting training data.

Use a measuring jug/scale appropriate to the lab protocol for the actual water
quantity. If temperature cannot be measured, leave numeric temperature cells
blank and record only an observed qualitative band; do not invent degrees.
Record a consistent normal rest/readiness policy in notes. A substantially
changed rest/body-temperature policy belongs in a separately named group.

## One ordinary cycle, step by step

1. Start a real recording session, e.g. `S01`. Record its start as an ISO timestamp
   with timezone, e.g. `2026-11-09T09:00:00+08:00`. Reuse that exact ID/start for
   the session's cycles. A session is one genuine recording period, not one row.
2. With the kettle in its normal unpowered filling state and removed from its
   base as directed by its manual, measure permitted clean water. Stay between
   the actual MIN/MAX marks. Keep external/base electrical areas dry.
3. Record volume, observed starting-water band and normal operating condition.
   A baseline example is `water_volume_ml=1000`, `start_temperature_band=room`,
   `operating_condition=standard_boil`, `baseline_group=baseline-1000-room`.
   Use the same kettle/profile and normal lid/automatic shut-off operation.
4. Collect a short idle lead-in, run one normal boil, and leave idle telemetry
   running afterward until the detector actually records a completed cycle.
   Roughly 15–30 seconds of idle before/after is a planning allowance; the saved
   cycle status, rather than the stopwatch alone, determines completion.
5. Copy the completed cycle UUID from saved cycle/report technical details or an
   export into the collection CSV. Do not manufacture a UUID or type electrical
   averages by hand. The tool joins the actual device/profile, UTC boundaries,
   electrical features and quality counters from the existing SQLite cache.
6. Note automatic stop, spills/interruption, unusual observed behaviour, changed
   starting conditions and any connection/data issue. Review independently of
   the model output. Mark `reviewed=TRUE`, `label=normal`,
   `label_basis=normal_observation` and your operator ID only when normal operation
   and data checks justify that label. Unknown/unreviewed is an acceptable state.
7. Follow the kettle's normal cooling/readiness instructions before another
   cycle. Record changed conditions; do not force rapid repeated switching.
   Repeat without changing the chosen group conditions.
8. At session end, wait for cycle completion, then use the existing controlled
   `flush` mechanism and await acknowledged drain if shutting down. Firmware
   flush does not itself prove cycle completion. Retain raw cloud data and a
   consistent SQLite backup/snapshot; do not blindly copy an actively written
   SQLite/WAL database. No reporting/AI run is required to collect evidence.

Safe planned variations are ordinary permitted water quantities and, if allowed
by the actual kettle instructions, normal reboils with separately recorded warm
starting conditions/group. Never run dry or below MIN, overfill, defeat the lid
or cut-out, obstruct operation, alter the supply or intentionally create a fault.
Stop a run under the agreed lab procedure if unsafe behaviour occurs. Do not
repeat it to obtain an anomaly sample.

Manufacturer basis: [Tefal Malaysia's kettle FAQs](https://www.tefal.com.my/pages/product-faqs)
state that the kettle's minimum/maximum markings must be respected. The exact
model's own manual takes precedence; no specific model capacity is asserted here.

## Blank CSV — what you enter

Use `../templates/kettle-cycle-collection.csv` (header only, no fabricated rows).
Open a copy in Excel/LibreOffice and keep column names/order unchanged. Treat
UUIDs, session IDs and ISO timestamps as text. One row refers to one completed
cycle, not one five-second reading and not one upload batch.

Required annotation columns:
- `cycle_id`: saved cycle UUID from the real software records.
- `session_id`, `session_started_at`: genuine recording period, timezone required.
- `water_volume_ml`: actual measured quantity; no inferred/modelled volume.
- `start_temperature_band`: `room`, `cool`, `warm` or `unknown`.
- `operating_condition`: `standard_boil`, `normal_reboil`, `other` or `unknown`.
- `baseline_group`: a simple identifier for comparable runs; letters/numbers/_/-.

Optional/review columns:
- `reviewed`: TRUE/FALSE; blank defaults FALSE.
- `label`: `normal`, `unusual`, `unknown`; blank defaults unknown.
- `label_basis`: `normal_observation`, `independent_observation`, `model_only`,
  `unknown`; blank defaults unknown.
- `label_evidence`: independent observation or external reference, not an AI verdict.
- `operator`: reviewer identifier; required for a reviewed non-unknown label.
- `notes`: deviations/rest policy/environment/context. No credentials.
- `water_start_temperature_c`, `ambient_temperature_c`: measured values only;
  blank when unmeasured. `label_verified_at`: optional timezone-aware review time.

An `unusual` label counts toward detection evaluation only when reviewed, attributed
to an operator and supported by an independent observation. Model-only labels
cannot become evaluation ground truth. Different normal volumes/start conditions
must not be relabelled as faulty merely because the score is high. Communication
gaps are measurement-quality exclusions, not proof of a faulty kettle.

## Dataset fields supplied automatically

The manifest links annotations to device/profile IDs, cycle UTC start/end,
A1 feature-schema version, raw-trace fingerprint, firmware/detection versions,
source provenance and quality counters. The ordered model vector reuses A1:

`duration_seconds`, `energy_kwh`, `average_power_w`, `peak_power_w`,
`average_current_a`, `voltage_span_v`, `average_power_factor`,
`frequency_span_hz`, `sampling_gap_count`, `invalid_fraction`.

The exported `cycle-features.csv` keeps every annotation row, including unsuitable
records, reasons and out-of-scope groups. Missing measurements/counters stay blank;
they are not filled with guessed zeroes. Existing A1 gates identify incomplete
cycles, invalid/raw-count mismatches, missing samples, time gaps, non-finite or
out-of-range readings, counter resets and energy cross-check warnings. Sensor
training also needs PZEM trace provenance. Review/labels are separate from quality:
a valid unlabelled cycle can be scored for exploration but is not counted as
verified normal/unusual ground truth.

The recorded raw-trace hash detects accidental changes; it is not an authenticity
signature or calibration certificate. Keep raw telemetry alongside the manifest.

## Preparing data offline — no model training

From `software`, with the existing virtual environment:

```powershell
$env:PYTHONPATH = Join-Path $PWD 'src'
./.venv/Scripts/python.exe -m energy_monitor.dataset template --output data/my-collection.csv
./.venv/Scripts/python.exe -m energy_monitor.dataset prepare --database data/collection-cache.db --collection data/my-collection.csv --baseline-group baseline-1000-room --output data/study-freeze-01
```

Use the real cache/snapshot path; this command does not sync Supabase itself.
The template output must not already exist. Preparation output must be a new
folder, preserving old freezes. The preparation command writes:
- `dataset-manifest.json`: annotations, frozen features, provenance and exclusions.
- `cycle-features.csv`: human-readable joined review/export sheet.
- `preparation-status.json`: counts and split readiness, never a claimed model result.
- `split-plan.json` only when enough independent comparable sessions exist.

Pilot data still gets a quality review/export even when a valid split cannot yet
be made. Different volumes, operating conditions, devices, detector or firmware
versions cannot share one baseline group/split; correct grouping explicitly.
An unreviewed/synthetic/manual cycle cannot quietly become genuine training data.

## Freeze and leakage prevention

Default: first three chronological sessions are training, fourth is validation,
all remaining later sessions are final test. At least one final session is needed.
A session never crosses partitions. Overlapping/inconsistent session times,
duplicate cycle IDs and changed manifest/split hashes are rejected. Rows from other
baseline groups remain visible as out-of-scope and are not false anomalies.

Freeze collection notes, independent labels, quality decisions and split before
fitting. Hashes cover the complete manifest; shuffling CSV rows does not change
the split. Later changes require an explicit new freeze. Once final-test results
have been inspected, changing labels/conditions/thresholds does not turn the same
records into a fresh independent test; collect a new held-out session if necessary.

A1 receives only normal reviewed training-partition cycles for fitting. Validation
and final-test cases map to A1's held-out role and never enter fitted trees,
normalization reference decisions or baseline medians. Model artifacts bind the
manifest/split hashes, training cycle IDs/sessions and exact collection scope.
A2 evaluation checks those identities again before scoring. These controls follow
[scikit-learn's separation of training, validation and test data](https://scikit-learn.org/stable/modules/cross_validation.html)
and [data-leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).

## Training and evaluating later, after genuine measurement review

A2 provides `training_cases`, `fit_prepared` and `evaluate_partition` on top of A1.
`fit_prepared(..., test_only=False)` is a future explicit programmatic action for
a reviewed genuine sensor manifest; it is not run by preparation or page views.
Verification uses only `test_only=True` plus `synthetic_fixture` datasets.
No production fit command, model activation or AI call was added.

An A2-scoped model requires matching collection context even in A1 prediction;
without that context it returns unavailable/scope mismatch. Ordinary report
scoring therefore cannot silently apply a 1 L baseline to a 0.5 L or unlabelled run.
A later approved report-context integration is needed before live activation.

For an existing reviewed, trusted local model artifact:

```powershell
./.venv/Scripts/python.exe -m energy_monitor.dataset review --bundle data/study-freeze-01 --model data/models/REVIEWED-MODEL.zip --trust-local-model --thresholds -0.05 0 0.05 --output data/validation-review-01
./.venv/Scripts/python.exe -m energy_monitor.dataset evaluate --bundle data/study-freeze-01 --model data/models/REVIEWED-MODEL.zip --trust-local-model --partition test --raw-threshold 0 --output data/final-evaluation-01
```

The example thresholds illustrate the review interface; they are not calibrated
recommendations. `review` evaluates validation sessions only and never changes
model parameters. Select and record a threshold/acceptance rule using validation,
then run the final test with that fixed rule. The tool records every requested
threshold; it cannot prevent a person from repeatedly peeking at a test set.
Do not choose the best-looking threshold from the final test. Existing A1 live
classification remains raw decision <0; a nonzero evaluation threshold is an
analysis comparison, not a deployed-policy change.

Evaluation writes `evaluation.json` plus `evaluation-cycles.csv` with score,
raw decision, independent label, session and reviewed-threshold flag. JSON includes
counts, score histograms/quantiles and:
- Normal false-alert rate = flagged eligible verified-normal / eligible verified-normal.
- Unusual detection rate = flagged eligible independently verified-unusual /
  eligible independently verified-unusual.
- Unknown/model-only labels never enter either truth denominator.
- With zero verified unusual cases, detection is unavailable, not 0% or 100%.
- Wilson 95% intervals accompany proportions, with sample-size/correlation caveats.

For example, even zero false alerts among 20 independent normal tests has an
approximately 16.1% upper Wilson bound; it does not establish perfect reliability.
Repeated same-session cycles may be correlated, so the binomial interval itself
is only descriptive, not a guarantee. The [NIST reference](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)
describes the Wilson proportion interval used here. No physical performance result
is claimed by this numerical illustration.

## Freeze plan and next checkpoint

Complete A2 review now. Before Sunday, 8 November, the highest-priority offline
checkpoint is A3: rehearse labelled fixture replay through cache/cycles/dataset/
evaluation/saved reports, then wire owner-reviewed collection context into report
scoring while retaining the current fail-closed guard. This can be done without
PZEM readings or live AI. Freeze interfaces/test procedures on Sunday; use next
week for real measurement checks, collection and evaluation, not dashboard redesign.
