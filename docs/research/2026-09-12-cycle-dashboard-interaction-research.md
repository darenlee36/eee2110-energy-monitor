# Kettle-cycle dashboard interaction research

- **Date:** 12 September 2026
- **Scope:** Dashboard interaction and visual direction only. Agentic AI reporting is explicitly deferred.
**Method:** Desk research against first-party product documentation, official Streamlit/Plotly documentation, and official accessibility/design-system guidance. This is design evidence, not a substitute for usability testing with household users.

## Recommendation

Replace the current generic measurement dashboard with a **cycle-first “kettle instrument”** built around two modes:

- **Live:** what the kettle is doing now.
- **Cycles:** one saved boiling cycle at a time, with history and comparison.

This mirrors the useful distinction between real-time and historical energy data in established monitors. Emporia exposes **Live/Past** directly above its usage chart, while Sense separates an instantaneous meter from device timelines and historical detail ([Emporia](https://help.emporiaenergy.com/en/articles/14646979-emporia-app-update-improved-usage-charts), [Sense live meter](https://help.sense.com/hc/en-us/articles/31802055183123-Dashboard-How-Do-I-Use-the-Meter-Feature), [Sense device timeline](https://help.sense.com/hc/en-us/articles/360061333894-Devices-Using-the-Device-Timeline-Feature)). For this project, the historical unit should be a **boiling cycle**, not an arbitrary time window.

## Proposed interaction model

### 1. Persistent command bar

Keep a compact top bar visible with:

- `Live | Cycles` segmented control.
- Connection label and icon: `Online`, `Stale`, or `Offline`.
- `Updated 8 seconds ago` timestamp.
- Auto-refresh control: `On · every 5 s` / `Paused`.
- Large **Refresh now** button.

Use a Streamlit fragment for the live region so only live data reruns every five seconds; the rest of the page and the selected cycle remain stable. Streamlit officially supports independent fragments with `run_every`, plus scoped reruns and Session State ([Streamlit fragments](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment)).

The pause control is important, not optional polish. WCAG guidance says automatically updating information presented with other content needs a way to pause, stop, hide, or control the update frequency ([W3C Pause, Stop, Hide](https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide)). When resumed, jump to the newest live state rather than replaying missed intermediate refreshes.

### 2. Live mode

Make the dominant element a single **cycle-state panel**, not six equal metric cards.

When idle:

- Large text: `Kettle idle`.
- Supporting text: `Waiting for the next boil`.
- Last completed cycle: time, duration, energy, and assessment.
- A quiet zero-power baseline rather than a “dead” empty screen.

When heating:

- Large state: `Heating` with elapsed time.
- Primary value: live power in watts.
- Secondary strip: voltage, current, cycle energy so far, reading quality.
- A short live power trace with a visible detection-threshold line.
- Text progress such as `1 min 42 s elapsed`; do not fabricate a percentage-to-boil because the system cannot know the remaining duration reliably.

When a cycle completes:

- Show a restrained completion toast: `Cycle saved · 2 min 37 s · 0.087 kWh`.
- Move immediately to a compact last-cycle summary without resetting the user’s history selection.

Sense’s official meter design emphasizes a live graph whose spikes and dips reveal appliance on/operate/off behaviour; that supports keeping the power trace central in Live mode ([Sense meter](https://help.sense.com/hc/en-us/articles/31802055183123-Dashboard-How-Do-I-Use-the-Meter-Feature)).

### 3. Cycles mode

Use a **master-detail** pattern:

- Left/top: filter chips and a selectable cycle table.
- Right/below: the selected cycle’s summary, graph, labels, and comparison.

Recommended filters:

- `All | Normal | Unusual | Incomplete`.
- `All volumes | 0.5 L | 1.0 L | 1.5 L | Unknown`.
- Recent period selector only if the cycle count becomes large.

Streamlit supports single-row dataframe selection with selection preserved when users sort the table, so a selected history row can drive the detail view without a custom frontend component ([Streamlit dataframe selections](https://docs.streamlit.io/develop/api-reference/data/st.dataframe)). The selected cycle ID, filters, and mode should be stored in Session State so refreshes do not reset context.

Emporia’s direct filter chips, tap-to-inspect values, and drill-down demonstrate that controls should stay near the chart instead of living in a hidden settings menu ([Emporia usage charts](https://help.emporiaenergy.com/en/articles/14646979-emporia-app-update-improved-usage-charts)).

### 4. Selected-cycle chart

Show one cycle using **elapsed time** on the horizontal axis and watts on the vertical axis:

- Solid warm-orange line/area: selected cycle.
- Dashed horizontal line: detection threshold, named in the legend.
- Start/end markers with text labels.
- Breaks or explicit markers for missing/invalid samples.
- Unified hover showing elapsed time, power, voltage, current, and cumulative cycle energy.
- Optional muted comparison band: median and expected range for cycles of the same labelled/predicted volume.

Plotly supports unified hover and formatted hover templates, and Streamlit can react to Plotly point/box/lasso selections when needed ([Plotly time series](https://plotly.com/python/time-series/), [Streamlit Plotly selections](https://docs.streamlit.io/develop/api-reference/charts/st.plotly_chart)). For this short 2–3 minute cycle, use hover and click-to-inspect; a range slider would add clutter and little value.

The comparison overlay must identify line styles and labels, not rely on color alone. W3C requires meaning to be conveyed by more than color, and meaningful graphical/UI cues should have at least 3:1 contrast against adjacent colors ([W3C Use of Color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color), [W3C Non-text Contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast)).

### 5. Cycle summary and comparison

Give the selected cycle a compact “receipt” hierarchy:

1. Duration, measured cycle energy, estimated energy charge.
2. Manual/predicted volume and model confidence.
3. Average/peak power and voltage/current statistics.
4. Assessment: Normal, Unusual, Incomplete, or Insufficient data.

Add one plain-language comparison, computed deterministically rather than generated by AI:

- `12 s longer than your median 1.0 L cycle`.
- `Energy is within the expected range for labelled 1.0 L cycles`.

Do not compare different volume classes by default. Do not display an anomaly score without a user-facing interpretation.

### 6. Optional water-volume labelling

After cycle completion, expose `0.5 L`, `1.0 L`, `1.5 L`, and `Unknown` as large pill controls, with **Save label** and **Skip**. Keep correction available in the selected-cycle detail.

- Display both values when available: `Predicted 1.0 L · 86% confidence` and `User label 0.5 L`.
- Never silently overwrite a manual label with a model prediction.
- If confidence is below the agreed threshold, display `Predicted volume: Unknown`.

Streamlit provides native pill controls, avoiding a custom inaccessible selector ([Streamlit pills](https://docs.streamlit.io/develop/api-reference/widgets/st.pills)). Controls should have comfortable touch targets; WCAG 2.2 defines a 24×24 CSS pixel minimum target, while larger controls reduce accidental activation ([W3C Target Size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum)).

### 7. Tariff provenance

Remove the arbitrary tariff input from the normal interface. Show a compact provenance row beneath the cycle charge:

`Estimate · TNB Domestic General · tariff version/effective date · AFA month · View calculation`

The calculation disclosure should show:

- Measured cycle kWh.
- Applied energy-rate component(s).
- Automatic Fuel Adjustment version/month where applicable.
- Result and rounding.
- Official source link and last-checked date.
- Disclaimer that this is a cycle energy estimate, not the complete household bill.

The Energy Commission states that the Peninsular Malaysia tariff consists of base tariff and a monthly Automatic Fuel Adjustment, and that RP4 runs from July 2025 through December 2027 ([Energy Commission IBR components](https://www.st.gov.my/pricing/electricity-pricing-framework/components-ibr)). Therefore, provenance/version metadata is part of accuracy, not footer decoration.

### 8. Progressive disclosure

Keep the household decision layer visible. Collapse specialist material:

- `How this cycle was detected`.
- `How this estimate was calculated`.
- `Device diagnostics`.
- `Raw readings`.
- `Model details` once models exist.

Do not hide cycle duration, energy, charge, assessment, data quality, or tariff identity. GOV.UK’s official design guidance recommends disclosure for secondary detail but warns against hiding information most users need ([GOV.UK Details](https://design-system.service.gov.uk/components/details/)). Streamlit expanders and popovers provide this without custom components and can lazily render their contents ([Streamlit expander](https://docs.streamlit.io/develop/api-reference/layout/st.expander), [Streamlit popover](https://docs.streamlit.io/develop/api-reference/layout/st.popover)).

## Visual direction: “warm technical instrument”

The existing interface feels bland because every metric has equal weight, every card uses the same grey treatment, and there is no visual representation of the kettle’s idle/heating/completed rhythm.

Use a restrained, recognisable visual system:

- **Canvas:** warm off-white (`#F7F3EC`) rather than clinical white.
- **Primary ink:** near-black charcoal (`#17201F`).
- **Heating accent:** burnt orange (`#D65A31`) for active power and cycle activity.
- **Instrumentation accent:** deep teal (`#0B6B69`) for controls, selected states, and trustworthy data.
- **Warning/error:** accessible amber/red, always paired with an icon and explicit text.
- **Shape:** one large rounded state panel, smaller cycle “receipt,” thin ruled separators, restrained shadows.
- **Typography:** readable sans-serif body; tabular numerals or bundled monospace only for measurements/timestamps. Use weight and scale to create hierarchy.
- **Graphic motif:** a subtle power-curve/steam-line motif in the hero panel, decorative only and never the sole carrier of status.
- **Motion:** no looping steam animation, pulsing cards, or flashing status. The changing data itself provides enough activity.

Streamlit supports configured fonts, metric sizing, separate light/dark themes, colors, borders, radii, and chart palettes through its official theme configuration ([Streamlit theming](https://docs.streamlit.io/develop/concepts/configuration/theming), [Streamlit colors and borders](https://docs.streamlit.io/develop/concepts/configuration/theming-customize-colors-and-borders), [Streamlit fonts](https://docs.streamlit.io/develop/concepts/configuration/theming-customize-fonts)). Prefer these supported controls over depending entirely on brittle internal CSS selectors.

## Accessibility requirements

- Never encode Idle/Heating/Complete/Unusual with color alone; include explicit text and a distinct icon/shape.
- Provide equivalent text for chart conclusions and current state; charts are non-text content ([W3C Non-text Content](https://www.w3.org/WAI/WCAG22/Understanding/non-text-content.html)).
- Make Refresh, pause, cycle rows, filter pills, and label controls keyboard operable and visibly focused.
- Keep interactive targets at least 24×24 CSS pixels; aim larger for the primary Refresh and volume controls.
- Announce important state changes such as `Cycle completed` and `Connection lost` as status messages without moving focus; WCAG requires status messages to be programmatically determinable ([WCAG 2.2, Status Messages](https://www.w3.org/TR/WCAG22/#status-messages)).
- Respect the user’s paused-refresh choice. Do not restart auto-refresh after selecting a cycle, opening diagnostics, or labelling volume.
- Avoid horizontal scrolling in the primary mobile view. A history table may scroll, but provide a tappable cycle-card alternative if mobile testing shows difficulty.

## Dashboard implementation priority

### First build

1. Live/Cycles navigation and persistent refresh controls.
2. Backend-derived Idle/Heating/Complete states.
3. Live state panel and selected-cycle receipt.
4. Single-row cycle-history selection.
5. One-cycle Plotly chart with threshold and sample-quality markers.
6. Manual volume pills and correction flow.
7. Tariff provenance disclosure.
8. Collapsed diagnostics/raw readings.
9. Warm technical theme, responsive layout, keyboard/contrast checks.

### Later dashboard enhancements

- Same-volume expected-range overlay after enough labelled cycles exist.
- Prediction confidence and correction once the volume classifier is trained.
- Deterministic anomaly explanation once a validated anomaly model exists.
- Export of selected-cycle data.

### Explicitly deferred

- Agentic AI reporting.
- Chat interface.
- LLM-written insights or recommendations.
- Automated PDF/report narratives.

The data model should retain stable cycle summaries, labels, provenance, and assessment fields so a reporting module can consume them later without redesigning the dashboard. No AI-report placeholder is needed in the first dashboard; it would compete with the core monitoring workflow before that workflow is validated.

## Sources

- [Emporia: improved usage charts](https://help.emporiaenergy.com/en/articles/14646979-emporia-app-update-improved-usage-charts)
- [Sense: power meter](https://help.sense.com/hc/en-us/articles/31802055183123-Dashboard-How-Do-I-Use-the-Meter-Feature)
- [Sense: device timeline](https://help.sense.com/hc/en-us/articles/360061333894-Devices-Using-the-Device-Timeline-Feature)
- [Sense: detailed usage views](https://help.sense.com/hc/en-us/articles/42270525299859-Using-Detailed-Usage-Views-to-Save-With-Sense)
- [Streamlit: fragments](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment)
- [Streamlit: Plotly chart selections](https://docs.streamlit.io/develop/api-reference/charts/st.plotly_chart)
- [Streamlit: dataframe selections](https://docs.streamlit.io/develop/api-reference/data/st.dataframe)
- [Streamlit: widgets](https://docs.streamlit.io/develop/api-reference/widgets)
- [Streamlit: layouts and containers](https://docs.streamlit.io/develop/api-reference/layout)
- [Streamlit: theming](https://docs.streamlit.io/develop/concepts/configuration/theming)
- [Plotly: time-series charts](https://plotly.com/python/time-series/)
- [Energy Commission: IBR components](https://www.st.gov.my/pricing/electricity-pricing-framework/components-ibr)
- [W3C: WCAG 2.2](https://www.w3.org/TR/WCAG22/)
- [W3C: Pause, Stop, Hide](https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide)
- [W3C: Use of Color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color)
- [W3C: Target Size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum)
- [GOV.UK Design System: Details](https://design-system.service.gov.uk/components/details/)
