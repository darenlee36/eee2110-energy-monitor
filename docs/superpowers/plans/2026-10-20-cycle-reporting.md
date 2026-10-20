# Cycle Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Native execution in the existing chat is recommended; no parallel implementation is needed.

**Goal:** Give every completed cycle a saved evidence-backed report and provide optional exact 5/10/20-cycle summaries in Energy Monitor.

**Architecture:** Python builds deterministic cycle evidence and a template report from the existing analytical cache. One new report-history table in SQLite and Supabase persists immutable snapshots; Streamlit reads them. A one-shot laptop entry point generates missing individual reports without needing a browser session; Codex enhancement and scheduling are a subsequent batch.

**Tech Stack:** Existing Python >=3.12, Pydantic, SQLite, httpx, Supabase PostgreSQL/RLS, Streamlit, pytest and Ruff. No new package or service.

**Spec:** `docs/superpowers/specs/2026-10-20-reporting-design.md` (user's 20 October cycle-based amendment included).

## Global constraints

- Product name Energy Monitor. Keep the existing single-appliance validation scope.
- Counts are cycles, not seconds. Individual reports contain one completed cycle; batches contain exactly 5, 10 or 20.
- Never combine devices/profiles or treat active/incomplete cycles as completed.
- Keep origin and manual-entry provenance explicit. Sensor origin does not prove calibration.
- Report model status as not trained/not evaluated; do not invent ML scores or physical-fault diagnoses.
- Reuse existing cycle energy and tariff logic, including partial-charge ranges and limitations.
- Public viewers can read reports; only the owner or local reporting process can generate them.
- Raw telemetry, labels and models remain unchanged. Keep secrets out of source, snapshots and browser code.
- Check account usage before each task and before remote changes. At <=5% remaining in either window, stop at the last tested checkpoint. Do not begin a task without margin to verify it.
- Do not add paid API usage, a new reporting automation or independent cloud generation in this plan.

## Review focus

1. A request for 20 cycles with only 19 available must not silently produce a 19-cycle report (Task 1).
2. Equal end times must produce stable ordering and evidence hashes (Task 1).
3. Manual/simulated/unknown provenance must never become a measured-energy claim (Task 1).
4. Retrying after an uncertain cloud response must not create a second report (Tasks 2–3).
5. Browser refresh or public access must not trigger report generation or future AI spending (Task 4).

## File map

- Create `software/src/energy_monitor/reporting.py`: report draft model, deterministic evidence/rendering, missing-cycle runner and module entry point.
- Modify `software/src/energy_monitor/storage.py`: scoped completed-cycle queries and SQLite report history.
- Modify `software/src/energy_monitor/cloud_edits.py`: cloud-backed report history on the existing store, preserving its owner gate.
- Create `software/supabase/migrations/202610200001_reports.sql`: only the new reports table, constraints, RLS and server grants.
- Modify `software/dashboard/app.py`: Reports navigation and existing-style controls; no CSS redesign.
- Create `software/tests/test_reporting.py`; extend `software/tests/test_cloud_edits.py` and `software/tests/test_dashboard.py`.
- Update `software/README.md` and a new reporting audit after verification.

All commands below run from `software/` in the existing worktree. Use its `.venv`.

## Task 1: Individual and batch report calculations

**Files:** reporting.py, storage.py, tests/test_reporting.py.

**Interfaces:** Add `SQLiteTelemetryStore.fetch_report_cycles(profile_id: str, device_id: str, limit: int | None = None) -> list[dict[str, object]]`, scoped to completed cycles and ordered by end time descending then cycle ID descending. Add `build_report(store: SQLiteTelemetryStore, *, profile_id: str, device_id: str, cycle_id: str | None = None, cycle_count: int = 1) -> ReportDraft` in reporting.py. One-cycle mode requires a cycle ID; batch mode accepts only 5/10/20 and no explicit cycle ID. `ReportDraft` contains the spec's immutable report fields plus `evidence_snapshot` and `report_text`; its engine is template and status is generated.

- [ ] Write failing tests named `test_individual_report_traces_cycle_evidence`, `test_batch_uses_exact_count`, `test_insufficient_history_is_explicit`, `test_equal_times_have_stable_order`, `test_provenance_is_not_claimed_as_measured`, `test_untrained_model_is_not_a_healthy_verdict`, and `test_active_or_wrong_device_cycle_is_rejected`.
  Assert exact cycle IDs/counts, source labels, Decimal-based totals, unchanged source rows, excluded-quality counts and explicit not-trained/not-evaluated states. Parameterize 5/10/20; reject 0, 2, negative and boolean counts.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest -o addopts='' -q tests/test_reporting.py`; confirm missing-interface failures before implementation.
- [ ] Implement the scoped query and builder. Reuse `get_cycle_detail`/existing tariff calculation for selected cycles rather than new pricing logic. Exclude invalid/missing/gapped readings' cycles from aggregate energy/cost while preserving their evidence/warnings. Separate provenance totals; do not produce an unqualified combined measured total.
- [ ] Produce canonical JSON with sorted keys, deterministic source order and no generated timestamp in the evidence hash. Derive report ID with UUID5 from engine/version/kind/requested count/evidence hash. Derive period metadata from selected cycles. Render fixed template Markdown with no unsafe HTML; render user labels/notes as escaped text, not remote images, links or instructions.
- [ ] Run the focused reporting and dashboard-query tests, then Ruff on changed Python files. Commit only this tested calculation slice.

## Task 2: Durable local report history and missing-cycle recovery

**Files:** storage.py, reporting.py, tests/test_reporting.py.

**Interfaces:** Add `save_report(report: ReportDraft) -> str`, `fetch_reports(profile_id: str, device_id: str, limit: int = 100) -> list[dict[str, object]]` and `fetch_report(report_id: str) -> dict[str, object] | None` to SQLiteTelemetryStore. Avoid a circular runtime import with the reporting model by using a TYPE_CHECKING import and validation in reporting.py. Add `generate_missing_reports(store: SQLiteTelemetryStore, *, profile_id: str, device_id: str) -> int`; return newly saved report count.

- [ ] Write failing tests for save/reopen equality, identical retry returning the same ID, changed evidence/version preserving earlier snapshots, and missing-cycle generation yielding N initially then zero on replay.
- [ ] Confirm the focused tests fail for absent persistence behaviour.
- [ ] Add one SQLite reports table with spec fields. Use immutable ID uniqueness and conflict-ignore followed by retrieval; never overwrite an old report. Bound history limits to 1–1000 and validate identifiers.
- [ ] Implement the missing-cycle sweep using completed cycles and evidence identity. No timers, background threads or AI calls. A resumed sweep must fill remaining gaps without duplicating completed work.
- [ ] Run reporting/storage regressions and Ruff. Commit this complete local-report checkpoint before starting cloud work.

## Task 3: Cloud persistence without new public write access

**Files:** migration 202610200001_reports.sql, cloud_edits.py, tests/test_cloud_edits.py, tests/test_reporting.py.

**Interfaces:** SupabaseCycleStore overrides the same save/fetch report methods. Cloud is authoritative; cache a validated returned report locally after confirmed success. Cloud failures raise SupabaseSyncError. For reads only, the UI catches this error, explicitly calls the SQLite base implementation and displays a cache warning. Generation never reports a cloud save as confirmed on failure. Keep saved field names identical across the two stores.

- [ ] Write failing HTTP-boundary tests for successful save/read, repeated ID, lost-response retry, malformed report response, owner denial and cached-read fallback. Assert no request mutates telemetry or models and errors do not expose keys.
- [ ] Confirm failures, then implement direct report-table requests with the existing httpx dependency and server-only config. Reuse the existing can_edit gate for interactive generation; the trusted laptop entry point explicitly enables generation.
- [ ] Create the additive public.reports migration: UUID primary key; profile reference; device ID; cycle/batch kind; requested count constraint (cycle=1, batch in 5/10/20); timezone-aware coverage/generated times; version, engine, status, hash, JSON evidence and report text. Use RLS, revoke anon/authenticated access and grant only required server SELECT/INSERT. Do not add UPDATE/DELETE rights or a telemetry foreign key to cache-derived cycle IDs.
- [ ] Run focused tests and migration review. Apply via the Supabase migration tool only after these checks. Verify deployed columns, RLS and denied client writes, plus server insert/retry/read in a rolled-back transaction or an explicitly labelled simulated report test.
- [ ] Commit after local and remote persistence evidence is recorded. Do not claim cloud success from SQL-file existence.

## Task 4: Reports view and one-shot laptop command

**Files:** reporting.py, dashboard/app.py, tests/test_reporting.py, tests/test_dashboard.py, README.md.

**Interfaces:** Module CLI supports `python -m energy_monitor.reporting --profile-id tefal-kettle --device-id DEVICE --generate-missing`, and manual `--cycle-id ID` or `--cycle-count 5|10|20`. Cloud-configured mode syncs stored telemetry into the existing analytical cache before building reports. Never print secret values. Dashboard offers Live, Cycles and Reports, preserving existing Live/Cycles behaviour.

- [ ] Write failing CLI tests for generation/replay and invalid argument combinations. Add dashboard tests for navigation, empty history, saved evidence display, 5/10/20 selectors, insufficient history and public read-only versus owner-generation behaviour.
- [ ] Confirm failures, then implement Reports with existing Streamlit widgets. Generation occurs only on explicit owner submission, never merely on refresh or entering the tab. Saved reports show source, engine, kind/count, period, evidence and warnings. Worker generation is the route to automatic per-cycle reports while no browser is open.
- [ ] Run reporting/cloud/dashboard tests. Run the laptop command twice against a disposable demo database and verify no duplicate reports. Verify UI locally in a real browser and capture source/engine disclosures. Do not deploy to main without completing the final regression gate.
- [ ] Document commands, laptop dependence, simulated-data limits and the fact that Codex enhancement/scheduling is still pending. Commit this functional manual/worker reporting slice.

## Task 5: Final verification and handoff

- [ ] Run `.\.venv\Scripts\python.exe -m pytest` and `.\.venv\Scripts\ruff.exe check . --no-cache`; record exact results, not historical counts.
- [ ] Verify the complete stored-data → cycle report → cloud persistence → dashboard history path and the 5/10/20 batch modes with labelled fixtures. Do not manufacture real sensor evidence.
- [ ] Record the release, verification evidence, limitations and any uncompleted work in `software/audits/2026-10-20-cycle-reporting-validation.md`. Inspect staged changes for secrets, commit the verified checkpoint and push the existing development branch.
- [ ] Report the unchanged 60% milestone score unless all requirements of another fixed milestone are actually complete. Reporting code alone does not complete milestone 10's final tests/documentation or real-data evaluation.
- [ ] Stop at this verified reporting foundation. Next batch: manual Codex enhancement, output validation, then a separately approved quota-bounded laptop schedule. Do not enable a schedule as a side effect of this implementation.

## Review gate

User review is required before executing this plan. Recommended execution is native in this existing chat, task-by-task, with checks after every task and no parallel agents. Do not reopen the agreed laptop-first or cycle-count decisions. If review identifies a missing runtime or permission decision, ask only that question in this chat.

## Execution checkpoints

- User approved execution on 21 October 2026 in this chat.
- Task 1 implemented: individual and exact 5/10/20-cycle template report calculations,
  scoped selection, evidence snapshots, stable IDs, source-separated totals and
  quality/tariff/untrained-model warnings. Seventeen new tests first failed for the
  absent feature; 24 focused reporting/query tests then passed. Full regression:
  128 passed with eight existing upstream deprecation warnings. Changed Python
  files passed Ruff. Report persistence, cloud table, Reports UI and worker are
  still pending; no AI generation or product reporting schedule is enabled.
- Baseline before changes: 111 passed. Root-directory invocation failed to import
  dashboard; the documented software-directory run passed. No source workaround used.
- One-time development continuation scheduled for 21 October 04:05 Malaysia time,
  after the verified 04:04:30 reset. Separate daily 10:00 checker remains active.
