# Cycle-First Household Kettle Dashboard Design

- **Date:** 12 September 2026
- **Status:** Approved design, awaiting written-spec review
- **Project:** EEE2110 single-appliance energy monitor
- **Primary appliance:** Household electric kettle

## 1. Purpose

Redesign the existing Streamlit prototype around complete kettle boiling cycles rather than an arbitrary window of telemetry. The dashboard must explain what the kettle is doing now, preserve each completed cycle as one meaningful record, estimate the variable TNB energy charge attributable to that cycle, and collect optional water-volume labels for later model training.

The first implementation remains software-only and uses synthetic telemetry. It must not add mains wiring instructions, claim that an anomaly is a physical fault, or imply that a cycle estimate is a complete household electricity bill.

## 2. Confirmed decisions

- The monitored appliance is one household kettle.
- The system samples every five seconds. A typical two-to-three-minute boil therefore contains approximately 24–36 readings; it is one cycle, not one reading.
- Cycle detection runs in the Python processing/backend layer, not in ESP32 firmware and not inside the dashboard.
- The dashboard has both automatic refresh every five seconds and a visible **Refresh now** control.
- Auto-refresh can be paused and remains paused until the user resumes it.
- Historical energy and cost are calculated for a selected cycle, not for the number of readings currently visible.
- The normal user interface uses a versioned TNB Domestic General configuration. It does not expose an arbitrary RM/kWh input.
- Water volume is optional. A user can label a completed cycle as `0.5 L`, `1.0 L`, `1.5 L`, or `Unknown`.
- A later classifier may predict those volume classes. It will return `Unknown` when confidence is insufficient, and a manual label always takes precedence.
- Anomaly comparison is volume-specific when an effective volume is known.
- Agentic AI reporting is deferred until the dashboard, cycle summaries, tariff provenance, and anomaly evidence are stable.
- The visual direction is based on the supplied Pinterest reference, [Neon Tides in Motion](https://www.pinterest.com/pin/731483164523710728/): flowing neon ribbons over a near-black canvas. The implementation recreates the visual language with original CSS gradients and shapes; it does not copy, embed, or download the source artwork.

## 3. Scope

### 3.1 First dashboard release

The first release includes:

1. A backend cycle-detection state machine.
2. Persistent cycle summaries in SQLite and Supabase-compatible SQL.
3. `Live` and `Cycles` dashboard modes.
4. Cycle-specific energy, duration, electrical statistics, data quality, and charge estimate.
5. Automatic refresh, pause/resume, and manual refresh.
6. Selectable and filterable cycle history.
7. A one-cycle interactive power chart.
8. Optional manual water-volume labelling.
9. A versioned tariff calculation interface with source and effective-date metadata.
10. Responsive neon-dark visual styling and required accessibility behaviour.

### 3.2 Explicitly deferred

- Training or deploying the water-volume classifier.
- Training or deploying Isolation Forest anomaly detection.
- Same-volume baseline bands until enough valid labelled cycles exist.
- Agentic AI reports, chat, LLM-written insights, or automated narrative PDFs.
- Appliance control, remote switching, boil completion prediction, or a fabricated percentage-to-boil.
- Exact water-volume regression.
- Complete household-bill replication without household-level monthly consumption and eligibility data.
- Real PZEM hardware integration, UART pin assignments, or mains-side work.

## 4. Success criteria

The redesign is successful when:

- The interface never describes a telemetry sample or an arbitrary chart window as a boiling cycle.
- A synthetic two-to-three-minute boil is automatically grouped into one cycle.
- The active view shows live power, voltage, and current as instantaneous readings and clearly labels energy and charge as “so far.”
- A completed-cycle view shows the measured energy and variable energy charge for that cycle only.
- The user can refresh without losing the selected mode, cycle, filters, or paused state.
- Interrupted or low-quality data produces an `Incomplete` or `Insufficient data` result instead of a confident estimate.
- Tariff identity, effective dates, AFA period, official source, calculation details, exclusions, and last-checked date are visible through progressive disclosure.
- A manual volume label is saved and remains authoritative over any later prediction.
- The primary desktop and mobile views are readable, keyboard operable, and do not rely on colour alone.
- Synthetic anomaly labels remain visibly identified as simulation data.

## 5. System architecture

```text
PZEM/simulator
    -> ESP32 five-second readings
    -> six-reading / thirty-second batch
    -> versioned ingestion API
    -> raw telemetry store
    -> cycle processor
         -> cycle summary
         -> tariff calculation
         -> later volume classifier
         -> later volume-specific anomaly model
    -> Streamlit dashboard
    -> later read-only agentic reporting
```

### 5.1 Component boundaries

| Component | Responsibility | Depends on | Must not do |
|---|---|---|---|
| Telemetry ingestion | Validate and store immutable readings idempotently | Telemetry contract, storage adapter | Detect cycles or render UI |
| Cycle detector | Convert ordered valid readings into active/completed/incomplete cycles | Raw readings, profile detection settings | Calculate tariffs or diagnose faults |
| Cycle summariser | Calculate cycle energy, duration, statistics, and quality | Cycle membership, raw readings | Invent missing measurements |
| Tariff calculator | Calculate the attributable variable energy charge and return provenance | Cycle kWh, effective tariff version, AFA version | Claim to reproduce the complete bill |
| Volume labelling service | Save and audit user labels | Cycle ID, allowed classes | Overwrite labels with predictions |
| Later volume classifier | Predict one allowed volume class or `Unknown` | Completed valid cycle features, model version | Delay the first dashboard release |
| Later anomaly service | Compare a cycle with an appropriate baseline | Cycle features, effective volume, model version | Confirm a physical fault |
| Dashboard | Query prepared live/cycle data and present interactions | Read/query services | Recompute domain rules differently from the backend |
| Later reporting agent | Produce cautious read-only summaries from evidence packages | Stable cycles, comparisons, provenance | Control the kettle or alter source data |

The local SQLite path and future Supabase PostgreSQL path share the same domain fields. Storage-specific code remains behind repository methods so domain calculations can be tested without Streamlit.

## 6. Telemetry and cycle data flow

1. The simulator or future ESP32 creates a reading every five seconds.
2. Six readings are uploaded in one idempotent batch every thirty seconds.
3. Ingestion stores each reading using `(device_id, sample_sequence)` as the unique identity.
4. After an ingestion batch is committed, the API invokes the cycle processor. The processor consumes unprocessed readings in `(recorded_at, sample_sequence)` order for each `(device_id, profile_id)`.
5. The detector updates one persisted cycle state: `idle`, `heating`, or `closing`.
6. Once the stop condition is confirmed, the processor stores a completed cycle summary and its reading boundaries.
7. A long telemetry gap, restart ambiguity, or inadequate valid data closes the active cycle as `incomplete`.
8. The tariff calculator uses the completed cycle’s measured kWh and the tariff version effective at the cycle timestamp.
9. The dashboard queries the current state, recent cycles, the selected cycle, and its readings.
10. A saved manual volume label updates the selected cycle’s effective volume without changing raw telemetry.

Processing must be idempotent. Re-running the processor over the same readings must update or reproduce the same cycle rather than create duplicates.

## 7. Automatic cycle detection

### 7.1 State machine

The detector operates per appliance profile:

```text
IDLE
  -> candidate start after a valid reading crosses the start threshold
  -> HEATING after the required consecutive start readings

HEATING
  -> continue while readings represent kettle operation
  -> candidate stop after a valid reading falls below the stop threshold
  -> COMPLETE after the required consecutive stop readings
  -> INCOMPLETE after a terminating data gap or unrecoverable quality condition
```

### 7.2 Recommended software-simulation defaults

| Setting | Default | Reason |
|---|---:|---|
| Expected sample interval | 5 s | Locked telemetry cadence |
| Start threshold | 1,000 W | Safely below the simulated kettle’s heating power and above idle noise |
| Consecutive start readings | 2 | Reject one-sample spikes while detecting within about 10 seconds |
| Stop threshold | 100 W | Separates kettle-off behaviour from heating |
| Consecutive stop readings | 3 | Confirms shutdown over about 15 seconds |
| Warning gap | More than 15 s | Indicates at least two expected samples are absent |
| Terminating gap | More than 60 s | Active cycle cannot be closed confidently |
| Minimum completed heating duration | 30 s | Rejects short test spikes as completed boils |
| Maximum cycle duration | 10 min | Prevents a stuck state from remaining active indefinitely |

These are deterministic development defaults, not claimed calibrated PZEM thresholds. They live in the appliance profile and must be calibrated against real kettle/PZEM data before physical validation. Changing them does not require a firmware release.

### 7.3 Boundary rules

- The cycle starts at the first reading in the confirmed start sequence.
- The cycle ends at the last heating reading before the confirmed stop sequence.
- Invalid readings are retained for traceability but do not satisfy start or stop confirmation.
- A short gap marks the cycle’s quality but does not automatically discard it.
- A terminating gap, maximum-duration breach, sequence reversal, or processor restart without enough context produces `incomplete`.
- Below-minimum activity is retained only as rejected detection evidence in diagnostics; it is not shown as a normal completed kettle cycle.
- The current firmware `appliance_state` may assist debugging, but backend power-and-quality rules are authoritative for cycle boundaries.

## 8. Cycle calculations

### 8.1 Energy

Cycle energy is derived from positive changes in the PZEM cumulative-energy counter between the cycle’s boundary readings. Positive deltas are summed so a controller or meter reset does not create negative energy.

Trapezoidal integration of active power over sample time is calculated as a validation cross-check. It does not silently replace the meter-derived value. The software-simulation tolerance is 20% relative difference. If the two methods differ beyond that tolerance, the cycle is flagged for review and its quality explanation shows the discrepancy. The tolerance is stored with the detection/calculation version and must be recalibrated using reference-meter evidence before physical validation.

An incomplete cycle may show measured energy so far, but its assessment and charge confidence must remain incomplete.

### 8.2 Stored summary features

Each cycle stores or deterministically derives:

- Start and end timestamps.
- Start and end sample sequences.
- Duration in seconds.
- Meter-derived energy in kWh.
- Power-integrated validation energy in kWh.
- Average and peak active power.
- Average voltage and current.
- Minimum and maximum voltage.
- Average power factor.
- Total, valid, invalid, and missing sample counts.
- Gap count and largest gap duration.
- Cycle status and assessment status.
- Detection-settings version.
- Tariff-calculation reference.
- Manual volume, predicted volume, prediction confidence, effective volume, and model version when applicable.
- Anomaly status, evidence summary, and model version when applicable.

Raw telemetry remains the source of truth. Cycle summaries can be rebuilt when processing rules change, with the rule version retained for auditability.

## 9. Data model changes

### 9.1 `appliance_profiles` and `cycle_detection_versions`

Keep appliance identity in `appliance_profiles`. Add `cycle_detection_versions` as a separate immutable configuration table containing the settings in Section 7.2, the energy cross-check tolerance, an effective timestamp, and a calibration status of `simulation_default` or `hardware_validated`. Each cycle references the exact detection version used.

### 9.2 `appliance_cycles`

Create a cycle table with:

- Stable `cycle_id` UUID.
- Device and profile identifiers.
- Start/end timestamps and sample sequences.
- `status`: `active`, `completed`, or `incomplete`.
- `assessment`: `not_evaluated`, `normal`, `unusual`, or `insufficient_data`.
- Calculated summary features and quality counts.
- `detection_version` and timestamps.
- Optional tariff-calculation ID.
- Optional predicted/effective volume fields.
- Optional anomaly/model fields reserved for later processors.
- A uniqueness rule that prevents duplicate cycles for the same device and starting sample.

### 9.3 `cycle_volume_labels`

Store manual labels separately as an audit trail:

- Label ID and cycle ID.
- Allowed value: `0.5_l`, `1.0_l`, `1.5_l`, or `unknown`.
- Creation time and source value (`dashboard` for the first release).
- Superseded timestamp or active flag for corrections.

The newest active manual label becomes the effective volume. If no manual label exists, a sufficiently confident model prediction may become effective. Otherwise the effective volume is `unknown`.

### 9.4 `tariff_versions` and `cycle_cost_estimates`

Tariff records contain:

- Provider and scheme: `TNB`, `Domestic General`.
- Version identifier.
- Effective start/end dates.
- Variable tariff components and calculation rules.
- AFA month/version and applicable rate or rule.
- Official source URLs.
- Source publication date and local last-checked timestamp.
- Currency and rounding rules.

Cycle estimates store the cycle ID, tariff version, measured kWh, included variable components, excluded components, unrounded result, displayed result, calculation timestamp, and validity status.

### 9.5 Local and cloud parity

SQLite migrations mirror the required tables and constraints for local development. Supabase migrations use PostgreSQL types, foreign keys, indexes, row-level security, and read-only dashboard access. The service role or Edge Function performs writes; browser/dashboard credentials never receive unrestricted table-write access.

## 10. TNB cycle charge design

The dashboard reports **Estimated cycle energy charge**, not “electricity bill” and not an arbitrary user-set price.

The calculator:

1. Selects the TNB Domestic General tariff version effective when the cycle occurred.
2. Applies the variable energy-related components that can be attributed to the cycle’s measured kWh.
3. Applies the relevant monthly AFA version when available and applicable.
4. Returns the result with its tariff/AFA provenance and rounding details.
5. Returns `unavailable` if a required tariff version is missing, expired, or internally inconsistent.

Fixed monthly charges, taxes, fund contributions, rebates, incentives, minimum charges, eligibility rules, and household-usage tiers cannot always be allocated accurately to one kettle cycle. They are excluded unless the calculator has the household-level inputs needed for that rule. The calculation disclosure lists every included and excluded component.

Visible provenance reads in this form:

`Estimate · TNB Domestic General · tariff effective date · AFA month · View calculation`

The disclosure includes measured kWh, each included component, exclusions, result, rounding, official source links, and last-checked date. The public dashboard has no free-form tariff-rate input. Development fixtures may inject a test tariff through configuration or test code only.

## 11. Water-volume strategy

### 11.1 First release

After a cycle completes, the user may select one of four large pills: `0.5 L`, `1.0 L`, `1.5 L`, or `Unknown`. Saving is optional, and **Skip** is always available. A label can later be corrected from the selected-cycle view.

### 11.2 Later classifier

The later model is a four-class classifier, not exact-volume regression. Candidate inputs are duration, measured energy, average/peak power, voltage/current summaries, power factor, and shape-related cycle features. It must be trained only after sufficient manually labelled, good-quality cycles are collected.

Model output includes class, confidence, model version, and inference time. A configurable confidence threshold controls whether a class is shown; below it, the result is `Unknown`. The chosen threshold must be validated with held-out labelled cycles before it is treated as production-ready.

### 11.3 Precedence

```text
active manual label
    > sufficiently confident prediction
    > Unknown
```

The interface shows both manual and predicted values when both exist and never silently overwrites a human correction.

## 12. Dashboard information architecture

### 12.1 Persistent command bar

The command bar contains:

- `Live | Cycles` segmented navigation.
- Connection icon and explicit `Online`, `Stale`, or `Offline` text.
- Relative update time such as `Updated 8 seconds ago`.
- `Auto-refresh on · every 5 s` or `Paused`.
- A clear pause/resume control.
- A large **Refresh now** button.

Mode, selected cycle, filters, auto-refresh preference, and open disclosures use Streamlit Session State. Refreshing live data must not reset the user’s context.

### 12.2 Live mode

The dominant state panel has three presentations.

**Idle**

- `Kettle idle` and `Waiting for the next boil`.
- Quiet zero-power trace.
- Last completed cycle time, duration, energy, and assessment.

**Heating**

- `Heating` and elapsed time.
- Live active power as the primary reading.
- Voltage, current, cycle energy so far, estimated cycle charge so far, and data quality as secondary readings.
- Short live power trace with the detection threshold.
- No estimated completion time or percentage boiled.

**Cycle complete**

- A non-blocking status message such as `Cycle saved · 2 min 37 s · 0.087 kWh`.
- A compact last-cycle receipt.
- Optional volume-labelling controls.

Live power, voltage, and current are explicitly described as the latest instantaneous measurements. Energy and cost use `so far` only while a cycle is active.

### 12.3 Cycles mode

Use a responsive master-detail layout:

- Desktop: history and filters on the left/top; selected-cycle details on the right/below.
- Mobile: filters, tappable cycle cards/table, then the selected-cycle details in one vertical flow.

Filters are:

- `All | Normal | Unusual | Incomplete`.
- `All volumes | 0.5 L | 1.0 L | 1.5 L | Unknown`.

One history row represents one cycle. Columns prioritise start time, duration, energy, estimated charge, effective volume, assessment, and data quality. Selecting a row updates the receipt and chart without changing the filters.

### 12.4 Selected-cycle receipt

Information order is:

1. Duration, measured energy, and estimated cycle energy charge.
2. Manual/predicted/effective water volume and confidence where applicable.
3. Average/peak power and voltage/current statistics.
4. Assessment and data-quality explanation.
5. One deterministic comparison with similar effective-volume cycles when a valid baseline exists.

Comparison language is factual, for example `12 s longer than the median of your valid 1.0 L cycles`. Different volume classes are not compared by default.

### 12.5 Selected-cycle chart

The Plotly chart uses elapsed seconds on the horizontal axis and watts on the vertical axis. It includes:

- A neon blue-to-pink selected-cycle line with a restrained translucent fill.
- A labelled dashed detection-threshold line.
- Start and end markers.
- Visible gaps or invalid-sample markers.
- Unified hover with elapsed time, power, voltage, current, and cumulative cycle energy.
- Textual chart summary for users who cannot interpret the visual chart.
- A later optional same-volume median/range overlay only after sufficient valid labelled data exists.

The chart omits a range slider because a two-to-three-minute cycle does not require it. The Plotly mode bar remains hidden unless a tested interaction requires it.

### 12.6 Progressive disclosure

Keep cycle duration, energy, charge, assessment, quality, and tariff identity visible. Place secondary technical detail in collapsed sections:

- How this cycle was detected.
- How this estimate was calculated.
- Device diagnostics.
- Raw readings.
- Model details, once models exist.

Controller battery is omitted from the primary interface unless the final physical controller genuinely uses a battery. When present, it belongs under device diagnostics.

## 13. Visual system

### 13.1 Direction

The visual language is an original dashboard treatment inspired by the supplied neon-wave reference:

- Near-black/navy canvas.
- Large static flowing ribbons that blend cobalt blue, violet, hot pink, coral, and small mint/cyan highlights.
- Subtle grain or texture to prevent flat digital gradients.
- Smoked-charcoal translucent surfaces with thin low-contrast borders.
- White primary text and cool muted secondary text.
- Restrained glows around selected controls, active charts, and the heating state.

The background is generated with CSS gradients, masks, pseudo-elements, and blur. It does not require an external image request and does not reproduce the original artwork.

### 13.2 Semantic palette

| Role | Direction |
|---|---|
| Canvas | Near-black/navy |
| Surface | Smoked charcoal with high text contrast |
| Primary data/control | Electric cyan or cobalt |
| Heating/activity | Coral to hot-pink gradient |
| Normal | Accessible green plus icon/text |
| Warning/incomplete | Accessible amber plus icon/text |
| Unusual/error | Accessible red plus icon/text |

Neon colour is decorative or reinforcing; it is never the only carrier of meaning. Strong saturation stays mostly in the background edges, state accent, selected controls, and chart traces so the readings remain legible.

### 13.3 Layout and typography

- One large state panel dominates Live mode.
- The cycle receipt is the second visual anchor.
- Cards use consistent radii, fine separators, limited shadows, and generous spacing.
- Measurements use tabular numerals.
- Labels and explanations use a highly readable sans-serif font.
- Primary values scale strongly; voltage/current/supporting statistics remain secondary.
- Desktop uses balanced columns; mobile collapses into one column without primary horizontal scrolling.

### 13.4 Motion

The background remains static. State changes may use a brief, non-essential transition, but there are no looping waves, flashing alerts, pulsing cards, or continuous steam effects. `prefers-reduced-motion` disables optional transitions.

## 14. Refresh and interaction behaviour

- The live fragment refreshes every five seconds when enabled.
- **Refresh now** updates immediately whether automatic refresh is active or paused.
- Pausing persists across mode changes, cycle selection, filtering, disclosures, and volume labelling.
- Resuming jumps to current data; it does not replay missed refreshes.
- The selected historical cycle remains stable while the live fragment refreshes.
- A new completed cycle produces a status message but does not forcibly replace a historical cycle the user is inspecting.
- If no historical selection exists, the newest completed cycle is selected by default.
- Completion, disconnection, and save outcomes are announced as status messages without moving keyboard focus.

## 15. Error and empty states

| Condition | Required behaviour |
|---|---|
| No telemetry | Explain how to start the local API and simulator; show no fabricated metrics |
| Device offline | Show `Disconnected`, last successful reading time, and low-voltage/Wi-Fi troubleshooting only |
| Stale data | Retain the latest value but label it stale and show its age |
| Invalid sample | Preserve it in diagnostics, exclude it from confirmation/statistics as defined, and explain the quality code |
| Short gap | Mark reduced quality and show missing-sample evidence |
| Long gap during heating | Close the cycle as `Incomplete` at the last trustworthy boundary |
| Tariff missing/expired | Show cycle kWh; show charge as `Unavailable`; never fall back to a guessed rate |
| Volume unlabeled | Show `Unknown`; retain the optional labelling prompt |
| Low prediction confidence | Show `Unknown — insufficient confidence` |
| No same-volume baseline | Show `Not enough similar labelled cycles for comparison` |
| Unusual cycle | Say it differs from the relevant recorded baseline and does not confirm a physical fault |
| Refresh/query failure | Keep the previous valid screen, show a non-blocking error, and allow retry |
| Label save failure | Keep the user’s selected value locally, clearly show it was not saved, and offer retry |

## 16. Accessibility requirements

- State and severity always use explicit text and an icon/shape in addition to colour.
- Body text, controls, charts, borders that convey state, and focus indicators meet applicable WCAG 2.2 AA contrast requirements.
- Refresh, pause/resume, mode tabs, filters, history selection, disclosures, and volume pills are keyboard operable with visible focus.
- Interactive targets meet at least 24×24 CSS pixels and primary controls aim larger.
- Important state changes use programmatically determinable status messages.
- The chart has a nearby textual conclusion and all non-decorative controls have accessible names.
- Decorative neon ribbons are ignored by assistive technology.
- Auto-refresh has a persistent pause mechanism.
- Primary mobile content requires no horizontal scrolling at 320 CSS pixels; a wide raw-data table may scroll inside its own labelled region.
- Zooming and browser text scaling must not obscure controls or values.

## 17. Security, privacy, and safety boundaries

- Dashboard access is read-only for raw telemetry and generated cycle summaries, except for narrowly scoped volume-label writes.
- Supabase row-level security and service-role separation remain mandatory.
- Secrets never appear in firmware source, dashboard client code, repository files, logs, or screenshots.
- Manual labels do not contain personal data.
- The software controls no appliance and sends no switching command.
- The interface provides no exposed-mains construction or wiring guidance.
- Hardware integration remains dependent on supervisor/laboratory approval and appropriately rated enclosed equipment.
- Anomaly and volume predictions include model/version provenance and uncertainty. They are decision support, not fault diagnosis.

## 18. Testing strategy

### 18.1 Unit tests

Test the following independently:

- Start/stop confirmation and exact boundary selection.
- Rejection of one-sample spikes.
- A normal two-to-three-minute cycle.
- Minimum/maximum duration rules.
- Short gaps, terminating gaps, invalid samples, sequence reversal, and meter reset.
- Idempotent reprocessing and restart recovery.
- Meter-delta energy calculation and power-integration cross-check.
- Summary statistics and quality counts.
- Tariff-version selection, component calculation, exclusions, rounding, expired/missing versions, and AFA changes.
- Manual volume validation, correction audit, and precedence over prediction.
- Low-confidence prediction falling back to `Unknown` when the classifier is later added.
- Assessment wording that never claims a confirmed physical fault.

### 18.2 Storage and integration tests

- Apply SQLite and PostgreSQL/Supabase migrations to clean databases.
- Verify foreign keys, uniqueness constraints, indexes, and permitted status values.
- Ingest duplicate batches and prove they do not create duplicate readings or cycles.
- Send simulator data through HTTP ingestion, processing, cycle storage, and dashboard query.
- Verify a cycle spanning multiple thirty-second batches is still one cycle.
- Verify manual labels persist without changing telemetry.
- Verify tariff estimates retain the exact tariff version used.

### 18.3 Dashboard tests

- Empty, idle, heating, completed, incomplete, stale, offline, invalid-data, and tariff-unavailable states render without exceptions.
- Live measurements, `so far` language, completed-cycle totals, and tariff provenance match backend results.
- Refresh, pause/resume, navigation, filters, selection, disclosures, and volume save/correction work.
- Auto-refresh does not reset a selected historical cycle or filters.
- Synthetic anomalies retain their simulation label.
- The arbitrary tariff input, `Energy in view`, and unqualified `Estimated cost` are absent from the main interface.

### 18.4 Visual and accessibility checks

- Browser inspection at representative desktop and mobile widths, including 320 CSS pixels.
- Keyboard-only walkthrough and visible-focus verification.
- Automated contrast checks plus manual review of text over translucent/neon backgrounds.
- Status-message verification with accessibility semantics.
- Reduced-motion verification.
- No horizontal scrolling in primary mobile content.
- Background and decorative effects do not obscure readings under zoom or text scaling.

### 18.5 Performance and resilience

- The five-second fragment refresh does not rerun or reset unrelated historical content.
- A practical history limit prevents large raw telemetry queries on each refresh.
- Cycle lists are paginated or date-limited when volume grows.
- Query and processing failures leave the last valid UI state available.

## 19. Implementation sequence

This design will later be expanded into a separate implementation plan. The planned order is:

1. Add failing tests for cycle detection and summaries.
2. Implement cycle domain models and the detector/summariser.
3. Add SQLite and Supabase-compatible cycle, label, and tariff migrations.
4. Add tariff fixtures and the versioned calculation service.
5. Extend the simulator with deterministic full-cycle scenarios and failures.
6. Add repository/query methods for live state, history, selected cycles, and labels.
7. Replace the dashboard’s time-window metrics with the Live/Cycles information architecture.
8. Implement refresh state, filters, master-detail selection, and volume labelling.
9. Apply the original neon-wave CSS theme and responsive states.
10. Run unit, integration, dashboard, browser, responsive, and accessibility verification.

## 20. Future agentic reporting contract

Agentic reporting remains outside the first implementation. The later reporting module may read a deterministic evidence package containing:

- Stable cycle ID and timestamps.
- Cycle summary and data-quality evidence.
- Effective water volume and its source/confidence.
- Same-volume baseline comparison.
- Anomaly assessment, model version, and cautious interpretation.
- Tariff version and calculation provenance.
- Explicit limitations and missing-data flags.

The agent is read-only. It cannot alter telemetry, labels, models, thresholds, or appliance state. A deterministic template report remains available if the AI service is unavailable. Scheduled reporting frequency will be implemented only after the cycle dashboard and evidence package are validated.

## 21. Acceptance checklist

- [ ] Automatic detection groups one typical simulated boil into one cycle.
- [ ] Cycle history uses one row/card per cycle.
- [ ] Live readings and cycle totals use unambiguous labels.
- [ ] Active-cycle energy and charge say `so far`.
- [ ] Completed-cycle energy comes from cycle boundaries, not the viewport.
- [ ] TNB tariff and AFA provenance are versioned and visible.
- [ ] Missing tariff data never triggers an arbitrary fallback rate.
- [ ] Manual volume labelling and correction work.
- [ ] The interface supports later predicted volume without making it mandatory.
- [ ] Refresh now, five-second auto-refresh, pause, and state preservation work.
- [ ] One-cycle chart displays threshold, boundaries, and bad/missing data.
- [ ] Incomplete and unusual cycles use cautious, explicit wording.
- [ ] The supplied neon-wave reference is reflected through original CSS styling.
- [ ] Desktop/mobile, keyboard, contrast, status, and reduced-motion checks pass.
- [ ] Agentic reporting remains deferred and no placeholder chat/AI panel appears.
- [ ] No mains wiring instructions or unsafe hardware claims are introduced.

## 22. Design evidence

- [Kettle-cycle dashboard interaction research](../../research/2026-09-12-cycle-dashboard-interaction-research.md)
- [Streamlit fragments](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment)
- [Streamlit dataframe selections](https://docs.streamlit.io/develop/api-reference/data/st.dataframe)
- [Streamlit Plotly charts](https://docs.streamlit.io/develop/api-reference/charts/st.plotly_chart)
- [Energy Commission electricity-pricing framework](https://www.st.gov.my/pricing/electricity-pricing-framework/components-ibr)
- [TNB tariff information](https://www.mytnb.com.my/tariff/index.html)
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/)
- [Pinterest visual reference: Neon Tides in Motion](https://www.pinterest.com/pin/731483164523710728/)
