# Energy Monitor: laptop-first reporting design

Date: 20 October 2026. Status: user approved the direction with cycle-based reporting;
implementation plan review is next. Not implemented.

## Agreed intent and scope

The user approved a laptop-based reporting workflow first, with independent cloud
generation considered later. Reports must appear in the existing Energy Monitor
dashboard. Hardware completion is not a prerequisite for developing this workflow.
Use labelled simulated records for development, never as real sensor validation or
model-performance evidence. Preserve one validated appliance profile as the FYP scope.

This design implements the reporting role already described in
`FYP_Product_Development_Handoff.md`, section 11. It does not replace that architecture.

## Alternatives and decision

1. **Selected: Python evidence and template reports, then a laptop Codex worker.**
   This preserves the existing stack, provides an inspectable fallback, and avoids
   introducing a paid application API now. Local generation depends on the laptop
   and app being available; saved cloud reports remain viewable independently.
2. **Deferred: independently hosted AI generation.** This removes the laptop
   dependency but requires a separate runtime, authentication and cost decision.
3. **Not sufficient: reports stored only in the laptop cache.** A hosted dashboard
   cannot reliably retrieve those files, and cloud restarts must not erase history.

## Target for the first implementation batch

Every completed cycle receives a deterministic individual report when the laptop
reporting worker next processes it. Owners can additionally request summaries of
the latest 5, 10 or 20 completed cycles from the same device/profile. These numbers
are cycle counts, not seconds. A selected completed cycle can also be processed
manually without waiting for the worker. Persist the evidence and text and display
saved reports in a Reports dashboard section.
The complete path must pass tests and a browser check. A template report must be
identified as a template, not as AI-generated. Actual Codex generation and unattended
scheduling follow this verified foundation in a separate batch.

## Existing structure and minimum additions

- Reuse `software/src/energy_monitor/storage.py` and the existing cloud sync path.
- Reuse cycle summaries, provenance and tariff calculations; do not recalculate
  scientific measurements inside an AI prompt.
- Add one reporting module for evidence construction, deterministic report rendering
  and report validation. Use existing Python/Pydantic dependencies.
- Add report persistence methods to the existing storage interfaces and a small
  reporting entry point for laptop execution. Do not add a second API service.
- Add a Reports branch to `software/dashboard/app.py`, preserving Live and Cycles.
  Use existing Streamlit components and styling; no dashboard redesign.
- Add focused reporting tests and extend the existing dashboard tests.

**Complexity justification:** Neither checked-in migrations nor the live Supabase
schema contains a reports table (verified read-only on 20 October). Local SQLite
also has no report-history interface. One `reports` table in each storage backend
is necessary for durable, cross-session history; no extra queue or job table is needed.

## Evidence contract

Each report is scoped to one profile, one device and exact source cycle IDs. An
individual report contains exactly one completed cycle. A batch contains exactly
5, 10 or 20 completed cycles; if fewer exist, show the available count and do not
silently create a smaller batch. Select batches by end time descending, with cycle
ID as a stable tie-breaker, and display evidence chronologically. Never group
different devices/profiles. Show provenance separately even within the same batch.

Derive the reporting period from the earliest selected start and latest selected
end. These are coverage metadata, not an additional arbitrary time-window selector.
Store UTC and display Malaysia time. Do not add a calendar/time-window reporting
mode to this first batch.

The evidence snapshot includes reporting version, source cycle IDs, device/profile,
window, data-origin and record-source counts, completed/active/incomplete and quality
counts, eligible energy totals, qualified tariff estimates and limitations.

- Never include active or incomplete cycles in a completed-cycle report or batch.
  Keep quality warnings on selected completed cycles visible; exclude cycles with
  invalid or missing readings/gaps from energy and cost totals and explain why.
- Keep simulated, sensor, manual, mixed and unknown sources distinguishable. Never
  present a combined total as verified sensor consumption. Manual values are entered
  values, not measurements recovered from raw telemetry.
- Preserve existing cycle energy and tariff conventions. Do not sum cumulative meter
  readings. Retain ranges, missing tariff components and unresolved household context;
  never describe the result as an official household bill.
- A sensor firmware label does not prove calibration. No profile has a trained
  Isolation Forest yet: report `not trained` and `not evaluated`, not a zero anomaly
  count, a model score, or a healthy verdict. Existing rule assessments are not ML results.
- Empty data produces a clear no-data report, not invented zero consumption or a fault.
- Labels and notes are untrusted text. Do not execute them or use them as instructions.

## Persistence and access

Persist report ID, profile/device, kind (`cycle` or `batch`), requested cycle count,
coverage period, generated time, reporting version,
engine (`template` initially, `codex` later), status, canonical evidence hash, evidence
snapshot and report text. Keep enough snapshot detail to reproduce every displayed
number even if labels, tariffs or later data change.

Use one additive migration for `public.reports`, with RLS enabled and no direct anon
or authenticated write access. Server-side code may read reports and write validated
report records. It must not gain permission to alter telemetry or models through a
reporting endpoint. Keep keys in existing secret storage, never in the browser or firmware.

Mirror report history in SQLite for offline use. Cloud mode uses Supabase as the durable
authority, not the local analytical cache. Retry of identical version/window/source
evidence must return the same saved report, not create duplicates. New evidence may
create a new versioned snapshot; do not overwrite a previously generated report silently.

Only the owner or the laptop reporting entry point may generate/persist reports.
Public visitors can read saved reports without a password. The owner gate applies
only to generation/editing, not to opening the dashboard. No public AI spending trigger.

## Dashboard behaviour

Add Reports beside Live and Cycles. List saved reports with kind/count, period, generation time,
source, engine and status. Opening a report shows its text, limitations and inspectable
evidence. Clearly identify template fallback and simulated data. Owners can select
a completed cycle or choose a 5/10/20-cycle batch. Public visitors cannot start report
generation. Laptop worker scans for completed cycles without a matching report and
generates missing reports; it must not depend on a browser session staying open.

Show explicit empty-history, cloud-unavailable and validation-error states. Continue
showing an available cached report with a cache warning when cloud access fails.
Do not imply report generation runs continuously inside Streamlit or while a laptop
worker is offline. Existing refresh and owner controls keep their current meanings.

## Later Codex and scheduling boundary

The Codex stage receives structured evidence, not privileged database credentials.
It drafts an explanation; ordinary application code validates and persists the result.
The narrative cannot change telemetry, labels, models, baselines or appliance state.
Unsupported numerical or physical-fault claims fail validation and retain an honest
error status plus the deterministic fallback. Do not claim this design guarantees
that an LLM never makes mistakes.

Test one manual Codex run before creating a reporting schedule. Confirm cadence and
maximum AI calls per run before scheduling. Keep this separate from the existing 10:00 AM
software-progress checker. Observe account usage and the user's 5% remaining cutoff;
do not create an unrestricted recurring AI workload.

Every completed cycle gets its numerical/template report regardless of AI quota.
Codex enhancement may be pending, unavailable or failed without blocking that report.
Batch summaries reuse saved evidence and do not request regeneration of constituent
AI reports. Keep a per-snapshot/version completion marker so unchanged evidence is
not repeatedly sent to Codex. Unlimited per-cycle AI calls are not part of this design.

## Acceptance and verification

1. Deterministic tests cover individual reports, exact 5/10/20 batches, insufficient
   history, stable ordering, incomplete cycles, mixed-device, provenance and untrained-model cases. Numbers and warnings trace
   to saved source evidence; repeated inputs produce identical canonical evidence.
2. SQLite persistence/reopen and duplicate-generation tests pass. Cloud tests verify
   report durability and rejection of client-role writes without mutating telemetry.
3. Dashboard tests verify Reports navigation, public read-only access, owner-only
   generation, source/engine labels, evidence display and failure states. Existing
   Live/Cycles tests continue to pass.
4. Run the full Python suite and Ruff after implementation, then verify the manual
   report path in a browser. Capture the report and its simulated-data disclosure.
5. Stop at a verified manual reporting checkpoint. Do not claim AI generation,
   unattended scheduling, real-data evaluation or milestone 10 completion prematurely.

## Review and execution gate

The user's cycle-based amendment is incorporated here. The implementation plan
must be reviewed and executed in this existing chat unless
the user chooses another method. No product code, database migration, new automation
or external API billing has been changed while preparing this design.
