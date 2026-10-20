# Cycle reporting foundation validation

Executed: 21 October 2026, Malaysia time. Product: Energy Monitor.
Branch: `codex/cycle-first-kettle-dashboard`; calculation/storage/cloud/UI commits
`ee93aa9`, `1e004f8`, `89e13b5`, `1ae0f6e`, reviewed fix `30d25a6`.
The verified code is [pushed on the development branch](https://github.com/darenlee36/eee2110-energy-monitor/tree/codex/cycle-first-kettle-dashboard).
Remote code HEAD matched `30d25a6fce0b22f849f60b6524138dbd42512fbd` after the push.
Main remained `158d3d56e518ecfbfb492dfabf9b18949696cd64`; the hosted deployment was not changed.

## Scope delivered

- Evidence-backed template report for one selected completed cycle.
- Exact latest 5/10/20 completed cycles per device/profile; insufficient history is
  an explicit error, never a smaller silent batch. Equal end times have stable ID ordering.
- Canonical evidence hash and deterministic report UUID; immutable SQLite/Supabase
  history, preserving old snapshots when evidence/version changes.
- Source-separated totals, quality exclusions, existing tariff qualifications,
  inspectable evidence and explicit untrained/not-evaluated model status.
- Reports tab using existing widgets/styles; public cloud viewing, owner-only
  explicit generation, no generation on refresh. Cloud read failure warns and uses
  cached history; failed cloud writes are never labelled confirmed local success.
- One-shot laptop worker with selected cycle, exact batch, and missing-cycle modes.
  Cloud mode syncs all current pages by cursor progress, including duplicate-only
  pages, before restoring edits and generating reports.

## Tests and observed behaviour

- Task 1: absent-feature RED followed by 24 focused passes; full regression 128 passes.
- Task 2: 10 failures before persistence, then 44 reporting/storage passes; full 138 passes.
- Task 3: 7 new failures before cloud methods, then 37 cloud/reporting passes; full 145 passes.
- Task 4: 12 missing-interface failures, then 63 reporting/cloud/dashboard passes.
  All these full/dashboard runs retained eight pre-existing upstream Streamlit/NumPy
  deprecation warnings. Ruff passed for every changed Python file.
- Separate-process worker test initially reproduced the Python `__main__` versus
  canonical-module model identity error. Canonical entry dispatch fixed it. Actual
  subprocess sweeps returned 5 new then 0; selected-cycle replay and exact batch also passed.
- Final full regression after the review fix: **159 passed, 8 existing warnings in
  39.91 seconds**. The final plan-closing gate repeated the full suite on the pushed
  code: **159 passed, 8 existing warnings in 39.71 seconds**. Full-tree Ruff: **all checks passed**.
- Independent read-only review found no critical issue and one important issue:
  readable reports omitted cycle-specific quality notes. Reproducing tests failed
  first; versioned v2 notes and plain-text legacy-v1 display then passed. Tests cover
  cross-check warnings without exclusion, immutable v1 compatibility and escaped
  Markdown image syntax. No minor findings were deferred.

## Live cloud evidence

Project `lxwogpxjfdckrfdicpyp`; connected migration tool applied
`20261020201336_cycle_report_history.sql`, with matching remote migration history.
`public.reports` has UUID uniqueness, scoped profile/device metadata, exact count
constraints, coverage/generated timestamps, version/engine/status, canonical hash,
JSON evidence and text. RLS is enabled. `anon` and `authenticated` have no
SELECT/INSERT/UPDATE/DELETE rights. `service_role` has SELECT/INSERT only, not
UPDATE/DELETE. No new telemetry/model mutation permission or public policy was added.

Report `57967451-66fd-55b2-b829-6e2bf41873fa` was created from an existing **simulated**
ESP32 cycle for `sim-esp32-03b248 / tefal-kettle`, 122 seconds, template engine.
Saving twice preserved one immutable snapshot. A fresh cache recovered the report.
The trusted cloud worker then ran twice for the same cycle and returned the same ID;
the original server snapshot remained unchanged. No raw telemetry was changed.

Supabase's existing `ingestion_batches` and new `reports` no-policy INFO advisories
are intentional server-only restrictions, not evidence of public access. Relevant
guidance: [RLS enabled with no policy](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy).

## Browser evidence

- Local labelled fixture: individual and exact 5/10/20 report creation verified in
  the real browser. The 20-cycle example shows 2.0 kWh **simulated** energy and a
  qualified RM0.8880–RM1.0880 gross-charge range, not a household bill.
- Separate local cloud preview retrieved the durable simulated report without owner
  login. Generation controls were absent. This verifies development code against
  live storage, not a new public Streamlit deployment.
- Page widths checked at 320, 768, 1024 and 1440 pixels: no horizontal page overflow.
  No console errors/warnings were observed before the deliberate preview restart.
  That restart logged one expected `WebSocket onclose` warning; the restarted page
  loaded and generated a v2 report successfully. Native labelled controls remain keyboard
  operable; this was not a full screen-reader/WCAG certification.
- Screenshots: [desktop](screenshots/2026-10-21-reporting-desktop.png),
  [report evidence](screenshots/2026-10-21-reporting-evidence.png),
  [phone width](screenshots/2026-10-21-reporting-mobile.png),
  [cloud view](screenshots/2026-10-21-reporting-cloud.png),
  [final readable quality notes](screenshots/2026-10-21-reporting-final-quality.png).

## Decisions and limitations

1. The connected migration tool assigned the actual migration timestamp rather than
   the plan's placeholder filename. Remote/local histories match; a mismatch would
   require reconciliation before another migration.
2. Report discovery can read the latest 100 snapshots across scopes without raw
   cycles. Scoped history is also limited to 100 in the UI (storage maximum 1000).
   Devices absent from that discovery window and the local cycle cache are not listed;
   this bound is explicit in the UI/docs. No discovery service/table was added.
3. Review intentionally did not revalidate pre-existing cycle-detection/tariff
   scientific accuracy. Reporting reuses those interfaces; incorrect input/calibration
   would still produce incorrect figures and requires the real-data validation stage.
4. The documented latest-100 discovery bound remains accepted after review; a device
   outside it and the local cycle cache can be omitted from the selector.
5. Future Codex engines/statuses and product scheduling remain a separate approved
   batch; this foundation cannot yet generate AI narratives or reports off-laptop.
6. Final regression/audit and development-branch push follow the review rather than
   treating their absence during review as a product defect. A failed gate must stop
   the push; main/deployment remain excluded.
7. The reviewer relied on this execution's live RLS and browser evidence rather than
   repeating remote checks. Additional independent integration issues are not ruled out.

Template v2 snapshots include readable per-cycle notes. Existing v1 snapshots retain
their original identity/text and still validate; the dashboard displays their saved
quality notes separately as plain text. No stored report was overwritten or migrated.

Reports are deterministic templates, not Codex/AI narratives. No paid API, product
reporting schedule, independent off-laptop worker, model training or hardware
validation was enabled. Saved cloud reports can be viewed while the laptop is off,
but no new reports are generated unless the one-shot laptop worker is run again.
Synthetic/manual/unknown data do not establish real sensor accuracy. Sensor origin
also does not establish calibration. Household usage context and applicable AFA
remain unresolved, so qualified cost estimates are partial.

Software checklist progress remains **60%**. Reporting foundation is partial work
towards milestone 10, not completion of agentic reporting, final real-data regression
or final software documentation. Real ESP32/PZEM measurement flow, calibration,
labelled dataset and Isolation Forest evaluation remain pending.

Next batch: manually validate Codex narrative generation/output checks; only then
seek approval for a quota-bounded laptop reporting schedule. Public deployment and
main merge require separate approval. The daily 10 AM software checker remains separate.

The one-time 04:05 development continuation was deleted after its purpose was
fulfilled. The independent daily software checker was verified ACTIVE at 10:00
Malaysia time and was left unchanged. Final usage check: 27% five-hour and 69%
weekly remaining; no 5% safeguard was crossed. No further implementation batch
was started after this verified target.
