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

## Visual report dashboard — 23 October 2026

Implemented the approved presentation upgrade without changing the immutable report
format, storage, source permissions or generation policy. Main report content now uses
human-readable metric cards and saved power/current/energy traces. Frequency averages
come only from valid saved readings; manual and legacy reports do not invent them.
Batch summaries compare exactly 5, 10 or 20 cycles, keep provenance groups separate,
and omit quality-excluded energy bars while showing recorded duration. Insights use
the existing deterministic observations/explanations/actions. Actual anomaly score
remains unavailable; the optional sample score remains display-only and labelled.

Fresh verification:

- Five initial feature cases failed before implementation (missing metrics, plots and
  unavailable-reading states), then passed with the new presentation.
- Dashboard regression: **27 passed**, including all three batch sizes, last-cycle
  selection, snapshot-only rendering, manual/v2 fallback, source/exclusion handling,
  sample-score immutability, invalid samples, gaps and meter-counter reversal.
- Full software regression: **172 passed**, eight existing upstream Streamlit/NumPy
  deprecation warnings. `ruff check . --no-cache`: clean.
- Isolated headless Edge used a disposable local database with 20 generated cycles and
  four saved reports. No test values were written to Supabase or production storage.
- Browser verified 5/10/20 bar counts, saved-report selection, the sample 72/100 display with
  actual score still unavailable, and three independent reading plots.
- Actual drag zoom changed the first chart's x range from approximately -8.65–153.65
  seconds to 23.74–104.80 seconds. **Reset view** restored the original range.
- The browser connector failed initialization; bundled Playwright with installed Edge
  provided isolated verification without installing another browser or using signed-in
  sessions. No browser console warnings or errors were observed during these checks.
- Preview launched from the repository root to load the deployment-equivalent dark
  theme. Layout checks at widths 320, 768, 1024 and 1440 found no horizontal overflow.
  Native controls expose accessible labels; assistive-technology testing is not claimed.
- One focused read-only code review found no actionable correctness blocker. It did
  not independently rerun tests, audit storage security, validate tariffs/calibration,
  or assess browser appearance; the parent execution supplied browser/test evidence.
- Final screenshot review caught a truncated four-decimal batch cost range. Three
  regression assertions failed before matching the existing three-decimal cycle range;
  the final dashboard rerun passed all 27 tests and Ruff remained clean. Stored amounts
  keep their original precision. Last-cycle switching is covered by AppTest; a direct
  browser attempt to locate the twentieth option encountered the virtualized option list
  and is not counted as a successful browser interaction.

Screenshots: [readings and metrics](screenshots/2026-10-23-report-readings-desktop.png),
[phone readings](screenshots/2026-10-23-report-readings-phone.png),
[batch comparison](screenshots/2026-10-23-report-batch-desktop.png),
[illustrative score](screenshots/2026-10-23-report-sample-score.png).

Remaining: Codex narrative generation with bounded input/output validation, an approved
laptop schedule, eventual off-laptop execution, real-data milestones and final software
regression/documentation. The visual work does not complete milestone 10. Overall
software progress remains **60%**. No main merge, push, public deployment, paid API,
secret change, product reporting schedule or hardware validation was performed in
this visual upgrade. The daily checker was not modified.

## Manual Codex-selected review — 24 October 2026

Continued after the user accepted the visual report and authorized development until
a decision gate. Reused existing immutable report storage and Streamlit/Plotly views.
One new `report_narrative.py` module isolates the process boundary and insight policy
from numerical/template generation. No new package, service, table or migration.

### Implemented boundary

- Codex selects IDs from an eligible, application-owned catalog. Python supplies all
  wording and values. This is AI-assisted selection, not free-form AI narrative.
- Catalog input excludes notes, labels, timestamps, device/profile IDs and raw traces.
  Strict selection validation rejects extra fields, unknown/ineligible IDs, duplicate
  selections and missing provenance. It cannot add an arbitrary numerical/health claim.
- Generated, validation-failed and runtime-error snapshots retain their source template,
  canonical evidence hash, deterministic policy/source completion ID and numerical
  fallback. Stored template/AI engine-policy pairs are validated to prevent mislabelling.
- The local-only CLI requires an existing database, saved template ID and explicit
  authorization flag. It does not load application secrets. ChatGPT login is required;
  no application API key is inherited or configured.
- Runtime uses a temporary cwd, minimal OS/auth-location environment, no user config,
  retained managed constraints, read-only sandbox and disabled tool/app/plugin/browser/
  memory/hook features. Unknown errors, tool events and incomplete/malformed results
  fail closed. No actual tool activity was observed in the accepted live execution.
- First saved outcome wins, including failure. Sequential retries reuse it without a
  new invocation. There is no automatic retry or automatic scheduling.

### Test and live evidence

- Initial contract tests: 11 expected missing-feature failures before implementation.
- Runtime/CLI tests first failed for absent isolation/parser/authorization behaviour.
  Dashboard success/failure display tests also failed before their UI was added.
- The first live simulated attempt was rejected because an `item.error` notice was
  initially misclassified as tool activity. Two generic, non-project diagnostic runs
  identified the exact message: Code Mode was unavailable because its host was disabled.
  The parser now permits only that exact fail-closed notice; other errors still fail.
  A reproducing test failed before the narrow correction and passed afterwards.
- Four deliberate live Codex invocations in total: one rejected simulated report,
  two generic diagnostics and one accepted new simulated report. No API key, new
  service billing, production telemetry or Supabase writes were used.
- Accepted simulated report `e1a0ca84-7c19-586b-b5eb-d4db2325bc42`, source template
  `0a3d01f4-9f8f-5a4f-9b08-9cba3005cf85`: generated status, five valid observations
  and three actions. A repeated request returned the saved outcome with a runner
  that would fail if called, proving sequential reuse.
- Existing cloud read/cache path verified with HTTP fixtures, including recovery into
  an empty cache without analytical cycles. This is not a live cloud deployment test.
- Final full regression: **201 passed**, eight existing upstream Streamlit/NumPy
  deprecation warnings. Ruff clean. A new mislabelling case failed before enforcing
  the reserved AI policy/engine boundary. An initially over-broad version allowlist
  then failed the existing template-version-history test; narrowing that check
  preserved history compatibility and produced the final green suite.
- Browser: actual accepted review rendered in isolated headless Edge; saved readings
  and anomaly score Unavailable retained. Widths 320/768/1024/1440 had no document
  horizontal overflow; no console warnings/errors observed. Dashboard tests verify
  viewing and refresh do not invoke Codex.
- Focused read-only review found no blocking defect. It noted that simultaneous
  processes or a crash after inference but before save can cause another invocation.
  It did not independently run model calls, browser checks or the full suite.

Screenshots: [AI review](screenshots/2026-10-24-ai-review-desktop.png),
[phone width](screenshots/2026-10-24-ai-review-phone.png).

### Remaining decision gate and limitations

No schedule is enabled. Cadence and maximum AI invocations per run require user
approval. Before scheduling, implement mechanical account-usage checks for the 5%
cutoff and serial execution; the current manual CLI relies on the operator checking
usage first. A client timeout does not enforce a server-side token/billing limit.
Failed snapshots are immutable and need an explicitly designed retry policy rather
than automatic resubmission. Laptop-off generation, free-form narrative generation,
model training and real-sensor validation are not delivered by this change.

No main merge, push, public deployment, live Supabase write, credential change or
daily-checker modification was performed. Fixed software milestone progress remains
60%; this is partial milestone-10 work, not completion of final evaluation/documentation.

## Guarded laptop reporting schedule — 25 October 2026

User approved 30-minute laptop-only passes with at most one pending cycle review
per pass. This supersedes the previous scheduling decision gate; off-laptop workers,
paid APIs, main merge and public deployment remain unapproved. No new dependency,
database table, secret or service was introduced. One worker module isolates quota,
process locking and crash reservations; one native Windows management script makes
the task inspectable, disabled-on-install and reversible.

### Verified controls

- Installed Codex app-server quota RPC: initialize, initialized,
  account/rateLimits/read only; no thread/start or turn/start. Both current five-hour
  and weekly windows must exceed 5% remaining. Missing, stale, invalid or non-finite
  data pauses AI. Numerical/template catch-up still runs without inference.
- Quota is rechecked by the actual model runner. Expiry is checked before reservation,
  after quota and after login immediately before inference. These are pre-call checks,
  not a guarantee against other shared-account activity or already-started inference.
- Default manual/scheduled entry points share a process lock. Cross-process lock
  rejection and release were exercised. A durable per-source/policy reservation is
  flushed before inference. Crash/uncertain saves remain held, never auto-retried;
  a quota pause before inference removes only its own unused reservation.
- Native task `EnergyMonitor-CycleReporting`: logged-in current user verified by SID,
  Interactive/Limited, every 30 minutes, IgnoreNew, no wake, StartWhenAvailable,
  ten-minute execution limit, battery operation allowed. Trigger and runtime expire
  at 7 December 2026 00:00 Malaysia time. Enable/Run reject unsafe settings drift.
- Independent review found two Important issues: cutoff checked too early, and task
  safeguards not revalidated at enable/run. Both were fixed; cutoff regression first
  failed then passed. Nine substituted native task drifts are rejected. Reviewer
  rechecked and approved the fixes without external/model calls or mutations.
- Actual task registration was read back while disabled, then enabled only after
  verification. Windows normalizes the principal to an abbreviated account name;
  a failing regression reproduced this and the comparison now resolves the unique
  SID rather than accepting arbitrary abbreviated text.

### Native execution and cloud evidence

- First Windows-launched pass saved four individual numerical reports, then paused
  with `usage_unavailable`, zero AI attempts and no completion reservation. Codex is
  absent from Windows' persistent user/machine PATH but available in the desktop app's
  interactive PATH. The task was disabled while this difference was corrected.
- The registered action now explicitly names the existing installed Codex executable;
  no global PATH, authentication or credentials were changed. A regression covers
  executable discovery without interactive-shell PATH.
- Retried native task exited **0**, status `completed`, exactly **one** AI attempt,
  zero held attempts; numerical templates were reused. Report:
  `75b90401-b71b-5530-8e8f-4cc8b5f3d38b`. Next native run shown as 25 October 01:10:45
  Malaysia time at verification; the task remains enabled, ready for 30-minute polling.
- A fresh temporary empty cache fetched that report directly from Supabase: engine
  codex, generated status, source counts simulated=1, totals only under simulated,
  model not_trained and no anomaly_score. `record_source=measured` is the existing
  automatic-versus-manual storage classification, not evidence of sensor origin;
  actual report provenance remains explicitly simulated.
- Re-requesting the same saved source through a fresh cloud-backed store returned
  idle/zero AI attempts with quota and runner functions that would fail if invoked.
  Thus saved-outcome reuse was confirmed without another model call.
- Exactly one live model invocation was used for this activation. No telemetry rows
  were fabricated/uploaded. Four numerical reports and one validated AI-selected
  report were written through the existing server-only cloud persistence path.

### Limits and completion boundary

Final full regression after native-executable discovery correction: **226 passed**,
eight existing upstream Streamlit/NumPy deprecation warnings; Ruff clean. Tests mock
external inference/quota boundaries and include the real cross-process lock plus
native Windows task-definition checks. They do not consume live model calls.

Physical laptop sleep/resume was not forced or tested. StartWhenAvailable is verified
configuration; backlog recovery is tested with stored fixtures. No generation occurs
while asleep/off or signed out. No multi-laptop exactly-once guarantee exists; retain
the ignored reservation journal. Failed/uncertain attempts require deliberate review.
Polling is not a complete CDC guarantee for concurrent cloud commits.

Exact 5/10/20-cycle batches remain explicit user options, not automatic repeated jobs.
The independent 10 AM software checker was not read, replaced or modified. Main,
public deployment, hardware milestones and model training are unchanged. Software
progress remains **60%**, with reporting as partial milestone-10 work. Next decision
gate is approval to publish the tested reporting upgrade, not a need for real telemetry
to continue developing this reporting foundation.
