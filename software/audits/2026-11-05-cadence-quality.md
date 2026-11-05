# Cadence-aware quality candidate — NOT deployed

Production filtering release: 71c37416fedb43d67be78c39c1be524214b4a183.
This candidate requires separate production approval.

## Defect and scope

cycles.py assumed five-second slots and only counted gaps above the configured
15-second warning threshold. A lost three-second reading creates a six-second
measurement gap and was missed. anomaly.py and report/electrical/energy charts
used a fixed 7.5-second separation. Live power and power-quality charts could
also draw continuous lines through an absent measurement.

The shared metrics helper now uses verified firmware schedules: pzem/sim-fw
0.2.0–0.2.5 and sim-0.1.0 use five seconds; 0.2.6/0.2.7 use three seconds.
Unknown firmware needs at least three consistent consecutive-sequence timestamp
observations at three or five seconds. Ambiguous observations are not treated
as a verified cadence. Sequence holes are separate evidence where uptime has
not reset. Device/profile/origin transitions do not infer lost samples between
unrelated streams. A firmware schedule transition uses the larger interval for
that boundary to avoid a false positive.

Gaps exceeding 1.5 times the verified cadence, or sequence holes, now produce
quality evidence. Missing-slot counts are estimates, not fabricated readings.
Cloud received_at is never used for sampling-gap detection. Delayed delivery
and duplicate-safe retry do not create false missing samples.

Charts insert missing-value breaks. Historical saved report snapshots without
sequence/firmware metadata retain the earlier five-second display policy unless
repeated timestamps establish a cadence. Their stored evidence is not rewritten.
Feature extraction rejects irregular raw traces even if an old summary omitted
the gap. No raw measurement, timestamps, firmware, database schema/security,
power thresholds, start/stop confirmation, termination timeout or energy
integration formula was changed.

## Verification and measured impact

- New cadence tests cover regular 3/5-second streams, one omitted sample,
  source/schedule transitions, sequence resets, delayed cloud receipt, historical
  reports and chart discontinuities.
- One old test expected a ten-second gap in a five-second stream to count zero
  missing readings; it now correctly expects one. Cycle completion stays intact.
- Focused regression: 161 passed, Ruff clean.
- Full Python regression: 371 passed, Ruff clean, ten upstream Streamlit/NumPy
  deprecation warnings.
- Read-only local replay of all six genuine frozen Supabase traces: 431 readings;
  ALL summary fields identical before/after, including start/end, duration,
  meter/integrated energy, assessment and zero gap/missing counts. See
  2026-11-05-cadence-impact.json.

On deployment, recomputing genuinely gapped histories can change their quality
counts/notes. Their durations, power/energy calculations and saved report
snapshots remain separate. No production recomputation or migration was run.
The candidate has not yet been exercised in a new physical collection session.

Release gate: user reviews this impact and explicitly approves deployment.
Rollback would be an ordinary revert of the candidate only, preserving the
already deployed date/time filters. No force push or record deletion.
