# Six-cycle preview release and Friday freeze

Prepared 5 November 2026. Core freeze: Friday 6 November. Slide preparation:
Saturday 7 November; presentation expected 12 or 13 November (date unconfirmed).
This supersedes the older collection document's 8 November development target.

## Production release

Production: a4b304af178c20d2abd561ba0cd0bbb2a34b59c4.
Rollback: c1861b1bc8c2dc78f3a0002a0c17e938d686367a, by an ordinary reviewed
revert commit and Streamlit restart; never force-push or delete data.

The public artifact contains only the six approved aggregate reference records,
scope hash, parameters and provenance/version metadata. The full Supabase snapshot
and prototype branch containing raw traces were NOT pushed. No secrets, database
permissions, telemetry, annotations, costs or historical reports were changed.
The five table fingerprints matched before and after hosted verification:
3,344 telemetry rows, eight cycles and 17 reports, with identical content hashes.

Fresh hosted browser verification after managed restart:

- All six scores and their contribution tables match the reference below.
- Main ML assessment remains Unavailable / Not Evaluated.
- Preview can close/reopen; saved report evidence remains readable.
- Cycles retains eight sensor-mode records, including the earlier 0.0470 kWh
  cycle, six Complete annotations, one Incomplete and one Unlabelled.
- Responsive viewports checked at 1,440 and 390 CSS pixels; no document overflow.
  The contribution table has its own horizontal scrolling on narrow screens.
- Existing Live status, chart scaling and three-second polling code is unchanged.

## Frozen v1

Baseline: pilot-deviation-v1-f1f5564e68e2.
Feature extraction: duration-measured-power-v1.
Reference artifact: config/pilot/kettle-six-cycles-v1.json.
Raw snapshot SHA256: 3adb5ddc20f0d081773f378e6555defa180a73b0a8177ca02ddf6708e10de6de.

| Cycle start MYT, 5 Nov | Duration s | Energy kWh | Sampled mean W | Pilot /100 | IF decision |
|---|---:|---:|---:|---:|---:|
| 13:14:38 | 189 | 0.0970 | 1812.8422 | 35.4 | 0.01344889 |
| 13:26:53 | 222 | 0.1110 | 1783.3573 | 5.2 | 0.05244502 |
| 13:41:41 | 219 | 0.1130 | 1823.0743 | 5.9 | 0.04459157 |
| 13:52:35 | 237 | 0.1220 | 1817.0850 | 20.5 | 0.04249612 |
| 14:41:01 | 174 | 0.0860 | 1759.6780 | 57.1 | -0.11002443 |
| 14:51:37 | 234 | 0.1180 | 1789.7481 | 16.5 | 0.04565720 |

431 sensor readings, one vector per completed cycle. Reference medians:
220.5 seconds, 0.112 kWh and 1801.295144 W. Duration MAD 15 s; mean-power
MAD 16.863833 W. Scales are max(1.4826 * MAD, floor): 22.239 s and 50 W.
Score = min(100, (0.7 * abs(duration - median) / duration_scale +
0.3 * abs(sampled_mean_power - median) / power_scale) * 100 / 3).
Energy is descriptive, not an additional correlated scoring feature.

The six public Complete annotations select collection records, not independently
reviewed normal training labels. Exact water volumes, starting temperatures and
independent session labels are unknown; 700–800 mL is only an estimate.
Higher deviation does not imply appliance failure. The shorter fifth run is
further from this reference; no physical cause has been established.

## Local-only incoming-cycle and v2 workflow

pilot_workflow.py reuses A2 PreparedDataset, frozen chronological SplitPlan and
A1 feature validation. It creates local JSON artifacts, never alters cloud
records, activates a model, calls AI or schedules work.

1. Record new cycles with templates/kettle-cycle-collection.csv using the A2
   collection procedure. Use exact measured water quantity, observed temperature
   band, genuine session ID/start and independent observations. Do not rename
   adjacent cycles as different sessions to manufacture independence.
2. Prepare the A2 dataset and chronological split using the existing dataset CLI.
   Its defaults retain the 30-training / 10-validation / 20-test aspiration.
   For a small exploratory split, use genuine earlier training sessions and
   separate later validation/test sessions; report the actual small denominators.
3. Score each eligible reviewed new cycle against frozen v1 FIRST. Unknown labels
   stay unknown. Require completed sensor source, matching scope, full features,
   zero quality exclusions and a raw-trace fingerprint.
4. The local gate additionally checks known firmware cadence (3 s for 0.2.6/0.2.7,
   5 s for 0.2.5), largest gap <= 1.5 times cadence and count consistent with
   duration/cadence. Unknown firmware is rejected until explicitly reviewed.
5. Save per-cycle v1 assessments with their original features, reference parameters,
   version/hash, trace hash, dataset/split hashes and original partition. The
   existing OS lock plus atomic replace prevents concurrent partial files;
   duplicate identical saves reuse evidence, differing saves refuse overwrite.
   These local files are not a replacement for Supabase report history.
6. Explicitly select only reviewed normal TRAINING cycle IDs for v2. A recorded
   independent review timestamp must follow the cycle. Public annotations alone,
   unknown labels and held-out sessions cannot enter expansion. A saved matching
   v1 assessment must already exist. V1 stays unchanged; v2 records parent,
   selected IDs, dataset/split hashes and recomputed medians/MAD.
7. Fit a separate v2 experimental IF artifact if warranted. Preserve both versions
   and original v1 scores. Once a cycle enters fitting, it is not held-out evidence
   for that version. The legacy six still have unknown conditions, so an expanded
   six-to-ten-cycle reference remains experimental even after new-cycle review.

From software/, using the existing virtual environment and PYTHONPATH=src:

```powershell
python -m energy_monitor.pilot_workflow fit --output data/pilot-experiments/kettle-v1-if.json
python -m energy_monitor.pilot_workflow assess --dataset DATASET.json --split SPLIT.json --ledger data/pilot-experiments/v1 --output data/pilot-experiments/assessments.json
python -m energy_monitor.pilot_workflow expand --dataset DATASET.json --split SPLIT.json --ledger data/pilot-experiments/v1 --cycle-id REVIEWED_TRAINING_UUID --output data/pilot-experiments/baseline-v2.json
python -m energy_monitor.pilot_workflow fit --reference data/pilot-experiments/baseline-v2.json --output data/pilot-experiments/kettle-v2-if.json
```

Repeat --cycle-id for each reviewed candidate. --testing is mandatory for
synthetic_fixture collections; their assessments/references retain testing=true.
Do not edit artifact files or overwrite an original assessment to force a fit.
The local directory is ignored by Git.

## Experimental Isolation Forest

Operational locally, not connected to production model activation. scikit-learn
1.9.1, 200 trees, max_samples=6, contamination=auto, random_state=42, n_jobs=1.
Two features: duration and actual sampled mean active power. No invented labels
or individual-sample pseudo-replication. JSON records the reference, feature and
model versions, training IDs, settings, dependency version and raw outputs;
reload validates checksums and reconstructs/refits deterministically without
unpickling code.

Local artifact: data/pilot-experiments/kettle-v1-if.json.
Embedded canonical manifest checksum (excluding the checksum field):
baf06aa2f3612e67a4d4eb151035502f180bf4baa111555fd05672fa209c06d7.
The six displayed IF decisions are IN-SAMPLE rankings. Lower decision values
are more isolated, not probabilities. No independent validation cycles, accuracy,
fault classification or failure confidence is available. New-cycle prediction
also returns Not Evaluated. Existing A1 production minimum/review gates remain.

## Friday feature-freeze acceptance checklist

Verification on 5 November: release-focused 147 passed; workflow/dataset/anomaly
71 passed before the final CLI addition. Full regression collected 367 checks:
363 passed initially; four native launcher checks required this isolated checkout's
software/.venv link to the existing environment. All four then passed together
with all 21 final workflow checks (25/25). The final CLI rehearsal is one additional
check beyond the full run: 368 distinct checks verified across these runs.
Ruff and git diff --check clean. Ten upstream Streamlit/NumPy deprecation warnings
occurred in the broad run. The first sandboxed attempt was discarded because
local HTTP/socket access was blocked (WinError 10013); test-only local services
were then run with appropriate access. No installed reporting task was altered
and no live AI was invoked.

- [x] Approved six-cycle preview published; source, statistics and scores verified.
- [x] Historical cloud telemetry/cycles/annotations/costs/reports fingerprints preserved.
- [x] Local v1 assessment journal, explicit v2 preparation and reproducible IF fit.
- [x] No production ML activation, hardware changes or paid inference.
- [ ] Confirm Ready -> Monitoring -> Ready using real idle/phone-charger states.
- [ ] Collect at least four additional genuine cycles if safe/feasible; record
      volume, conditions, session, labels and quality before experimental v2.
- [ ] Review recurring sensor/upload stalls with real runtime/serial evidence.
      Firmware 0.2.7 is a recovery correction, not long-term reliability proof.
- [ ] Resolve or explicitly disclose the detector's historical 5-second missing-
      sample estimator before claiming a calibrated 3-second production quality
      metric. New local pilot eligibility adds strict cadence checks; production
      detector behavior and historical cycle end timestamps were not changed here.
- [ ] Confirm real new completed cycles persist, costs remain qualified and saved
      numerical reports recover after a fresh cache/app restart.
- [ ] Freeze the stable demo build and select desktop/mobile screenshots.

Keep costs qualified while monthly AFA/household context is unresolved. Raw
Modbus provenance and independent calibration are still separate evidence gaps.
No short successful run proves long-duration reliability or power-loss retention.

Software tracker remains 70%: genuine-data quality/calibrated dataset, independent
IF evaluation and final reporting/regression/documentation milestones are not
fully complete. The local experimental workflow does not earn those milestones.

Tomorrow's physical presence is needed only for the approved real-state checks,
ordinary supervised collection and any separately approved diagnostic capture.
No new electrical test, reset, flash or wiring action was performed by this work.
Slides can begin Saturday using the verified preview and local IF results, with
the in-sample and data-size limitations stated explicitly.
