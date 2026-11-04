# Energy Monitor dashboard v2 implementation checklist

Baseline `7c3d7a1` · branch `codex/dashboard-ui-v2`. Local review only.

The reference capture uses the 78 stored R1B readings. Their original timestamps
are preserved, so the local Live screenshot correctly shows Offline. Browser
checks use 1440 px desktop and 390 px phone viewports.

## UI1 — Live

- [x] **UI1.01** Increase the Energy Monitor heading size while preserving its font and colour. — app.py, styles.css; browser header and navigation.
- [x] **UI1.02** Remove the subtitle describing live telemetry, load-cycle detection and usage insights. — app.py, styles.css; browser header and navigation.
- [x] **UI1.03** Remove the visible Supabase/local-cache explanation. — app.py, styles.css; browser header and navigation.
- [x] **UI1.04** Remove the verbose data-source-label explanation. — app.py, styles.css; browser header and navigation.
- [x] **UI1.05** Preserve source filtering, Live/Cycles/Reports navigation, auto-refresh and Refresh now. Remove Owner controls globally as specified by the public-editing decision. — app.py, styles.css; browser header and navigation.
- [x] **UI1.06** Remove monitor ID and appliance-profile helper text from the primary Live layout. — components.py, dashboard_queries.py; R1B replay and Live browser.
- [x] **UI1.07** Remove verbose firmware and telemetry-origin descriptions from the primary layout. — components.py, dashboard_queries.py; R1B replay and Live browser.
- [x] **UI1.08** Move detailed simulation and provenance warnings to accessible technical details rather than displaying long paragraphs. — components.py, dashboard_queries.py; R1B replay and Live browser.
- [x] **UI1.09** Make the status panel substantially smaller. — components.py, dashboard_queries.py; R1B replay and Live browser.
- [x] **UI1.10** Use concise operational statuses: Connecting, Ready, Monitoring Load, Finalizing, Cycle Complete and Offline. — components.py, dashboard_queries.py; R1B replay and Live browser.
- [x] **UI1.11** Make status transitions consistent with fresh received telemetry and the existing detector state. Preserve offline/stale precedence. — components.py, dashboard_queries.py; R1B replay and Live browser.
- [x] **UI1.12** Remove redundant explanatory paragraphs, preserving essential freshness information in compact form. — components.py, dashboard_queries.py; R1B replay and Live browser.
- [x] **UI1.13** Keep the existing active-power, voltage and current metric cards and their authentic measurements. — components.py, dashboard_queries.py; R1B replay and Live browser.
- [x] **UI1.14** Preserve the existing Live Power chart. — components.py, styles.css; Live charts, tariff and viewport checks.
- [x] **UI1.15** Add Live Voltage and Live Current charts. — components.py, styles.css; Live charts, tariff and viewport checks.
- [x] **UI1.16** Use readable chart axes, consistent styling and recorded time references. — components.py, styles.css; Live charts, tariff and viewport checks.
- [x] **UI1.17** Display Cycle Duration and Measured Cycle Energy as adjacent tiles. — components.py, styles.css; Live charts, tariff and viewport checks.
- [x] **UI1.18** Display Estimated Gross Cycle Charge as a separate tile. — components.py, styles.css; Live charts, tariff and viewport checks.
- [x] **UI1.19** Include the applicable qualified TNB rate/tariff information underneath the charge, using actual tariff-engine results rather than an invented flat rate. — components.py, styles.css; Live charts, tariff and viewport checks.
- [x] **UI1.20** Add subtle illumination or accent styling to smaller summary tiles. — components.py, styles.css; Live charts, tariff and viewport checks.
- [x] **UI1.21** Use restrained illuminated borders, side accents or soft glow distinct from the status panel. — components.py, styles.css; Live charts, tariff and viewport checks.
- [x] **UI1.22** Preserve the existing Cycle Energy chart. — components.py; measured cycle traces and scoped comparison.
- [x] **UI1.23** Support multiple completed-cycle curves on the same chart. — components.py; measured cycle traces and scoped comparison.
- [x] **UI1.24** Align comparison curves by elapsed cycle time, preserving actual duration and recorded samples. — components.py; measured cycle traces and scoped comparison.
- [x] **UI1.25** Use distinguishable lines, legends, hover details and accessible comparison selection. — components.py; measured cycle traces and scoped comparison.
- [x] **UI1.26** Remove repetitive frequency-line and chart-colour explanations. — components.py; power-quality chart and reset regression.
- [x] **UI1.27** Remove redundant small telemetry descriptions below the chart. — components.py; power-quality chart and reset regression.
- [x] **UI1.28** Preserve frequency and power-factor cards, chart, axes, colours and Reset view control. — components.py; power-quality chart and reset regression.

## UI2 — Cycles

- [x] **UI2.01** Remove the Owner controls expander entirely. — app.py, cloud_edits.py, storage.py and annotation migration; RPC and history tests.
- [x] **UI2.02** Provide password-free public cycle labeling and non-destructive editing using validated server-side write operations. — app.py, cloud_edits.py, storage.py and annotation migration; RPC and history tests.
- [x] **UI2.03** Remove unnecessary instructions around the cycle-history table. — app.py, cloud_edits.py, storage.py and annotation migration; RPC and history tests.
- [x] **UI2.04** Preserve status filters, label selection, history table and cycle selection. — app.py, cloud_edits.py, storage.py and annotation migration; RPC and history tests.
- [x] **UI2.05** Remove redundant small explanatory text. — components.py; R1B cycle summary preview.
- [x] **UI2.06** Keep the selected-cycle heading, metric values, units and summary cards. — components.py; R1B cycle summary preview.
- [x] **UI2.07** Preserve the Power chart, detection threshold and cycle-boundary indicators. — app.py, components.py; elapsed-time charts, threshold and tariff preview.
- [x] **UI2.08** Add Voltage versus Elapsed Time. — app.py, components.py; elapsed-time charts, threshold and tariff preview.
- [x] **UI2.09** Add Current versus Elapsed Time. — app.py, components.py; elapsed-time charts, threshold and tariff preview.
- [x] **UI2.10** Use consistent elapsed-time axes, interactivity, colours and Reset view controls. — app.py, components.py; elapsed-time charts, threshold and tariff preview.
- [x] **UI2.11** Preserve How this estimate was calculated and all genuine calculation details. — app.py, components.py; elapsed-time charts, threshold and tariff preview.
- [x] **UI2.12** Remove the small explanatory paragraph below the power chart, retaining necessary interpretation details in tooltips or legends. — app.py, components.py; elapsed-time charts, threshold and tariff preview.

## UI3 — Reports

- [x] **UI3.01** Remove small introductory text beneath the Reports title. — app.py; Reports navigation and saved report preview.
- [x] **UI3.02** Remove verbose history descriptions. — app.py; Reports navigation and saved report preview.
- [x] **UI3.03** Remove redundant viewing/generation/Owner controls explanations. — app.py; Reports navigation and saved report preview.
- [x] **UI3.04** Remove repetitive report-generation helper captions. — app.py; Reports navigation and saved report preview.
- [x] **UI3.05** Preserve the Reports heading. — app.py; Reports navigation and saved report preview.
- [x] **UI3.06** Preserve the Saved Report dropdown. — app.py; Reports navigation and saved report preview.
- [x] **UI3.07** Remove the Owner controls expander globally. — app.py; Reports navigation and saved report preview.
- [x] **UI3.08** Keep the Monitor / Appliance selector where required. — app.py; Reports navigation and saved report preview.
- [x] **UI3.09** Move the long sensor-origin/calibration explanation out of the main layout. — components.py; R1B performance tiles and tariff detail.
- [x] **UI3.10** Remove repetitive electrical-average and cost paragraphs; preserve essential tariff qualifications. — components.py; R1B performance tiles and tariff detail.
- [x] **UI3.11** Remove the redundant standalone Cycle Completed text. — components.py; R1B performance tiles and tariff detail.
- [x] **UI3.12** Preserve the Cycle Performance heading and all nine measured-value tiles. — components.py; R1B performance tiles and tariff detail.
- [x] **UI3.13** Replace the three vertically stacked charts with one selectable chart. — components.py; measurement selector, exact R1B points and gap regression.
- [x] **UI3.14** Provide Power, Voltage, Current and Energy in a dropdown. — components.py; measurement selector, exact R1B points and gap regression.
- [x] **UI3.15** Update chart title, units, axis and data when selection changes. — components.py; measurement selector, exact R1B points and gap regression.
- [x] **UI3.16** Default to Power throughout the cycle. — components.py; measurement selector, exact R1B points and gap regression.
- [x] **UI3.17** Preserve hover, zoom and Reset view. — components.py; measurement selector, exact R1B points and gap regression.
- [x] **UI3.18** Preserve the Reading Values expandable table. — components.py; measurement selector, exact R1B points and gap regression.
- [x] **UI3.19** Remove unnecessary text below charts without deleting invalid-sample or data-gap handling. — components.py; measurement selector, exact R1B points and gap regression.
- [x] **UI3.20** Preserve Insights & Next Steps as a separate section. — components.py; rule-based presentation and historical report regression.
- [x] **UI3.21** Make What the Readings Show, Possible Explanations and What to Try Next larger and bolder. — components.py; rule-based presentation and historical report regression.
- [x] **UI3.22** Format the three subsections as readable, visually separated information groups. — components.py; rule-based presentation and historical report regression.
- [x] **UI3.23** Replace the small current-analysis note with a clearly identified DISCLAIMER appropriate to the selected report's actual analysis mode. — components.py; rule-based presentation and historical report regression.
- [x] **UI3.24** Generate observations from the selected cycle's actual measurement evidence, quality and assessment. — components.py; rule-based presentation and historical report regression.
- [x] **UI3.25** Use condition-dependent possible explanations and actions. Avoid generic repeated advice when specific evidence supports something more useful. — components.py; rule-based presentation and historical report regression.
- [x] **UI3.26** Include the planned model classification and unusualness score presentation. — components.py; rule-based presentation and historical report regression.
- [x] **UI3.27** Support evidence-grounded AI-assisted insights after the relevant reporting and model functionality is validated and explicitly enabled. — components.py; rule-based presentation and historical report regression. Future activation requires separately approved, validated evidence.
- [x] **UI3.28** Do not overwrite or retrospectively misrepresent saved historical report evidence. — components.py; rule-based presentation and historical report regression.
- [x] **UI3.29** When an actual trained model is available, remove the obsolete statement about the model being untrained. Before then, show an accurate Not Evaluated status. — components.py, app.py; saved model evidence and demo-only preview.
- [x] **UI3.30** Keep concise, accurate Training Status, Model Version and Data Provenance indicators. — components.py, app.py; saved model evidence and demo-only preview.
- [x] **UI3.31** Preserve the Anomaly Score card, Preview the Score Display expander and Technical Details and Saved Evidence expander. — components.py, app.py; saved model evidence and demo-only preview.

## Release checks

- [ ] Review the local preview and separately approve deployment.
- [ ] Apply the additive public-annotation migration; verify both RPCs are server-only.
- [ ] Release the dashboard and reload the hosted worker if stale imports appear.
- [ ] Verify the 0.0470 kWh R1B cycle and saved report, then check public annotations in an approved test scope.
- [ ] Record post-release status and keep model activation and firmware changes separate.

## Rollback

Revert the dashboard commits on the release branch and restart Streamlit to
restore the previous UI. The annotation migration is additive. Leave the
annotation history in place so a rollback does not erase public notes.
If writes must be suspended immediately, remove the server role's execute
permission from the public annotation save function after the previous UI is
restored. Leave the R1B report function and its saved records intact.
