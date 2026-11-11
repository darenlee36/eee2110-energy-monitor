from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime, time
from decimal import Decimal
from statistics import fmean
from uuid import UUID
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from energy_monitor.cloud_edits import SupabaseCycleStore
from energy_monitor.dashboard_queries import (
    CATALOG_PATH,
    CycleDetail,
    CycleListItem,
    LiveView,
    get_cycle_detail,
)
from energy_monitor.metrics import sampling_gap_counts
from energy_monitor.models import (
    ConnectionState,
    CycleStatus,
    ManualCycleRecord,
    TariffEstimate,
    TelemetryReading,
)
from energy_monitor.report_narrative import FAILURE_MESSAGES, narrative_sections
from energy_monitor.storage import SQLiteTelemetryStore
from energy_monitor.tariffs import load_tariff_catalog, tariff_calculation_rates

MALAYSIA_TZ = ZoneInfo("Asia/Kuala_Lumpur")
ANOMALY_ADVISORY_THRESHOLD = 60.0
CHART_FONT = "Cascadia Code, SFMono-Regular, Consolas, Liberation Mono, monospace"
REPORT_SOURCES = {
    "simulated": ("Demonstration data", "#E22DFF"),
    "sensor": ("Sensor-recorded data", "#3BD7FF"),
    "manual": ("Manually entered data", "#FFC46B"),
    "mixed": ("Mixed-origin data", "#72F6C7"),
    "unknown": ("Unverified origin", "#A8B4D0"),
}


def malaysia_time(value: datetime | str) -> datetime:
    """Presentation only: legacy naive timestamps mean UTC, never host local time."""
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(MALAYSIA_TZ)


def format_myt(value: datetime | str, fmt: str = "%d %b, %H:%M:%S") -> str:
    return malaysia_time(value).strftime(fmt) + " MYT"


def _report_source(cycle: dict) -> str:
    return "manual" if cycle["record_source"] == "manual" else cycle["data_origin"]


REPORT_MEASUREMENTS = {
    "Power": ("active_power_w", "W", "#3BD7FF"),
    "Voltage": ("voltage_v", "V", "#FFC46B"),
    "Current": ("current_a", "A", "#E22DFF"),
    "Energy": ("cumulative_energy_kwh", "kWh", "#72F6C7"),
}


def build_report_trace(cycle: dict, measurement: str) -> go.Figure | None:
    """Plot one measurement from the saved snapshot, preserving invalid samples and gaps."""
    readings = cycle.get("readings", [])
    valid = [r for r in readings if r["quality_status"] == "valid"]
    if not valid:
        return None
    field, unit, color = REPORT_MEASUREMENTS[measurement]
    start = datetime.fromisoformat(cycle["started_at"])
    baseline = valid[0]["cumulative_energy_kwh"]
    span = max(0, valid[-1]["cumulative_energy_kwh"] - baseline)
    first_increment = max(0, cycle["meter_energy_kwh"] - span)
    if measurement == "Energy" and any(
        b[field] < a[field] for a, b in zip(valid, valid[1:], strict=False)
    ):
        return None
    elapsed, values = [], []
    # Old saved report snapshots omit cadence/sequence metadata. Preserve their
    # five-second display policy unless repeated timestamps establish a cadence.
    gap_counts = sampling_gap_counts(readings, legacy_seconds=(
        5 if not any(r.get("firmware_version") for r in readings) else None))
    previous_time = None
    for index, reading in enumerate(readings):
        timestamp = datetime.fromisoformat(reading["timestamp"])
        if previous_time is not None and gap_counts[index]:
            elapsed.append(None)
            values.append(None)
        elapsed.append((timestamp - start).total_seconds())
        value = reading[field] if reading["quality_status"] == "valid" else None
        if value is not None and measurement == "Energy":
            value = first_increment + max(0, value - baseline)
        values.append(value)
        previous_time = timestamp
    figure = go.Figure(go.Scatter(
        x=elapsed, y=values, mode="lines+markers", connectgaps=False,
        line={"color": color, "width": 3}, marker={"size": 4}, name=measurement,
        hovertemplate="%{x:.0f} s<br>%{y:.5g} " + unit + "<extra></extra>",
    ))
    figure.update_layout(
        **_transparent_chart_layout(285),
        title={"text": f"{measurement} throughout the cycle", "x": 0.02},
        xaxis={"title": "Elapsed (s)", "automargin": True},
        yaxis={"title": f"{measurement} ({unit})", "automargin": True},
        showlegend=False,
    )
    return figure


def build_report_comparison(cycles: list[dict], cycle_names: list[str]) -> list[go.Figure]:
    figures = []
    cycle_ids = [str(cycle["cycle_id"]) for cycle in cycles]
    sources = list(dict.fromkeys(_report_source(c) for c in cycles))
    for field, title, unit in (
        ("meter_energy_kwh", "Energy used by cycle", "Energy (kWh)"),
        ("duration_seconds", "Duration by cycle", "Duration (s)"),
    ):
        figure = go.Figure()
        for source in sources:
            group = [i for i, cycle in enumerate(cycles) if _report_source(cycle) == source]
            label, color = REPORT_SOURCES[source]
            figure.add_trace(go.Bar(
                x=[cycle_ids[i] for i in group],
                y=[cycles[i][field] if field != "meter_energy_kwh"
                   or cycles[i]["eligible_for_totals"] else None for i in group],
                customdata=[cycle_names[i] for i in group],
                name=label, marker_color=color,
                hovertemplate="%{customdata}<br>%{y:.5g} " + unit
                + "<extra>%{fullData.name}</extra>",
            ))
        figure.update_layout(
            **_transparent_chart_layout(330), title={"text": title, "x": 0.02},
            xaxis={"type": "category", "categoryorder": "array", "categoryarray": cycle_ids,
                   "tickmode": "array", "tickvals": cycle_ids, "ticktext": cycle_names,
                   "automargin": True},
            yaxis={"title": unit, "rangemode": "tozero", "automargin": True},
            legend={"orientation": "h", "y": -0.35},
        )
        figures.append(figure)
    return figures


def render_anomaly_assessment(report: dict) -> None:
    """Render model evidence already saved with a report."""
    model = report["evidence_snapshot"]["model"]
    predictions = model.get("predictions", []) if model.get("status") == "trained" else []
    evaluated = any(p.get("anomaly_score") is not None for p in predictions)
    if len(predictions) == 1 and predictions[0]["anomaly_score"] is not None:
        prediction = predictions[0]
        st.metric("Anomaly score", f"{prediction['anomaly_score']:.1f} / 100")
        st.progress(prediction["anomaly_score"] / 100,
                    text="Higher = more unusual against the normal baseline")
        st.write("Classification: " + prediction["classification"].title())
        st.caption("Baseline percentile, not a probability of equipment failure.")
        comparisons = [{"Measurement": key.replace("_", " ").title(),
                        "Recorded": value["value"], "Baseline median": value["baseline_median"],
                        "Difference (%)": value["percent_delta"]}
                       for key, value in prediction["comparisons"].items()]
        st.dataframe(pd.DataFrame(comparisons), hide_index=True, width="stretch")
        st.caption("Differences describe the baseline comparison; they do not prove a cause.")
    elif len(predictions) > 1:
        st.caption("Per-cycle assessments; no invented combined appliance-health score.")
        st.dataframe(pd.DataFrame([
            {"Cycle": i + 1, "Score (0–100)": p["anomaly_score"],
             "Classification": p["classification"].title()}
            for i, p in enumerate(predictions)]), hide_index=True, width="stretch")
        if not any(p["anomaly_score"] is not None for p in predictions):
            st.metric("Anomaly score", "Unavailable")
    else:
        st.metric("Anomaly score", "Unavailable")
    messages = {
        "insufficient_data": "Not enough reviewed normal sensor cycles to train a baseline.",
        "test_only_model": "A test-only model cannot assess real appliance behaviour.",
        "quality_excluded": "This cycle has unsuitable data or unverified/simulated origin.",
        "scope_mismatch": "The model requires matching appliance, monitor "
                          "and recorded operating conditions.",
        "training_cycle": "This cycle was used in training; it is not an independent evaluation.",
    }
    if not evaluated:
        st.caption("Classification: Not Evaluated")
    reasons = {messages[p["status"]] for p in predictions if p["status"] in messages}
    for message in sorted(reasons):
        st.caption(message)
    if any("configured_model_unavailable" in p["quality_warnings"] for p in predictions):
        st.warning("Configured model unavailable; the numerical report is retained.")
    training = model.get("status", "not_trained").replace("_", " ")
    version = model.get("model_version") or "Unavailable"
    st.caption(f"Training status: {training} · Model version: {version}")
    sources = sorted(report["evidence_snapshot"]["source_counts"])
    st.caption("Data provenance: " + ", ".join(sources))


def render_cycle_assessment(
    cycle: dict, store: SQLiteTelemetryStore | None, *, show_insights: bool = False,
) -> tuple[dict | None, dict] | None:
    from energy_monitor.adaptive_warning import assess_warning
    from energy_monitor.experimental_if import (
        assess_current_model,
        assess_selected_cycle,
        current_rolling_power_comparison,
        rolling_power_comparison,
    )

    st.subheader("Anomaly score")
    try:
        row, release = assess_selected_cycle(cycle, store)
    except ValueError as exc:
        st.info(f"Not Evaluated. {exc}")
        return
    except (OSError, ImportError, KeyError, TypeError):
        st.info("Not Evaluated. The experimental model or cycle evidence is unavailable.")
        return

    rolling = None
    try:
        rolling = current_rolling_power_comparison(row, release, store)
    except (ValueError, KeyError, TypeError):
        st.caption("The current rolling baseline is unavailable.")
    current = None
    if rolling is not None:
        try:
            current = assess_current_model(row, rolling, release)
        except (ValueError, KeyError, TypeError, ImportError):
            st.info("Current Isolation Forest score unavailable for this cycle.")
    reference_prediction, reference_model = current or (row, release["model"])
    try:
        calibrated = assess_warning(cycle, row)
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        calibrated = None
        st.caption("Saved adaptive warning policy unavailable for this cycle.")
    policy = calibrated["policy"] if calibrated else None
    if calibrated:
        reference_prediction, reference_model = calibrated, policy["model"]
    insight = model_pattern_insight(row, reference_prediction, reference_model, policy)
    role = ("included in model fitting" if row["cycle_id"] in reference_model["training_cycle_ids"]
            else "unseen at model fitting")
    warning_status = (
        f"Calibrated warning cutoff {policy['threshold']:.2f} / 100."
        if policy else "Adaptive warning calibration pending."
    )
    insight["summary"] = (
        f"Isolation Forest score {reference_prediction['score']:.2f} / 100, "
        f"dashboard advisory cutoff {ANOMALY_ADVISORY_THRESHOLD:g} / 100. "
        f"This cycle was {role} for model {reference_model['artifact_sha256'][:12]}. "
        f"{warning_status}"
    )
    if current is not None:
        prediction, model = current
        style = "alert" if not policy and insight["pattern"] == "unusual" else "standard"
        with st.container(key=f"if-score-{style}-current-{row['cycle_id']}"):
            st.metric("Current Isolation Forest anomaly score", f"{prediction['score']:.2f} / 100")
    if policy:
        style = "standard" if insight["pattern"] == "normal" else "alert"
        with st.container(key=f"if-score-{style}-frozen-{row['cycle_id']}"):
            st.metric("Frozen warning-model score", f"{calibrated['score']:.2f} / 100")

    if insight["pattern"] == "warning":
        st.error(
            f"**Potential anomalous appliance behaviour** · Score {calibrated['score']:.2f} "
            f"/ 100 · adaptive threshold {policy['threshold']:.2f} / 100. "
            "Review the readings and operating conditions before drawing conclusions.",
            icon="⚠️",
        )
    elif insight["pattern"] == "unusual":
        score = f" · {row['score']:.2f} / 100" if current is None and not policy else ""
        st.warning(
            f"**Unusual pattern**{score} · Compare the heating time and power with a similar run.",
            icon="⚠️",
        )
    else:
        st.success(
            f"**Normal pattern** · Below the {ANOMALY_ADVISORY_THRESHOLD:g} / 100 advisory cutoff.",
            icon="✅",
        )

    if current is not None:
        st.caption(
            f"Experimental · {len(model['training_cycle_ids'])} eligible training cycles · "
            f"model {model['artifact_sha256'][:12]}"
        )
        with st.expander("How the current anomaly score was calculated"):
            st.write(
                f"The 200-tree Isolation Forest fits the current pool of "
                f"{len(model['training_cycle_ids'])} verified Complete cycles using duration "
                "and measured mean power. Scores refresh when the eligible pool changes."
            )
            st.markdown(
                f"`−100 × ({prediction['raw_score']:.9f}) = {prediction['score']:.2f}`"
            )
            st.caption("The selected cycle contributed to this fit. Independent detection "
                       "accuracy has yet to be established.")
    if policy:
        st.caption(f"Adaptive warning threshold: {policy['threshold']:.2f} / 100")
    else:
        st.caption("Adaptive warning threshold: calibration pending")
    with st.expander("Advisory cutoff and adaptive warning"):
        st.write(
            f"Score {reference_prediction['score']:.2f} / 100 · "
            f"model {reference_model['artifact_sha256'][:12]}."
        )
        member = row["cycle_id"] in reference_model["training_cycle_ids"]
        st.caption("In-sample training cycle." if member else "Unseen at model fitting.")
        st.write(
            f"The dashboard shows Unusual pattern at {ANOMALY_ADVISORY_THRESHOLD:g} / 100 "
            "or higher. The adaptive warning cutoff uses scores from independently reviewed "
            "normal cycles held out of the warning fit."
        )
        if policy:
            st.caption(
                f"{policy['percentile']}th-percentile upper rank with ties excluded · "
                f"{policy['calibration_count']} held-out normal cycles across "
                f"{policy['calibration_session_count']} sessions · "
                f"{policy['method']} · policy {policy['artifact_sha256'][:12]} · "
                f"calibrated {format_myt(policy['calibrated_at'])}."
            )
        else:
            st.caption("No eligible published calibration applies to this cycle.")
    if rolling is not None:
        power = row["features"]["average_power_w"]
        mean = rolling["mean_power_w"]
        difference = rolling["power_change_w"]
        change_pct = 100 * difference / mean
        st.metric("Power deviation from rolling baseline", f"{change_pct:+.1f}%")
        st.metric(
            f"Current rolling baseline · {rolling['cycle_count']} eligible cycles",
            f"{mean:,.1f} W",
        )
        st.caption(f"Cycle mean {power:,.1f} W · Difference {difference:+,.1f} W")
        with st.expander("Rolling baseline deviation details"):
            st.write(
                f"The current baseline averages the measured mean power of "
                f"{rolling['cycle_count']} Complete cycles with verified sensor readings. "
                "Each eligible cycle contributes once, including the selected cycle "
                f"when it qualifies. This cycle averaged {power:,.1f} W."
            )
            st.markdown(
                f"`({power:,.1f} W − {mean:,.1f} W) ÷ {mean:,.1f} W × 100 "
                f"= {change_pct:+.1f}%`"
            )
            st.caption(f"Baseline version {rolling['version']}.")
            try:
                original = rolling_power_comparison(row, release, store)
            except (ValueError, KeyError, TypeError):
                original = None
            if original is not None:
                st.caption(
                    f"Original comparison · {original['cycle_count']} earlier cycles · "
                    f"{original['mean_power_w']:,.1f} W · {original['version']}."
                )
    measurements = [
        ("Duration", row["features"]["duration_seconds"], "duration_seconds", "s"),
        ("Mean active power", row["features"]["average_power_w"], "average_power_w", "W"),
        ("Meter energy", row["energy_kwh"], "energy_kwh", "kWh"),
    ]
    with st.expander("Original Isolation Forest assessment"):
        model = release["model"]
        st.metric("Original Isolation Forest anomaly score", f"{row['score']:.2f} / 100")
        role = "Training-reference cycle" if row["reference_member"] else "Unseen at model fitting"
        st.caption(
            f"{role} · {len(model['training_cycle_ids'])} frozen training cycles · "
            f"{model['model_version']} · {model['artifact_sha256'][:12]}"
        )
        st.dataframe([{
            "Measurement": label, "Cycle": f"{value:.3f} {unit}",
            "Training reference median": f"{release['statistics'][key]['median']:.3f} {unit}",
        } for label, value, key, unit in measurements], hide_index=True, width="stretch")
        st.caption({
            "frozen_reference": "This cycle contributes to the frozen training reference.",
            "pending_review": "Training eligibility awaits independent review.",
            "quarantined_unusual": "Held for review because the model flagged this observation.",
            "quarantined_reference_shift": (
                "Held for review because a feature shifted substantially."
            ),
        }[row["learning_disposition"]])
        st.caption("Higher scores indicate greater model unusualness. Independent detection "
                   "accuracy has yet to be established.")
        st.markdown(
            f"1. Use **{row['features']['duration_seconds']:g} s** of cycle duration and "
            f"**{row['features']['average_power_w']:,.1f} W** of sampled mean active power."
        )
        st.markdown(
            f"2. Evaluate those two measurements with the "
            f"**{model['settings']['n_estimators']}-tree** forest fitted to "
            f"**{len(model['training_cycle_ids'])}** reference cycles. "
            "Observations that are easier to isolate receive a more negative raw score."
        )
        st.markdown(
            f"3. Multiply the raw score by −100 and bound it to 0–100. "
            f"`−100 × ({row['raw_score']:.9f}) = {row['score']:.2f}`."
        )
        st.caption(f"Mapping {release['score_mapping']}. Energy is shown separately.")
    if show_insights:
        st.subheader("Insights & next steps")
        for messages in cycle_presentation_insights(cycle, insight).values():
            for message in messages:
                st.write(message)
    return rolling, insight


def model_pattern_insight(row: dict, prediction: dict, model: dict,
                          policy: dict | None = None) -> dict:
    reference = model["training_reference"]["cycles"]
    duration_mean = fmean(c["features"]["duration_seconds"] for c in reference)
    power_mean = fmean(c["features"]["average_power_w"] for c in reference)
    duration_delta = row["features"]["duration_seconds"] - duration_mean
    power_delta = row["features"]["average_power_w"] - power_mean
    duration_pct, power_pct = 100 * duration_delta / duration_mean, 100 * power_delta / power_mean
    pattern = (
        "unusual" if prediction["score"] >= ANOMALY_ADVISORY_THRESHOLD else "normal"
    )
    if policy and prediction["score"] >= policy["threshold"]:
        pattern = "warning"
    return {
        "pattern": pattern, "duration_led": abs(duration_pct) >= abs(power_pct),
        "comparison": (
            f"Compared with this model's training means, duration was "
            f"{duration_delta:+.1f} s ({duration_pct:+.1f}%) and sampled mean active "
            f"power was {power_delta:+,.1f} W ({power_pct:+.1f}%)."
        ),
    }


def rolling_power_insights(cycle: dict, rolling: dict) -> list[str]:
    mean = rolling["mean_power_w"]
    delta = rolling["power_change_w"]
    voltage = cycle.get("average_voltage_v")
    context = f" The recorded mean voltage was {voltage:.1f} V." if voltage else ""
    return [
        f"Compared with the current mean of {rolling['cycle_count']} verified cycles, "
        f"this cycle's mean power changed by {delta:+,.1f} W ({100 * delta / mean:+.1f}%).",
        context.strip() or "Mean voltage is unavailable for this comparison.",
    ]


def render_report_dashboard(
    report: dict, store: SQLiteTelemetryStore | None, *, cycle_names: dict[str, str],
) -> dict | None:
    """Render the saved report and return the displayed single-cycle assessment."""
    evidence = report["evidence_snapshot"]
    cycles = evidence["cycles"]
    names = [cycle_names[str(cycle["cycle_id"])] for cycle in cycles]
    if len(cycles) > 1:
        st.subheader(f"{len(cycles)}-cycle overview")
        for source, total in evidence["totals_by_source"].items():
            st.caption(REPORT_SOURCES[source][0])
            cells = st.columns(3)
            cells[0].metric("Cycles included in totals", total["cycle_count"])
            cells[1].metric("Report energy", f"{float(total['energy_kwh']):.5f} kWh")
            cost = "Unavailable" if total["charge_low_rm"] is None else (
                f"RM {float(total['charge_low_rm']):.3f}–{float(total['charge_high_rm']):.3f}"
            )
            cells[2].metric("Estimated report cost", cost)
        for index, figure in enumerate(build_report_comparison(cycles, names)):
            render_resettable_chart(figure, chart_key=f"report_batch_{report['report_id']}_{index}")
        st.caption(
            "Cycles are ordered by completion time. Quality-excluded cycles have no energy bar; "
            "duration still describes the recorded cycle. Totals stay separate by data origin."
        )
    if evidence["excluded_quality_count"]:
        st.warning(
            f"{evidence['excluded_quality_count']} cycle(s) excluded from energy/cost totals."
        )
    selected = st.selectbox(
        "Cycle readings", range(len(cycles)),
        format_func=lambda i: (
            f"{names[i]} · " + format_myt(cycles[i]["started_at"], "%d %b, %H:%M")
        ),
        key=f"report_cycle_{report['report_id']}",
    ) if len(cycles) > 1 else 0
    cycle = cycles[selected]
    binding=cycle.get("collection_context")
    if binding:
        if binding["scope"]:
            scope=binding["scope"]
            st.caption(f"Recorded water: {scope['water_volume_ml']:,g} mL · "
                       f"Starting water: {scope['start_temperature_band'].title()} · "
                       f"{scope['operating_condition'].replace('_',' ').title()}")
            st.caption(f"Recording session: {binding['session_id']}")
        if binding["dataset_kind"] == "synthetic_fixture":
            st.caption("Collection annotations are synthetic fixtures, not real kettle evidence.")
        if binding["status"] != "bound":
            st.warning("Collection context is unavailable, mismatched or awaiting review. "
                       "Numerical readings are retained; model scoring is blocked.")
    st.subheader(f"{names[selected]} performance")
    if _report_source(cycle) != "sensor":
        st.caption(REPORT_SOURCES[_report_source(cycle)][0])
    cells = st.columns(3)
    seconds = cycle["duration_seconds"]
    cells[0].metric("Cycle duration", f"{seconds // 60}m {seconds % 60:02d}s")
    cells[1].metric("Energy used", f"{cycle['meter_energy_kwh']:.5f} kWh")
    estimate = TariffEstimate.model_validate(cycle["tariff_estimate"])
    cells[2].metric("Estimated cycle cost", _charge_value(estimate))
    cells[2].caption("Qualified estimate · see tariff details")
    manual = cycle["record_source"] == "manual"
    valid = [r for r in cycle.get("readings", []) if r["quality_status"] == "valid"]
    frequency = fmean(r["frequency_hz"] for r in valid) if valid and not manual else None
    for row in (
        [("Average power", "average_power_w", ",.0f", "W"),
         ("Peak power", "peak_power_w", ",.0f", "W"),
         ("Average current", "average_current_a", ".2f", "A")],
        [("Average voltage", "average_voltage_v", ".1f", "V"),
         ("Average power factor", "average_power_factor", ".3f", ""),
         ("Average frequency", None, ".2f", "Hz")],
    ):
        cells = st.columns(3)
        for cell, (label, field, precision, unit) in zip(cells, row, strict=True):
            value = None if manual else frequency if field is None else cycle[field]
            cell.metric(
                label, "Unavailable" if value is None else f"{value:{precision}} {unit}".strip()
            )
    if not cycle["eligible_for_totals"]:
        st.warning(
            "This cycle's recorded values are shown, but excluded from report energy/cost totals."
        )
    st.subheader("Recorded readings")
    if cycle.get("readings") and not manual:
        measurement = st.selectbox(
            "Measurement", list(REPORT_MEASUREMENTS),
            key=f"report_measurement_{report['report_id']}_{selected}",
        )
        figure = build_report_trace(cycle, measurement)
        if figure is not None:
            render_resettable_chart(
                figure, chart_key=f"report_trace_{report['report_id']}_{selected}_{measurement}"
            )
        else:
            st.warning(f"{measurement} trace unavailable: check saved reading quality or resets.")
        with st.expander("Reading values"):
            frame = pd.DataFrame(cycle["readings"])
            frame["timestamp"] = frame["timestamp"].map(format_myt)
            st.dataframe(frame.rename(columns={
                "timestamp": "Recorded at (MYT)", "voltage_v": "Voltage (V)",
                "current_a": "Current (A)", "active_power_w": "Power (W)",
                "cumulative_energy_kwh": "Meter counter (kWh)",
                "frequency_hz": "Frequency (Hz)", "power_factor": "Power factor",
                "quality_status": "Reading quality",
            }), hide_index=True, width="stretch")
    else:
        st.info(
            "No saved reading trace available. "
            "Older/manual reports retain their recorded summary only."
        )
    # The immutable report may predate the cycle annotation or the local cache.
    score_cycle = cycle
    cached = store.fetch_cycle(cycle["cycle_id"]) if store is not None else None
    scope_keys = ("device_id", "profile_id", "start_sequence", "end_sequence",
                  "status", "detection_version")
    if cached is None or all(cached.get(k) == cycle.get(k) for k in scope_keys):
        label = cached.get("custom_label") if cached is not None else None
        if (not label and isinstance(store, SupabaseCycleStore)
                and _report_source(cycle) == "sensor"):
            try:
                label = store.fetch_cycle_annotation(cycle["cycle_id"]).get("label")
            except ValueError:
                pass
        if label:
            score_cycle = {**cycle, "custom_label": label}
    assessment = render_cycle_assessment(score_cycle, store)
    rolling, insight = assessment if assessment else (None, None)
    st.subheader("Insights & next steps")
    if rolling is not None:
        with st.container(border=True):
            st.markdown("#### How this cycle compares with the current baseline")
            for message in rolling_power_insights(cycle, rolling):
                st.write(message)
    if report["engine"] == "codex" and report["status"] == "generated":
        st.info("DISCLAIMER · This saved report includes constrained AI-assisted interpretation. "
                "The readings and tariff evidence remain the basis for review.")
    elif report["engine"] == "codex":
        st.info("DISCLAIMER · The saved AI-assisted review was unavailable. "
                "The numerical evidence is shown with rule-based guidance.")
    else:
        st.info("DISCLAIMER · Rule-based observations from saved evidence. "
                "Appliance condition and physical causes require separate verification.")
    headings = {"observations": "What the readings show", "possibilities": "Possible explanations",
                "actions": "What to try next"}
    for section, messages in cycle_presentation_insights(cycle, insight).items():
        with st.container(border=True):
            st.markdown(f"#### {headings[section]}")
            for message in messages:
                st.write(message)
    if report["engine"] == "codex":
        st.subheader("AI-assisted review")
        st.caption(
            "Codex-selected insights; Python supplies every statement and value. "
            "Not a free-form diagnosis. Opening or refreshing this report makes no AI call."
        )
        sections = narrative_sections(report)
        if sections:
            for section, messages in sections.items():
                st.markdown("**" + {"observations": "Selected observations",
                                   "actions": "Suggested next steps"}[section] + "**")
                for message in messages:
                    st.write(message)
        else:
            st.warning(FAILURE_MESSAGES[evidence["codex_review"]["failure"]])
    render_tariff_disclosure(estimate, "Cycle energy")
    if len(cycles) != 1 or insight is None:
        return None
    if rolling is not None:
        mean = rolling["mean_power_w"]
        delta = rolling["power_change_w"]
        insight["rolling_comparison"] = (
            f"Current rolling mean power is **{mean:,.1f} W** across "
            f"**{rolling['cycle_count']} verified cycles**. This cycle averaged "
            f"**{mean + delta:,.1f} W**, a difference of **{delta:+,.1f} W "
            f"({100 * delta / mean:+.1f}%)**."
        )
    return insight


def cycle_presentation_insights(cycle: dict, insight: dict | None = None) -> dict[str, list[str]]:
    """Derive display guidance without changing canonical report text or hashes."""
    duration = cycle["duration_seconds"]
    energy = cycle["meter_energy_kwh"]
    if cycle["record_source"] == "manual":
        return {
            "observations": [f"The manual entry records {duration:g} s and {energy:.5f} kWh."],
            "possibilities": ["A manual record has no saved sensor trace to explain variation."],
            "actions": ["Keep the reference meter reading with the manual entry."],
        }
    valid = cycle.get("valid_sample_count", 0)
    invalid = cycle.get("invalid_sample_count", 0)
    missing = cycle.get("missing_sample_count", 0)
    ratio = cycle.get("energy_difference_ratio", 0)
    observations = [
        f"Recorded duration was {duration:g} s, with {energy:.5f} kWh on the cycle meter.",
        f"Recorded average power was {cycle['average_power_w']:,.0f} W "
        f"and peak power was {cycle['peak_power_w']:,.0f} W.",
    ]
    if insight:
        observations.append(insight["comparison"])
    if invalid or missing or cycle.get("gap_count"):
        observations.append(
            f"{valid} valid readings, {invalid} invalid, {missing} estimated missing."
        )
        possibilities = ["A gap in the recorded stream limits comparison across the interval."]
        actions = ["Review chart gaps and check connectivity during those intervals."]
    elif cycle["assessment"] == "unusual":
        observations.append(f"The meter and integrated energy estimates differ by {ratio:.1%}.")
        possibilities = ["A counter change or missed interval could affect the energy cross-check."]
        actions = ["Compare the saved meter counter with the power trace."]
    elif insight and insight["pattern"] != "normal":
        if insight["duration_led"]:
            possibilities = [
                "Duration had the larger relative difference. Water volume or starting "
                "temperature could explain part of the difference."
            ]
            actions = ["Compare heating times at the same water volume and starting temperature."]
        else:
            possibilities = [
                "Mean active power had the larger relative difference. Supply voltage "
                "or a difference in connected load could affect the comparison."
            ]
            actions = ["Compare voltage and active-power traces under similar conditions."]
        if insight["pattern"] == "warning":
            actions.append(
                "Review the trace and collection notes. A score alone cannot establish a fault."
            )
    elif insight:
        possibilities = [
            f"The score is below the {ANOMALY_ADVISORY_THRESHOLD:g} / 100 dashboard cutoff."
        ]
        actions = ["Check repeatability at the same water volume and starting temperature."]
    else:
        possibilities = ["Water volume and starting conditions can change duration and energy."]
        actions = ["Compare with a completed cycle under the same recorded operating conditions."]
    source = _report_source(cycle)
    if source != "sensor":
        observations.append(f"Source: {REPORT_SOURCES[source][0]}.")
    return {"observations": observations, "possibilities": possibilities, "actions": actions}


def _transparent_chart_layout(height: int) -> dict[str, object]:
    return {
        "height": height,
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(7,8,18,0.72)",
        "font": {"color": "#F7F8FF", "family": CHART_FONT},
        "margin": {"l": 20, "r": 20, "t": 58, "b": 20},
        "hovermode": "x unified",
    }


def render_resettable_chart(figure: go.Figure, *, chart_key: str) -> None:
    """Render a chart with an explicit control that restores its default viewport."""
    reset_count_key = f"{chart_key}_reset_count"
    if reset_count_key not in st.session_state:
        st.session_state[reset_count_key] = 0

    _, reset_control = st.columns([6, 1])
    with reset_control:
        if st.button(
            "Reset view",
            icon=":material/refresh:",
            help="Return this graph to its original zoom and scale",
            key=f"{chart_key}_reset_button",
            width="stretch",
        ):
            st.session_state[reset_count_key] += 1

    st.plotly_chart(
        figure,
        width="stretch",
        config={"displayModeBar": False},
        key=f"{chart_key}_{st.session_state[reset_count_key]}",
    )


def render_command_bar(connection: ConnectionState, updated_at: datetime | None) -> str:
    updated = (
        "Waiting for data"
        if updated_at is None
        else format_myt(updated_at)
    )
    return (
        f'<div class="command-bar" role="status" aria-live="polite">'
        f'<span class="connection connection-{connection.value}">'
        f"● {connection.value.title()}</span><span>Last reading: {updated}</span></div>"
    )


def _charge_value(estimate: TariffEstimate | None) -> str:
    if estimate is None:
        return "Unavailable"
    if estimate.amount_rm is not None:
        return f"RM {estimate.amount_rm:.4f}"
    if estimate.amount_range_rm is not None:
        low, high = estimate.amount_range_rm
        return f"RM {low:.3f}–{high:.3f}"
    return "Unavailable"


def live_status(view: LiveView) -> tuple[str, str]:
    if view.state == "empty":
        return "Connecting", "idle"
    if view.connection_state is not ConnectionState.ONLINE or view.state == "unconfirmed":
        return "Offline", "offline"
    if view.recent_readings[-1].quality_status.value != "valid":
        return "Connecting", "idle"
    return {
        "idle": ("Ready", "idle"),
        "monitoring": ("Monitoring", "idle"),
        "heating": ("Active Load Detected", "heating"),
        "finalizing": ("Finalizing", "finalizing"),
        "cycle_complete": ("Cycle Complete", "complete"),
    }[view.state]


def _charge_caption(estimate: TariffEstimate | None) -> None:
    if estimate is None:
        return
    rate = estimate.gross_variable_rate_sen_per_kwh
    detail = f" · {rate} sen/kWh" if rate is not None else ""
    qualifier = " · qualified estimate" if estimate.status.value == "partial" else ""
    st.caption(f"{estimate.provider} {estimate.scheme}{detail}{qualifier}")
    render_tariff_disclosure(estimate, "Cycle energy")


def render_live_state(view: LiveView, store: SQLiteTelemetryStore) -> None:
    title, status = live_status(view)
    st.markdown(
        f'<section class="state-panel state-{status}" role="status" aria-live="polite">'
        f'<h2>{title}</h2></section>',
        unsafe_allow_html=True,
    )
    if view.state == "empty":
        st.info("Waiting for readings from the selected source.")
        return
    latest = view.recent_readings[-1]
    received = (f"Received at {format_myt(view.received_at)}"
                if view.received_at is not None else "Received time unavailable")
    st.caption(f"Measured at {format_myt(latest.timestamp)} · {received} · "
               f"Sample age {view.sample_age_seconds} s")
    origin = latest.data_origin
    if origin != "sensor":
        st.caption(f"Source: {REPORT_SOURCES[origin][0]}")
    with st.expander("Technical details"):
        st.text(f"Monitor: {latest.device_id} · Profile: {latest.profile_id}")
        st.caption(f"Selected cycle rules: {view.appliance_rule_name} · "
                   f"Start {view.start_threshold_w:g} W")
        st.text(f"Firmware: {latest.firmware_version} · Source: {REPORT_SOURCES[origin][0]}")
        st.caption("Sensor mode does not establish calibration. "
                   "Monitor IDs can be reused across sensor and simulation runs.")
        st.caption(view.quality_note)
    fresh = view.connection_state is ConnectionState.ONLINE
    power = view.latest_power_w or 0
    power_format = ",.1f" if abs(power) < 100 else ",.0f"
    primary = st.columns(3)
    primary[0].metric("Active power now" if fresh else "Last recorded power",
        f"{power:{power_format}} W")
    primary[1].metric("Voltage now" if fresh else "Last recorded voltage",
        f"{view.latest_voltage_v or 0:.1f} V")
    primary[2].metric("Current now" if fresh else "Last recorded current",
        f"{view.latest_current_a or 0:.3f} A")

    render_resettable_chart(
        build_live_power_figure(view, start_threshold_w=view.start_threshold_w),
        chart_key="live_power_chart",
    )
    with st.container(key="live-electrical-pair"):
        voltage, current = st.columns(2)
        for column, metric in ((voltage, "Voltage"), (current, "Current")):
            with column:
                render_resettable_chart(
                    build_electrical_figure(view.recent_readings, metric, elapsed=False),
                    chart_key=f"live_{metric.lower()}",
                )

    if view.active_cycle is not None:
        active = view.active_cycle
        st.subheader("Current cycle")
        _live_cycle_tiles(
            f"{active.duration_seconds} s", "Cycle energy so far",
            active.meter_energy_kwh, _charge_value(view.charge_so_far), view.charge_so_far,
        )
    if view.last_cycle is not None:
        last = view.last_cycle
        st.subheader("Last completed cycle")
        if (last.source_label == "Sensor mode"
                and (last.cycle_label or "").casefold() not in {"complete", "incomplete"}):
            st.info("Cycle finished. Open Cycles and label it Complete or Incomplete.")
        _live_cycle_tiles(
            f"{last.duration_seconds} s", last.energy_label, last.energy_kwh,
            last.charge_text, last.tariff_estimate,
        )
    energy_figure = build_cycle_energy_figure(view)
    if energy_figure is not None:
        add_cycle_comparison(energy_figure, view, store)
        render_resettable_chart(energy_figure, chart_key="live_cycle_energy_chart")

    if view.recent_readings:
        latest = view.recent_readings[-1]
        st.subheader("Power quality")
        quality = st.columns(2)
        quality[0].metric("Frequency now" if fresh else "Last recorded frequency",
            f"{latest.frequency_hz:.2f} Hz")
        quality[1].metric("Power factor now" if fresh else "Last recorded power factor",
            f"{latest.power_factor:.3f}")
        render_resettable_chart(
            build_power_quality_figure(view),
            chart_key="live_power_quality_chart",
        )
def _live_cycle_tiles(
    duration: str, energy_label: str, energy: float, charge: str, estimate: TariffEstimate | None,
) -> None:
    with st.container(key=f"cycle-tiles-{energy_label.lower().replace(' ', '-')}"):
        duration_col, energy_col = st.columns(2)
        duration_col.metric("Cycle duration", duration)
        energy_col.metric(energy_label, f"{energy:.5f} kWh")
        st.metric("Estimated gross cycle charge", charge)
        _charge_caption(estimate)


def build_electrical_figure(
    readings: list[TelemetryReading], metric: str, *, elapsed: bool,
) -> go.Figure:
    field, unit, color, digits = {
        "Voltage": ("voltage_v", "V", "#FFC46B", 1),
        "Current": ("current_a", "A", "#72F6C7", 3),
    }[metric]
    times = [r.timestamp for r in readings]
    x = ([(t - times[0]).total_seconds() for t in times] if elapsed
         else [malaysia_time(t) for t in times])
    y = [getattr(r, field) if r.quality_status.value == "valid" else None for r in readings]
    # Insert gaps between separated samples so the chart never implies a measurement in between.
    gap_counts = sampling_gap_counts(readings)
    for index in range(len(times) - 1, 0, -1):
        if gap_counts[index]:
            x.insert(index, x[index])
            y.insert(index, None)
    time_hover = "%{x:g} s" if elapsed else "%{x|%H:%M:%S} MYT"
    figure = go.Figure(go.Scatter(
        x=x, y=y, name=metric, mode="lines+markers", connectgaps=False,
        marker={"size": 4}, line={"color": color, "width": 2},
        hovertemplate=f"{time_hover}<br>%{{y:.{digits}f}} {unit}<extra></extra>",
    ))
    figure.update_layout(
        **_transparent_chart_layout(height=285),
        title={"text": metric if elapsed else f"Live {metric}", "x": 0.02},
        xaxis_title="Elapsed (s)" if elapsed else "Recorded time (MYT)",
        yaxis_title=f"{metric} ({unit})", showlegend=False,
    )
    return figure


def add_cycle_comparison(
    figure: go.Figure, view: LiveView, store: SQLiteTelemetryStore,
) -> None:
    if view.active_cycle is not None or view.last_cycle is None:
        return
    latest = view.recent_readings[-1]
    candidates = {}
    for row in store.fetch_report_cycles(latest.profile_id, latest.device_id, limit=20):
        if (row["cycle_id"] == str(view.last_cycle.cycle_id)
                or row.get("record_source") != "measured"
                or row.get("data_origin") != latest.data_origin):
            continue
        label = format_myt(str(row["started_at"]))
        for description in dict.fromkeys((row.get("effective_volume"), row.get("custom_label"))):
            if description and description != "unknown":
                label += f" · {description}"
        candidates[row["cycle_id"]] = label
    if not candidates:
        return
    selected = st.multiselect(
        "Compare completed cycles", list(candidates), format_func=candidates.__getitem__,
        max_selections=3,
        key=f"energy-compare-{latest.device_id}-{latest.profile_id}-{latest.data_origin}",
    )
    if not selected:
        return
    figure.update_traces(name=f"Latest · {format_myt(view.last_cycle.started_at)}")
    for cycle_id, color, dash in zip(
        selected, ("#3BD7FF", "#FFC46B", "#E99AFF"), ("dash", "dot", "dashdot"),
        strict=False,
    ):
        detail = get_cycle_detail(store, cycle_id, None)
        if not detail.readings:
            continue
        elapsed, values = cycle_energy_points(detail.readings, detail.summary.meter_energy_kwh)
        figure.add_scatter(
            x=elapsed, y=values, mode="lines+markers", name=candidates[cycle_id],
            line={"color": color, "dash": dash}, connectgaps=False,
            hovertemplate="%{x:g} s<br>%{y:.5f} kWh<extra>%{fullData.name}</extra>",
        )
    figure.update_layout(showlegend=True, legend={"orientation": "h", "y": -0.25})


def build_live_power_figure(view: LiveView, start_threshold_w: float) -> go.Figure:
    readings = view.recent_readings
    timestamps = [malaysia_time(reading.timestamp) for reading in readings]
    power = [reading.active_power_w for reading in readings]
    peak_power = max(power, default=0.0)
    custom_hover = [
        [reading.voltage_v, reading.current_a, reading.quality_status.value]
        for reading in readings
    ]
    gap_counts = sampling_gap_counts(readings)
    for index in range(len(readings) - 1, 0, -1):
        if gap_counts[index]:
            timestamps.insert(index, timestamps[index])
            power.insert(index, None)
            custom_hover.insert(index, [None, None, "sampling gap"])
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=timestamps,
            y=power,
            mode="lines",
            name="Active power",
            connectgaps=False,
            line={"color": "#3BD7FF", "width": 3},
            fill="tozeroy",
            fillcolor="rgba(59, 215, 255, 0.10)",
            customdata=custom_hover,
            hovertemplate=(
                "%{x|%H:%M:%S}<br>%{y:,.1f} W"
                "<br>Voltage %{customdata[0]:.1f} V"
                "<br>Current %{customdata[1]:.3f} A"
                "<br>Quality %{customdata[2]}<extra></extra>"
            ),
        )
    )
    if readings:
        latest = readings[-1]
        figure.add_trace(
            go.Scatter(
                x=[timestamps[-1]],
                y=[latest.active_power_w],
                mode="markers",
                name="Current reading",
                marker={
                    "color": "#72F6C7",
                    "line": {"color": "#070812", "width": 2},
                    "size": 11,
                },
                hovertemplate="Current %{y:,.1f} W<extra></extra>",
            )
        )
        if peak_power >= start_threshold_w:
            figure.add_hline(
                y=start_threshold_w, name="Start threshold", showlegend=True,
                line={"color": "#E22DFF", "width": 2, "dash": "dash"},
            )
        cycle = view.active_cycle or view.last_cycle
        if cycle is not None:
            cycle_end = cycle.ended_at or readings[-1].timestamp
            chart_start = readings[0].timestamp
            chart_end = readings[-1].timestamp
            if cycle_end >= chart_start and cycle.started_at <= chart_end:
                figure.add_vrect(
                    x0=malaysia_time(max(cycle.started_at, chart_start)),
                    x1=malaysia_time(min(cycle_end, chart_end)),
                    fillcolor="rgba(255, 116, 72, 0.10)",
                    name="Detected cycle", showlegend=True,
                    line_width=0,
                    layer="below",
                )
    invalid = [
        (malaysia_time(reading.timestamp), reading.active_power_w)
        for reading in readings
        if reading.quality_status.value != "valid"
    ]
    if invalid:
        figure.add_trace(
            go.Scatter(
                x=[item[0] for item in invalid],
                y=[item[1] for item in invalid],
                mode="markers",
                name="Invalid/missing sample",
                marker={"color": "#FF5F6D", "size": 10, "symbol": "diamond"},
                hovertemplate="Invalid reading at %{x|%H:%M:%S}<extra></extra>",
            )
        )
    figure.update_layout(
        **_transparent_chart_layout(height=330),
        title={"text": "Live power · last 5 min", "x": 0.02},
        xaxis={
            "title": "Malaysia time (MYT)",
            "gridcolor": "rgba(184,196,255,0.10)",
            "tickformat": "%H:%M",
            "automargin": True,
        },
        yaxis={
            "title": "Power (W)",
            "gridcolor": "rgba(184,196,255,0.12)",
            "range": [0, max(5.0, peak_power * 1.1)],
            "automargin": True,
        },
        showlegend=True,
        legend={"orientation": "h", "y": -0.35},
    )
    return figure


def build_power_quality_figure(view: LiveView) -> go.Figure:
    readings = view.recent_readings
    timestamps = [malaysia_time(reading.timestamp) for reading in readings]
    frequency = [reading.frequency_hz for reading in readings]
    factors = [reading.power_factor for reading in readings]
    gap_counts = sampling_gap_counts(readings)
    for index in range(len(readings) - 1, 0, -1):
        if gap_counts[index]:
            timestamps.insert(index, timestamps[index])
            frequency.insert(index, None)
            factors.insert(index, None)
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.14,
    )
    figure.add_trace(
        go.Scatter(
            x=timestamps,
            y=frequency, connectgaps=False,
            mode="lines",
            name="Frequency",
            line={"color": "#E22DFF", "width": 2.5},
            hovertemplate="%{x|%H:%M:%S}<br>%{y:.2f} Hz<extra></extra>",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=timestamps,
            y=factors, connectgaps=False,
            mode="lines",
            name="Power factor",
            line={"color": "#72F6C7", "width": 2.5},
            hovertemplate="%{x|%H:%M:%S}<br>%{y:.3f}<extra></extra>",
        ),
        row=2,
        col=1,
    )
    figure.add_hline(
        y=50,
        line={"color": "rgba(247,248,255,0.45)", "width": 1, "dash": "dash"},
        row=1,
        col=1,
    )
    figure.update_layout(
        **_transparent_chart_layout(height=390),
        title={"text": "Power quality · last 5 min", "x": 0.02},
        showlegend=False,
    )
    figure.update_xaxes(
        title_text="Malaysia time (MYT)",
        gridcolor="rgba(184,196,255,0.10)",
        tickformat="%H:%M",
        automargin=True,
        row=2,
        col=1,
    )
    figure.update_yaxes(
        title_text="Frequency (Hz)",
        gridcolor="rgba(184,196,255,0.12)",
        automargin=True,
        row=1,
        col=1,
    )
    figure.update_yaxes(
        title_text="Power factor",
        range=[0, 1.05],
        gridcolor="rgba(184,196,255,0.12)",
        automargin=True,
        row=2,
        col=1,
    )
    return figure


def cycle_energy_points(
    readings: list[TelemetryReading], measured_total: float,
) -> tuple[list[float], list[float | None]]:
    started_at = readings[0].timestamp
    elapsed = [(reading.timestamp - started_at).total_seconds() for reading in readings]
    baseline = readings[0].cumulative_energy_kwh
    observed_span = max(0.0, readings[-1].cumulative_energy_kwh - baseline)
    first_increment = max(0.0, measured_total - observed_span)
    energy = [
        first_increment + max(0.0, reading.cumulative_energy_kwh - baseline)
        if reading.quality_status.value == "valid" else None
        for reading in readings
    ]
    gap_counts = sampling_gap_counts(readings)
    for index in range(len(readings) - 1, 0, -1):
        if gap_counts[index]:
            elapsed.insert(index, elapsed[index])
            energy.insert(index, None)
    return elapsed, energy


def build_cycle_energy_figure(view: LiveView) -> go.Figure | None:
    readings = view.cycle_readings
    if not readings:
        return None
    measured_total = (
        view.active_cycle.meter_energy_kwh if view.active_cycle is not None
        else view.last_cycle.energy_kwh if view.last_cycle is not None else 0.0
    )
    elapsed, energy = cycle_energy_points(readings, measured_total)
    state = (
        "Last known cycle · awaiting fresh readings" if view.state == "unconfirmed"
        and view.active_cycle is not None else "In progress" if view.active_cycle is not None
        else "Completed cycle"
    )
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=elapsed,
            y=energy,
            mode="lines+markers",
            name="Cycle energy",
            line={"color": "#72F6C7", "width": 3},
            marker={"color": "#72F6C7", "size": 4},
            fill="tozeroy",
            fillcolor="rgba(114, 246, 199, 0.09)",
            hovertemplate="%{x:.0f} s<br>%{y:.5f} kWh<extra></extra>",
        )
    )
    figure.update_layout(
        **_transparent_chart_layout(height=270),
        title={"text": f"Cycle energy · {state.lower()}", "x": 0.02},
        xaxis={
            "title": "Elapsed (s)",
            "gridcolor": "rgba(184,196,255,0.10)",
            "automargin": True,
        },
        yaxis={
            "title": "Energy (kWh)",
            "gridcolor": "rgba(184,196,255,0.12)",
            "rangemode": "tozero",
            "automargin": True,
        },
        showlegend=False,
    )
    return figure


def reset_cycle_filters() -> None:
    st.session_state.update(
        status_filter="All", label_filter="All labels", cycle_date_filter="All dates",
        cycle_use_time=False, data_source_filter="All records",
        cycle_time_start=time(13), cycle_time_end=time(15),
    )


def render_cycle_filters(store: SQLiteTelemetryStore, now: datetime) -> tuple[str, str, dict]:
    with st.container(key="cycle-time-filters"):
        dates, times = st.columns(2)
        period = dates.selectbox("Recording date (MYT)", [
            "All dates", "Today", "Yesterday", "Last 7 days", "Custom date range",
        ], key="cycle_date_filter")
        use_time = times.checkbox("Limit start time", key="cycle_use_time")
        options = {"date_filter": period, "now": now}
        if period == "Custom date range":
            start, end = st.columns(2)
            options["date_start"] = start.date_input(
                "Start date (MYT)", value=malaysia_time(now).date(),
                key="cycle_date_start", format="DD/MM/YYYY")
            options["date_end"] = end.date_input(
                "End date (MYT)", value=malaysia_time(now).date(),
                key="cycle_date_end", format="DD/MM/YYYY")
        if use_time:
            start, end = st.columns(2)
            options["time_start"] = start.time_input(
                "From (MYT, inclusive)", value=time(13), step=60, key="cycle_time_start")
            options["time_end"] = end.time_input(
                "Until (MYT, exclusive)", value=time(15), step=60, key="cycle_time_end")
            st.caption("Start included · end excluded · same-day windows only.")
    status = st.segmented_control(
        "Cycle status",
        ["All", "Normal", "Unusual", "Incomplete"],
        key="status_filter",
    )
    label_options = ["All labels", "Unlabelled", *store.list_cycle_labels()]
    label = st.selectbox(
        "Cycle label",
        label_options,
        key="label_filter",
    )
    st.button("Reset filters", key="reset_cycle_filters", on_click=reset_cycle_filters)
    return str(status or "All"), str(label or "All labels"), options


def render_manual_cycle_entry(store: SQLiteTelemetryStore) -> UUID | None:
    with st.expander("Add manual cycle record"):
        st.caption(
            "Use for a known past cycle without sensor telemetry. Manual records stay "
            "separate from automatic anomaly analysis."
        )
        with st.form("manual_cycle_entry", clear_on_submit=True):
            date_column, time_column = st.columns(2)
            cycle_date = date_column.date_input(
                "Cycle date",
                format="DD/MM/YYYY",
                key="manual_cycle_date",
            )
            cycle_time = time_column.time_input(
                "Start time (Malaysia / MYT)",
                step=60,
                key="manual_cycle_time",
            )
            duration_column, energy_column = st.columns(2)
            duration_seconds = duration_column.number_input(
                "Duration (seconds)",
                min_value=1,
                max_value=86_400,
                value=180,
                step=5,
            )
            energy_kwh = energy_column.number_input(
                "Energy (kWh)",
                min_value=0.00001,
                max_value=100.0,
                value=0.08500,
                step=0.001,
                format="%.5f",
            )
            label = st.selectbox(
                "Record label",
                store.list_cycle_labels(),
                index=None,
                placeholder="Choose or type a label",
                accept_new_options=True,
            )
            notes = st.text_area(
                "Notes (optional)",
                max_chars=500,
                placeholder="For example: read from reference meter",
            )
            submitted = st.form_submit_button("Add manual record", type="primary")

        if not submitted:
            return None
        if cycle_time is None or label is None or not str(label).strip():
            st.error("Start time and record label are required")
            return None
        try:
            started_at = datetime.combine(cycle_date, cycle_time, tzinfo=MALAYSIA_TZ)
            cycle_id = store.add_manual_cycle(
                ManualCycleRecord(
                    started_at=started_at,
                    duration_seconds=int(duration_seconds),
                    energy_kwh=float(energy_kwh),
                    label=str(label),
                    notes=notes,
                )
            )
            st.success("Manual cycle record added")
            return cycle_id
        except ValueError as exc:
            st.error(str(exc))
            return None


def render_cycle_history(items: list[CycleListItem]) -> UUID | None:
    st.caption(f"{len(items):,} matching cycle(s) · newest first")
    if not items:
        st.info("No cycles match these filters.")
        return None
    st.caption("Select anywhere within a row to inspect its full receipt. Times use Malaysia time.")
    frame = pd.DataFrame(
        [
            {
                "Cycle": f"Cycle {item.cycle_number}" if item.cycle_number is not None else "",
                "Start (MYT)": malaysia_time(item.started_at).strftime(
                    "%d %b, %H:%M"
                ),
                "Duration": (
                    f"{item.duration_seconds // 60}m {item.duration_seconds % 60:02d}s"
                ),
                "Energy": f"{item.energy_kwh:.4f} kWh",
                "Charge": item.charge_text,
                "Label": item.cycle_label or "Unlabelled",
                "Source": item.source_label,
                "Assessment": item.assessment.value.replace("_", " ").title(),
                "Status": item.status.value.title(),
            }
            for item in items
        ]
    )
    st.caption("Table assessments use cycle rules. Experimental model results appear separately.")
    event = st.dataframe(
        frame,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-cell",
        key="cycle_history-" + hashlib.sha256(
            ",".join(str(item.cycle_id) for item in items).encode()).hexdigest()[:16],
    )
    cells = event.selection.cells
    return items[cells[0][0]].cycle_id if cells and cells[0][0] < len(items) else None


def _operating_duration(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes}m {seconds}s"


def _cycle_charge_totals(estimates: list[TariffEstimate]) -> list[tuple[str, str]]:
    groups = defaultdict(list)
    for estimate in estimates:
        key = (
            estimate.provider, estimate.scheme, estimate.tariff_version,
            estimate.monthly_household_kwh, estimate.gross_variable_rate_sen_per_kwh,
            estimate.afa_version if estimate.afa_rate_sen_per_kwh else None,
            estimate.included_components, estimate.excluded_components,
            estimate.unresolved_components, estimate.amount_range_rm is not None,
        )
        groups[key].append(estimate)
    totals = []
    for group in groups.values():
        first = group[0]
        if first.amount_range_rm is not None:
            low = sum(estimate.amount_range_rm[0] for estimate in group)
            high = sum(estimate.amount_range_rm[1] for estimate in group)
            amount = f"RM {low:.3f}–{high:.3f}"
            basis = "base tariff range · household usage unresolved"
        else:
            amount = f"RM {sum(estimate.amount_rm for estimate in group):.4f}"
            basis = f"{first.gross_variable_rate_sen_per_kwh} sen/kWh"
            if first.monthly_household_kwh is not None:
                basis += f" · {first.monthly_household_kwh} kWh/month household usage"
            if first.afa_rate_sen_per_kwh:
                basis += f" · {first.afa_period} AFA"
        totals.append((f"{first.provider} {first.scheme} · {basis} · {len(group)} cycles", amount))
    return totals


def render_cycle_totals(items: list[CycleListItem]) -> None:
    unique = {item.cycle_id: item for item in items}
    by_source = defaultdict(list)
    for item in unique.values():
        if (item.status is CycleStatus.COMPLETED
                and (item.cycle_label or "").strip().casefold() != "incomplete"):
            by_source[item.source_label].append(item)
    with st.container(border=True, key="cycle-totals"):
        st.subheader("Filtered cycle totals")
        excluded = len(unique) - sum(map(len, by_source.values()))
        if excluded:
            noun = "cycle" if excluded == 1 else "cycles"
            st.caption(f"{excluded:,} incomplete {noun} excluded.")
        if not by_source:
            st.caption("No completed cycles to total for these filters.")
        for source, cycles in by_source.items():
            st.markdown(f"**{source}**")
            energy_cycles = [item for item in cycles if item.energy_eligible]
            energy = sum(Decimal(str(item.energy_kwh)) for item in energy_cycles)
            duration = sum(item.duration_seconds for item in cycles)
            costs = [
                estimate for item in energy_cycles
                if (estimate := item.tariff_estimate) is not None
                and (estimate.amount_rm is not None or estimate.amount_range_rm is not None)
            ]
            totals = _cycle_charge_totals(costs)
            cost_text = "Unavailable" if not totals else totals[0][1]
            if len(totals) > 1:
                cost_text = "See breakdown"
            counts = st.columns(3)
            counts[0].metric("Completed cycles", f"{len(cycles):,}")
            counts[1].metric(
                "Total energy", f"{energy:.4f} kWh" if energy_cycles else "Unavailable",
            )
            counts[2].metric("Operating duration", _operating_duration(duration))
            averages = st.columns(3)
            averages[0].metric("Estimated variable charge", cost_text)
            averages[1].metric("Average energy",
                               f"{energy / len(energy_cycles):.4f} kWh"
                               if energy_cycles else "Unavailable")
            averages[2].metric("Average duration",
                               _operating_duration(round(duration / len(cycles))))
            if len(totals) > 1:
                for basis, amount in totals:
                    st.caption(f"{basis} · {amount}")
            st.caption(
                f"{len(cycles) - len(energy_cycles)} energy unavailable · "
                f"{len(cycles) - len(costs)} cost unavailable · "
                f"{sum(estimate.status.value == 'partial' for estimate in costs)} qualified "
                "estimate(s). Charges cover available cycles only and exclude household bill items."
            )


def render_cycle_receipt(detail: CycleDetail) -> None:
    summary = detail.summary
    status_class = {
        "normal": "status-normal",
        "unusual": "status-unusual",
        "insufficient_data": "status-warning",
        "not_evaluated": "status-warning",
    }[summary.assessment.value]
    st.markdown(
        f'<section class="cycle-receipt {status_class}">'
        f'<p class="eyebrow">SELECTED CYCLE · MALAYSIA TIME</p>'
        f"<h2>{format_myt(summary.started_at, '%d %b %Y, %H:%M')}</h2>"
        f"<p>{summary.status.value.title()} · "
        f"{summary.assessment.value.replace('_', ' ').title()}</p></section>",
        unsafe_allow_html=True,
    )
    metrics = st.columns(3)
    metrics[0].metric("Cycle duration", f"{summary.duration_seconds} s")
    metrics[1].metric(detail.energy_label, f"{summary.meter_energy_kwh:.5f} kWh")
    if summary.data_origin != "sensor" and detail.record_source != "manual":
        st.caption(REPORT_SOURCES[summary.data_origin][0])
    metrics[2].metric("Estimated gross cycle charge", _charge_value(detail.tariff_estimate))
    secondary = st.columns(3)
    if detail.record_source == "manual":
        secondary[0].metric("Record source", "Manual entry")
        secondary[1].metric("Assessment", "Not evaluated")
    else:
        secondary[0].metric("Average power", f"{summary.average_power_w:,.0f} W")
        secondary[1].metric("Peak power", f"{summary.peak_power_w:,.0f} W")
    secondary[2].metric("Cycle label", detail.cycle_label or "Unlabelled")
    if summary.status.value != "completed" or summary.assessment.value != "normal":
        st.caption(summary.quality_note)
    if detail.manual_notes:
        st.caption(f"Manual notes: {detail.manual_notes}")


def build_cycle_figure(detail: CycleDetail, start_threshold_w: float) -> go.Figure:
    elapsed = [
        (reading.timestamp - detail.summary.started_at).total_seconds()
        for reading in detail.readings
    ]
    power = [reading.active_power_w for reading in detail.readings]
    energy_so_far = [0.0]
    for previous, current in zip(detail.readings, detail.readings[1:], strict=False):
        energy_so_far.append(
            energy_so_far[-1]
            + max(0.0, current.cumulative_energy_kwh - previous.cumulative_energy_kwh)
        )
    custom_hover = [
        [reading.voltage_v, reading.current_a, energy]
        for reading, energy in zip(detail.readings, energy_so_far, strict=True)
    ]
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=elapsed,
            y=power,
            mode="lines",
            name="Selected cycle",
            line={"color": "#3BD7FF", "width": 3},
            fill="tozeroy",
            fillcolor="rgba(226, 45, 255, 0.12)",
            customdata=custom_hover,
            hovertemplate=(
                "%{x:.0f} s<br>%{y:,.0f} W<br>Voltage %{customdata[0]:.1f} V"
                "<br>Current %{customdata[1]:.3f} A"
                "<br>Cycle energy %{customdata[2]:.5f} kWh<extra></extra>"
            ),
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[min(elapsed, default=0), max(elapsed, default=0)],
            y=[start_threshold_w, start_threshold_w],
            mode="lines",
            name="Detection threshold",
            line={"color": "#E22DFF", "width": 2, "dash": "dash"},
            hovertemplate="Start threshold %{y:,.0f} W<extra></extra>",
        )
    )
    if elapsed:
        figure.add_trace(
            go.Scatter(
                x=[elapsed[0], elapsed[-1]],
                y=[power[0], power[-1]],
                mode="markers",
                name="Cycle boundaries",
                marker={"color": "#72F6C7", "size": 11, "symbol": "circle-open"},
                hovertemplate="Boundary at %{x:.0f} s<extra></extra>",
            )
        )
    invalid = [
        (second, watts)
        for second, watts, reading in zip(elapsed, power, detail.readings, strict=True)
        if reading.quality_status.value != "valid"
    ]
    figure.add_trace(
        go.Scatter(
            x=[item[0] for item in invalid],
            y=[item[1] for item in invalid],
            mode="markers",
            name="Invalid/missing sample",
            marker={"color": "#FF5F6D", "size": 11, "symbol": "diamond"},
            hovertemplate="Invalid reading at %{x:.0f} s<extra></extra>",
        )
    )
    figure.update_layout(
        xaxis_title="Elapsed (s)",
        yaxis_title="Power (W)",
        hovermode="x unified",
        height=380,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(7,8,18,0.72)",
        font={
            "color": "#F7F8FF",
            "family": "Cascadia Code, SFMono-Regular, Consolas, Liberation Mono, monospace",
        },
        margin={"l": 20, "r": 20, "t": 30, "b": 20},
        xaxis={"gridcolor": "rgba(184,196,255,0.12)", "automargin": True},
        yaxis={
            "gridcolor": "rgba(184,196,255,0.12)",
            "rangemode": "tozero",
            "automargin": True,
        },
        showlegend=True,
        legend={"orientation": "h", "y": -0.24},
    )
    return figure


def _calculation_value(value: Decimal, *, minimum_places: int = 0) -> str:
    whole, fraction = f"{value:.9f}".split(".")
    fraction = fraction.rstrip("0").ljust(minimum_places, "0")
    return f"{whole}.{fraction}" if fraction else whole


def tariff_disclosure_text(
    estimate: TariffEstimate,
    energy_label: str = "Measured cycle energy",
) -> str:
    afa_name = "AFA unavailable"
    if estimate.afa_period:
        try:
            month = datetime.strptime(estimate.afa_period, "%Y-%m").strftime("%B %Y")
        except ValueError:
            month = estimate.afa_period
        afa_name = f"{month} AFA"
        if estimate.afa_rate_sen_per_kwh is not None:
            afa_name += f" applied at {estimate.afa_rate_sen_per_kwh:+.2f} sen/kWh"
    calculations = []
    cases = tariff_calculation_rates(estimate, load_tariff_catalog(CATALOG_PATH))
    energy = estimate.energy_kwh
    energy_text = _calculation_value(energy, minimum_places=4)
    for tier, rates in cases:
        total = sum(rates.values())
        calculation = [f"**{tier}**", ""]
        calculation.extend(
            f"- {name}: {energy_text} kWh × {rate} sen/kWh ÷ 100 "
            f"= RM {_calculation_value(energy * rate / 100)}"
            for name, rate in rates.items()
        )
        calculation += [
            "",
            f"Combined variable rate: {' + '.join(str(rate) for rate in rates.values())} "
            f"= {total} sen/kWh.",
            "",
            f"**{energy_text} kWh × {total} sen/kWh ÷ 100 "
            f"= RM {_calculation_value(energy * total / 100)}**",
        ]
        calculations.append("\n".join(calculation))
    if estimate.amount_range_rm is not None:
        calculations.insert(
            0, "Household usage is unknown, so both base tiers are shown. "
            "AFA is unresolved and excluded from this range.",
        )
    if not cases:
        calculations.append("A component calculation could not be verified for this estimate.")
    unresolved = ", ".join(item.replace("_", "-") for item in estimate.unresolved_components)
    component_names = {
        "energy_efficiency_incentive": "energy-efficiency incentive",
        "retail_charge": "retail charge",
        "renewable_energy_fund": "renewable-energy fund",
        "complete_bill_rounding": "complete-bill rounding",
    }
    exclusions = ", ".join(
        component_names.get(item, item.replace("_", " "))
        for item in estimate.excluded_components
    )
    sources = "\n".join(f"- Source: {url}" for url in estimate.source_urls)
    return (
        f"**{estimate.provider} {estimate.scheme}**  \n"
        f"Tariff version: {estimate.tariff_version or 'unavailable'} · effective "
        f"{estimate.tariff_effective_from or 'unknown'} to "
        f"{estimate.tariff_effective_to or 'unknown'}.  \n"
        f"{afa_name}. {energy_label}: {energy_text} kWh.\n\n"
        + "\n\n".join(calculations) + "\n\n"
        f"**Displayed variable cycle charge: {_charge_value(estimate)}**  \n"
        f"Unresolved: {unresolved or 'none'}.  \nExcluded: {exclusions or 'none'}.  \n"
        f"Last checked: {estimate.last_checked_date or 'unavailable'}.  \n"
        f"{sources}  \n**This is not a complete household bill.**"
    )


def render_tariff_disclosure(
    estimate: TariffEstimate,
    energy_label: str = "Measured cycle energy",
) -> None:
    with st.expander("How this estimate was calculated"):
        st.markdown(tariff_disclosure_text(estimate, energy_label))


def render_cycle_annotation(store: SQLiteTelemetryStore, cycle_id: UUID) -> None:
    with st.expander("Cycle annotation"):
        try:
            annotation = store.fetch_cycle_annotation(cycle_id)
        except ValueError:
            st.info("Annotations are currently unavailable.")
            return
        labels = list(dict.fromkeys(["Complete", "Incomplete", *store.list_cycle_labels()]))
        if str(annotation.get("label") or "").casefold() not in {"complete", "incomplete"}:
            st.info("Was the run completed? Choose Complete or Incomplete and save.")
        with st.form(f"annotation-{cycle_id}-{annotation.get('id')}"):
            label = st.text_input(
                "Label", value=str(annotation.get("label") or ""), max_chars=40,
            )
            if labels:
                existing = st.selectbox("Or reuse a label", ["", *labels])
            else:
                existing = ""
            note = st.text_area("Optional note", value=str(annotation.get("note") or ""),
                                max_chars=500)
            st.caption("Shared annotation · separate from cycle assessment and training review")
            if st.form_submit_button("Save annotation", type="primary"):
                try:
                    store.save_cycle_annotation(
                        cycle_id, label=existing or label, note=note,
                        expected_id=annotation.get("id"),
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("Annotation saved.")
                    if (existing or label).strip().casefold() == "complete" and hasattr(
                        store, "append_first_assessment"
                    ):
                        from energy_monitor.experimental_if import preserve_first_assessment

                        cycle = store.fetch_cycle(cycle_id)
                        if cycle is not None:
                            try:
                                if preserve_first_assessment(cycle, store):
                                    st.info("Experimental score saved for this cycle.")
                            except ValueError:
                                st.info("Experimental score pending trace or cloud verification.")
