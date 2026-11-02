# A2 — Dataset collection and Isolation Forest evaluation preparation

Date: 2 November 2026, Asia/Kuala_Lumpur.
Base: `ef5f81c` (A1). Branch: `codex/a2-dataset-evaluation-preparation`.
Verdict: **PASS for the requested offline preparation/workflow scope.**
No genuine PZEM data, production fit/activation, paid/live AI inference, hardware
connection, mains action, OLED development, push, merge or deployment occurred.
Feature-freeze target remains Sunday, 8 November 2026.

## Delivered artifacts

- `software/templates/kettle-cycle-collection.csv`: blank 16-column CSV, zero data
  rows. Required measurement/annotation fields and optional label/review fields
  are explained in the procedure; electrical features are joined automatically.
- `software/docs/kettle-data-collection.md`: usable step-by-step collection sheet,
  session targets, safe normal variations, data dictionary, CLI examples,
  independent-label rules, freeze/split/evaluation procedure and limitations.
- `software/src/energy_monitor/dataset.py`: one offline module/CLI reusing A1,
  existing models and the SQLite store; no new dependency/service/table.
- `software/tests/test_dataset.py`: 19 synthetic-only preparation/evaluation tests.

Small compatibility changes:
- `software/src/energy_monitor/anomaly.py`: optional `collection_scope` argument;
  an A2-scoped model fails closed without the matching operating-condition scope.
  Existing A1 models/callers without this metadata retain their existing contract.
- `software/dashboard/components.py`: scope-mismatch explanation now covers
  recorded operating conditions as well as device/profile.
- `software/README.md`: A2 entry points, artifacts and genuine-data boundaries.
- This audit is the local technical handover.

Existing firmware, detector, Supabase, report history, authentication, scheduling,
reporting providers, dependency declarations and dashboard layout are preserved.

## Workflow and structure

```text
Blank operator CSV + existing SQLite cycle/raw-reading cache
 -> prepare_dataset -> A1 feature/quality extraction + annotation validation
 -> dataset manifest + full feature/quality review CSV
 -> make_split: chronological whole sessions, one comparable baseline scope
 -> frozen dataset/split hashes
 -> future explicit fit_prepared (A1 normal-only fitting; never automatic)
 -> existing trusted local model + evaluate_partition/review_thresholds
 -> JSON/CSV results, score distributions and explicit unavailable rates
```

Annotations contain cycle UUID, session ID/start, water volume, starting-water
band, normal operating condition, baseline-group identifier, optional measured
temperatures, reviewer/operator/time, optional label/basis/evidence and notes.
Derived fields include device/profile, actual UTC cycle boundaries, all A1
features/quality counters, data origin, firmware/detector versions and raw-trace
fingerprint. A fingerprint detects accidental change; it is not calibration or
authenticity proof. Numeric input bounds are sanity checks, not permission to
exceed the actual kettle's MIN/MAX marks.

Unknown labels/temperatures stay unknown; electrical values are not manually
filled in the CSV. Reviewed unusual labels need an operator and independent
observation. Model-only or unreviewed labels cannot enter truth denominators.
Labels curate normal training data/evaluation; Isolation Forest still fits only
its unsupervised feature matrix, not a supervised target vector.

Incomplete, missing, gap-affected, invalid, non-finite, out-of-range, counter-reset
or otherwise unsuitable records remain visible with reasons. A1 extraction and
quality gates are reused. In genuine mode, simulated/manual/unverified origin is
excluded. A pilot bundle still writes the review sheet/manifest when too few
sessions exist to create a split; it does not claim a ready evaluation.

## Practical collection target

1. Separate 6–10-cycle pilot, checking boundaries, timing, measurement validity,
   cache and annotation workflow; no model-performance claim.
2. One initial fixed-volume baseline: 60 eligible reviewed normal cycles across
   six genuine sessions: 30 training (first 3 x 10), 10 validation (fourth),
   20 final test (last 2 x 10). Allow spare recorded cycles for exclusions.
3. Optional 5–10 normal cycles at each of two other permitted water levels,
   separate groups. Examples 500/1,000/1,500 mL depend on the actual kettle's marks;
   its exact capacity was not assumed. Larger normal boils are not anomalies.

This is a practical target, not a guarantee of statistical sufficiency or accuracy.
It takes several hours including ordinary refill/rest/readiness over multiple
sessions. Never create a fault, bypass controls, run dry, overfill or rush unsafe
operation for a label. Genuine unusual cases are optional; absence means detection
performance is unavailable. The exact kettle manual and approved lab procedure
remain prerequisites for next week's physical work.

## Split and leakage controls

Default split: first three sessions training, next session validation, remaining
later sessions final test. IDs are grouped before assignment; no session crosses
roles. Session start/end overlap and inconsistent starts are rejected. Record row
order does not change dataset digest or split. Duplicate cycle IDs are rejected.

A baseline scope fixes device/profile, measured volume, starting-water band,
operating condition, group, detector version and firmware version set. Other
groups are retained as out-of-scope. Mixing these conditions in the same group
is an explicit error rather than a hidden model anomaly.

The model binds dataset/split hashes, training IDs/sessions and collection scope.
Evaluation verifies the exact eligible reviewed-normal training identities and
rejects held-out leakage. A2-scoped prediction requires matching context; live
report calls without owner-reviewed context remain unavailable. A future A3
integration must derive context from the actual cycle/collection record, not
blindly pass the model's own stored scope back to itself.

Hashes freeze features, quality decisions and annotation labels. Later edits
require a new explicit freeze. Looking at final-test results and then changing a
threshold/label does not make the same test set independent again. Quality review
and independent labels should be frozen before fitting. Hashing held-out data for
integrity does not feed those values into fitting/reference medians/normalization.

## Evaluation outputs and limitations

- Evaluation is validation/test only; training-partition evaluation is rejected.
- Normal false-alert numerator/denominator/rate and descriptive Wilson 95% bound.
- Separately verified-unusual detection numerator/denominator/rate/bound.
- Unknown/model-only labels never contribute to either truth denominator.
- No verified unusual examples -> denominator 0, rate/interval unavailable.
- Per-label score count/minimum/median/95th percentile/maximum and ten-bin histogram.
- Per-cycle CSV records UUID/session, independent label, score, raw decision,
  reviewed-threshold flag and the unchanged A1 classification at raw zero.
- Missing/unfitted model -> explicit unavailable result, not fabricated metrics.

`review_thresholds` and CLI `review` use validation sessions only. Candidate raw
thresholds are recorded; nothing is automatically chosen or activated. Select
and document an acceptance rule on validation before the final test. The CLI
cannot prevent a human from repeatedly inspecting test outcomes, so the frozen
protocol remains necessary. Nonzero evaluation thresholds do not change A1's
production decision threshold or report classifications.

The Wilson calculation follows the NIST method. For illustration only, 0/20
false alerts still has an upper 95% Wilson bound of approximately 16.1% under
binomial assumptions. Repeated session cycles can be correlated; this bound is
descriptive and does not prove population reliability. Synthetic fixture rates
are plumbing tests, never real appliance/model performance results.

## CLI and persistence

Commands: `template`, `prepare`, `review`, `evaluate`. No training or activation
CLI command was added. `fit_prepared(..., test_only=...)` is an explicit future
programmatic action; fixture datasets cannot use production mode.

Preparation reads an existing local cache; it does not fetch cloud credentials,
run sync or contact hardware. Output directories must be new, preserving previous
freezes. Template creation refuses to overwrite an existing file. Model loading
retains A1's explicit trusted-local pickle boundary and matching-runtime checks.

Artifacts:
- Preparation: `dataset-manifest.json`, `cycle-features.csv`,
  `preparation-status.json`; `split-plan.json` when ready.
- Validation review: `validation-threshold-review.json`.
- Evaluation: `evaluation.json`, `evaluation-cycles.csv`.

## Verification — fresh 2 November 2026

- **270 Python tests passed** (251 A1/baseline cases + 19 A2 cases), 72.29 seconds.
- **8 existing upstream Streamlit/NumPy timedelta deprecation warnings**.
- Ruff across src/dashboard/tests: clean.
- Python compileall for src/dashboard: pass.
- pip check: no broken requirements; **no new dependency** for A2.
- CLI `--help`: lists the four offline commands successfully.
- Checked-in CSV: 16 columns, zero measurement rows.
- Git diff against A1 for `software/firmware`: empty.

Test-first failures were observed for the absent dataset module and missing CLI.
Fixtures exercise CSV round-trip/invalid review flags/non-overwrite, pilot output,
raw-quality and missing-cycle exclusions, volume/detector scope mismatch,
chronological/nonoverlapping session grouping, frozen-hash tampering, duplicate
IDs, held-out/training leakage, test-only fitting/production rejection,
unknown/model-only versus independently verified labels, unavailable models,
false-alert/detection denominators, distributions, threshold-review separation,
scoped artifact load/evaluation and Wilson-bound sanity. Existing full-suite A1
report/provider/guard/dashboard regressions remain passing.

The A2 CLI integration test fits an explicitly test-only model from synthetic
features, saves it only in a temporary test directory and verifies exports.
No fixture result was promoted into a genuine dataset, production model or UI
health score. No physical measurements or real detection accuracy were invented.

Reproduce from software:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src') + [IO.Path]::PathSeparator + $PWD
./.venv/Scripts/python.exe -m pytest -o addopts='' -q --tb=short
./.venv/Scripts/ruff.exe check src dashboard tests
./.venv/Scripts/python.exe -m compileall -q src dashboard
./.venv/Scripts/python.exe -m pip check
./.venv/Scripts/python.exe -m energy_monitor.dataset --help
```

## Acceptance / remaining dependencies

| Requested item | Result |
|---|---|
| Structured collection metadata/features/provenance | PASS, CSV plus validated joined manifest |
| Practical normal/volume/condition protocol and targets | PASS, future standard-operation procedure only |
| Reuse A1 validation and expose unsuitable records | PASS |
| Reproducible session/time split and leakage checks | PASS |
| Evaluation/FAR/verified unusual/distributions/threshold review | PASS as implemented workflow; real results not available |
| Usable collection template | PASS, blank CSV; no fake readings |
| Clearly synthetic verification, no production activation | PASS |

Next week's dependencies: actual kettle model/fill limits, reviewed measurement
checks and calibrated boundaries, genuine complete PZEM cycles, recorded
conditions and independent labels. A final alert-threshold acceptance rule needs
review using validation evidence; it cannot be certified from synthetic fixtures.
No real model accuracy or final-report validation milestone is complete.

Highest-priority offline checkpoint: **A3 — end-to-end collection/report handoff
rehearsal and owner-reviewed cohort-context binding**, using clearly labelled
fixtures and no live AI. Verify replay/restart/exports and context-mismatch
unavailability before Sunday, then freeze interfaces on 8 November. This prepares
next week for measurement/collection/evaluation rather than new architecture.
OLED remains outside this checkpoint.

Progress: independent software-only tracker stays **60%**; unified software 71%
and overall 76% remain the supplied provisional baseline until central rubric
review assigns preparation/workflow credit. Do not award real-data training,
model accuracy or genuine end-to-end sensor milestones for these fixtures.
