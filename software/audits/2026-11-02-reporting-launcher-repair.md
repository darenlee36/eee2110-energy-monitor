# Windows reporting launcher repair - 2 November 2026

Base: A3 `17848c6`. Branch: `codex/reporting-launcher-repair`.
Scope: existing EnergyMonitor-CycleReporting task only; no deployment or inference.

## Root cause and correction

The installed task pointed to a removed version directory under the Codex app's
bin folder. Its existing Python CLI rejected that nonexistent explicit path before
opening a database: exit **2** reproduced, with no database created. The current
installed binary answered `--version`: **codex-cli 0.166.0-alpha.5.1**; this starts
no model turn.

The existing reporting-schedule.ps1 now provides a Worker action and resolves an
existing Codex executable on each start, first from PATH and then from immediate
app version directories. The task stores the stable launcher path, not a version
path. No new service or reporting architecture was added. An explicit no-AI CLI
mode performs numerical catch-up before returning, bypassing quota/provider calls
and all AI reservation logic. It uses the existing worker lock.

The shared virtual environment had an editable import pointing to the older
checkout. The launcher sets PYTHONPATH only for its child process to select current
A3 code. It retains the original runtime environment, .env location, SQLite cache,
state directory, device/profile scope and credentials without reading/printing their
values or reinstalling the environment.

## Native execution evidence

| Check | Result | Evidence |
|---|---|---|
| Original failure reproduced | PASS | Exit 2, rejected missing executable before database creation |
| Existing binary discovery | PASS | Current binary exists and harmless --version succeeds |
| Native task first repaired start | PASS | 18:29:42 MYT start; checkpoint 18:29:53; exit 0 |
| Native repeat start | PASS | 18:35:01 MYT start; checkpoint 18:35:10; exit 0 |
| Current code selected | PASS | Status worker_source points to this worktree's report_worker.py |
| Native discovery | PASS | Status contains existing 22cfb1d302613eae/codex.exe and found=true |
| No inference | PASS | Explicit -NoAI/--no-ai; status no_ai, ai_enabled=false, ai_attempts=0 |
| Duplicate numerical catch-up | PASS | Both native passes templates_created=0; fixture pass creates 2 then 0 |
| Reservation history | PASS | All 4 existing attempt filenames and SHA-256 hashes unchanged |
| Native task safety | PASS | Exported Triggers/Principals/Settings XML matches original exactly |
| Live AI generation | NOT TESTED | Explicitly prohibited for this checkpoint; remains disabled |

Task is enabled/Ready in numerical-only mode, on the original 30-minute trigger.
Signed-in user, Interactive/Limited, no wake, IgnoreNew, StartWhenAvailable,
ten-minute timeout and 7 December expiry are preserved. Original task XML/action
and safe verification metadata are backed up under ignored
`data/launcher-repair-2026-11-02/`. No credentials are included in this audit.

## Regression verification

Focused launcher, worker, narrative/quota, interrupted-attempt and network fallback
regressions: **59 passed**. Tests use synthetic fixtures/mocked AI only, including
native PowerShell numerical-only execution against a temporary local database.
They verify explicit mutually exclusive modes, zero quota/provider calls, report
idempotency, removed-version discovery, current-checkout imports, existing quota
limits, durable reservations and saved numerical fallback. Ruff: **clean** across src/dashboard/tests. Final CLI return-path check: **1 passed**
after the lint-only fix. Dependency check: **no broken requirements**.
`git diff --check`: clean.

## Limits and handover

- This is successful native numerical-only operation, not proof of live AI output.
  A separate approved mode change is needed to resume guarded AI.
- Native passes used existing cloud records; they did not upload fixture telemetry.
  Numerical-only mode may sync/cache and save legitimate missing numerical reports.
- Both the current worktree and original runtime/data paths must remain available.
  Discovery handles normal app-version replacement; it cannot repair an absent
  Codex installation or protect against removal during an already-running pass.
- Current automatic scope remains the existing synthetic ESP32/device profile;
  genuine PZEM integration/model validation is a separate checkpoint.
- Windows must be awake and this user signed in. Sleep/resume was not tested here.
- No OLED, hardware/mains work, production model, paid API, public deployment,
  push/merge or unified ChatGPT progress-checker change occurred.
