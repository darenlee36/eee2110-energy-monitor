# Genuine kettle Isolation Forest release

6 November 2026 MYT. Source is the ten named completed, Complete-labelled sensor
cycles retrieved read-only from Supabase. The earlier 10:40 trial and the 5 November
14:04 incomplete-labelled trial are outside this cohort. The public artifact holds
aggregate measurements, training IDs, fingerprints, model parameters and actual
outputs. The raw telemetry snapshot stays local.

## Trace audit and fit

All ten IDs were distinct. All 756 measurement timestamps and device uptimes
advanced by 3 s within each run. Sequences were continuous, quality was valid and
firmware was a single PZEM version in each run. The audit reproduced stored bounds,
duration, mean power, meter energy from the preceding sample and integrated energy.
No counter decreased within a run. Counter increments were at most 0.002 kWh.

| # | Start MYT | Samples | Duration s | Meter kWh | Integrated kWh | Mean W | Frozen v1 deviation | IF score /100 | Decision |
| - | - | -: | -: | -: | -: | -: | -: | -: | -: |
| 1 | 5 Nov 13:14:38 | 64 | 189 | .097 | .095810 | 1812.84 | 35.36 | 48.66 | +.013448890 |
| 2 | 5 Nov 13:26:53 | 75 | 222 | .111 | .110459 | 1783.36 | 5.16 | 44.76 | +.052445023 |
| 3 | 5 Nov 13:41:41 | 74 | 219 | .113 | .110891 | 1823.07 | 5.93 | 45.54 | +.044591571 |
| 4 | 5 Nov 13:52:35 | 80 | 237 | .122 | .120105 | 1817.08 | 20.47 | 45.75 | +.042496116 |
| 5 | 5 Nov 14:41:01 | 59 | 174 | .086 | .085960 | 1759.68 | 57.11 | 61.00 | -.110024426 |
| 6 | 5 Nov 14:51:37 | 79 | 234 | .118 | .116681 | 1789.75 | 16.47 | 45.43 | +.045657201 |
| 7 | 6 Nov 11:11:17 | 85 | 252 | .126 | .124997 | 1776.96 | 37.92 | 50.21 | -.002111524 |
| 8 | 6 Nov 11:23:50 | 85 | 252 | .127 | .124948 | 1783.96 | 36.52 | 48.43 | +.015700309 |
| 9 | 6 Nov 11:49:18 | 79 | 234 | .139 | .138468 | 2129.02 | 79.71 | 48.12 | +.018813803 |
| 10 | 6 Nov 12:04:10 | 76 | 225 | .137 | .134259 | 2148.29 | 74.12 | 46.12 | +.038845249 |

Reference #1–6 remain frozen. #1–4 ran 0.2.6, #5–6 ran 0.2.7 and #7–10 ran
0.2.9. Device uptime and sequence blocks distinguish separate firmware boot
sessions. They do not establish independent collection conditions. Mean/median
duration are 212.5/220.5 s, energy .107833/.112 kWh and measured power
1797.63/1801.30 W. Population standard deviations are 23.20 s, .012482 kWh
and 22.22 W.

#9–10 averaged 241.54/242.45 V and 8.812/8.861 A with PF 1.0, compared with
220.87–224.21 V in #1–8. Meter vs integrated energy differed by 0.38% and 2.00%.
Higher sampled power supports the higher energy. The cause of the voltage and
operating-condition shift remains unverified. #7 is held for the experimental
threshold. #9–10 are held for a feature shift of at least three frozen reference
scale units even though the forest decisions are positive. #8 is pending
independent review. Public Complete labels record run completion.

## Model and interpretation

The initial model fits six genuine cycles with one vector per cycle, duration in
seconds and arithmetic mean PZEM active power in watts. Features use native units.
The model uses scikit-learn 1.9.1, 200 trees, random state 42, all six samples,
one job and automatic contamination threshold. Its content identity is
`baf06aa2f3612e67a4d4eb151035502f180bf4baa111555fd05672fa209c06d7`.
Both raw `score_samples` and `decision_function` are in the release JSON.
Display mapping v1 clips `-100 * score_samples` to 0–100. The model threshold
is 50. Values above 50 correspond to a negative decision.

The archived model outputs for #1–6 are in-sample. #7–10 were unseen when fitted.
They are observations, with no independently established detection accuracy.
This small forest can assign moderate scores to loads far outside the observed
power range. Review guards use independent trace and reference evidence.
The score is an experimental model output and carries no calibrated fault
probability. Saved numerical reports retain the evidence from their generation.

## Complete-label flow

Live prompts for a completion label on the latest unlabelled sensor cycle.
Cycles offers Complete and Incomplete in the existing audited annotation form.
Saving Complete enables trace validation and inference on the selected cycle.
Reports also offers the scores independently of saved report availability.
Known audited results are immutable in the versioned JSON. Unseen eligible
inference uses the same model and feature checks. Automatic baseline publication
requires an owner policy, independent review evidence and durable versioned state.
