from __future__ import annotations

from datetime import datetime
from statistics import fmean
from uuid import UUID
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from energy_monitor.dashboard_queries import CycleDetail, CycleListItem, LiveView
from energy_monitor.models import ConnectionState, ManualCycleRecord, TariffEstimate
from energy_monitor.report_narrative import FAILURE_MESSAGES, narrative_sections
from energy_monitor.reporting import report_insights
from energy_monitor.storage import SQLiteTelemetryStore

MALAYSIA_TZ = ZoneInfo("Asia/Kuala_Lumpur")
CHART_FONT = "Cascadia Code, SFMono-Regular, Consolas, Liberation Mono, monospace"
REPORT_SOURCES = {
    "simulated": ("Demonstration data", "#E22DFF"),
    "sensor": ("Sensor-recorded data", "#3BD7FF"),
    "manual": ("Manually entered data", "#FFC46B"),
    "mixed": ("Mixed-origin data", "#72F6C7"),
    "unknown": ("Unverified origin", "#A8B4D0"),
}


def _report_source(cycle: dict) -> str:
    return "manual" if cycle["record_source"] == "manual" else cycle["data_origin"]


def build_report_traces(cycle: dict) -> list[go.Figure]:
    """Plot saved evidence only; gaps and invalid samples never become zeroes."""
    readings = cycle.get("readings", [])
    valid = [r for r in readings if r["quality_status"] == "valid"]
    if not valid:
        return []
    start = datetime.fromisoformat(cycle["started_at"])
    baseline = valid[0]["cumulative_energy_kwh"]
    span = max(0, valid[-1]["cumulative_energy_kwh"] - baseline)
    first_increment = max(0, cycle["meter_energy_kwh"] - span)
    counter_reset = any(
        b["cumulative_energy_kwh"] < a["cumulative_energy_kwh"]
        for a, b in zip(valid, valid[1:], strict=False)
    )
    elapsed, power, current, energy = [], [], [], []
    previous_time = None
    for reading in readings:
        timestamp = datetime.fromisoformat(reading["timestamp"])
        if previous_time is not None and (timestamp - previous_time).total_seconds() > 15:
            for values in (elapsed, power, current, energy):
                values.append(None)
        good = reading["quality_status"] == "valid"
        elapsed.append((timestamp - start).total_seconds())
        power.append(reading["active_power_w"] if good else None)
        current.append(reading["current_a"] if good else None)
        energy.append(
            first_increment + max(0, reading["cumulative_energy_kwh"] - baseline)
            if good and not counter_reset else None
        )
        previous_time = timestamp
    figures = []
    for title, unit, values, color in (
        ("Power throughout the cycle", "Power (W)", power, "#3BD7FF"),
        ("Current throughout the cycle", "Current (A)", current, "#E22DFF"),
        ("Energy accumulated", "Energy (kWh)", energy, "#72F6C7"),
    ):
        figure = go.Figure(go.Scatter(
            x=elapsed, y=values, mode="lines+markers", connectgaps=False,
            line={"color": color, "width": 3}, marker={"size": 4},
            name=unit, hovertemplate="%{x:.0f} s<br>%{y:.5g} " + unit + "<extra></extra>",
        ))
        figure.update_layout(
            **_transparent_chart_layout(285), title={"text": title, "x": 0.02},
            xaxis={"title": "Elapsed (s)", "automargin": True},
            yaxis={"title": unit, "rangemode": "tozero", "automargin": True},
            showlegend=False,
        )
        figures.append(figure)
    return figures


def build_report_comparison(cycles: list[dict]) -> list[go.Figure]:
    figures = []
    sources = list(dict.fromkeys(_report_source(c) for c in cycles))
    for field, title, unit in (
        ("meter_energy_kwh", "Energy used by cycle", "Energy (kWh)"),
        ("duration_seconds", "Duration by cycle", "Duration (s)"),
    ):
        figure = go.Figure()
        for source in sources:
            group = [(i, c) for i, c in enumerate(cycles, 1) if _report_source(c) == source]
            label, color = REPORT_SOURCES[source]
            figure.add_trace(go.Bar(
                x=[f"Cycle {i}" for i, _ in group],
                y=[c[field] if field != "meter_energy_kwh" or c["eligible_for_totals"]
                   else None for _, c in group],
                name=label, marker_color=color,
                hovertemplate="%{x}<br>%{y:.5g} " + unit + "<extra>%{fullData.name}</extra>",
            ))
        figure.update_layout(
            **_transparent_chart_layout(330), title={"text": title, "x": 0.02},
            xaxis={"categoryorder": "array",
                   "categoryarray": [f"Cycle {i}" for i in range(1, len(cycles) + 1)],
                   "automargin": True},
            yaxis={"title": unit, "rangemode": "tozero", "automargin": True},
            legend={"orientation": "h", "y": -0.35},
        )
        figures.append(figure)
    return figures


def render_report_dashboard(report: dict) -> None:
    """Readable report presentation; persistence and generation stay in app.py."""
    evidence = report["evidence_snapshot"]
    cycles = evidence["cycles"]
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
        for index, figure in enumerate(build_report_comparison(cycles)):
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
        format_func=lambda i: f"Cycle {i + 1} · " + datetime.fromisoformat(
            cycles[i]["started_at"]
        ).astimezone(MALAYSIA_TZ).strftime("%d %b, %H:%M"),
        key=f"report_cycle_{report['report_id']}",
    ) if len(cycles) > 1 else 0
    cycle = cycles[selected]
    st.subheader(f"Cycle {selected + 1} performance")
    st.caption(
        REPORT_SOURCES[_report_source(cycle)][0]
        + " · Sensor origin is not calibration evidence."
    )
    cells = st.columns(3)
    seconds = cycle["duration_seconds"]
    cells[0].metric("Cycle duration", f"{seconds // 60}m {seconds % 60:02d}s")
    cells[1].metric("Energy used", f"{cycle['meter_energy_kwh']:.5f} kWh")
    cells[2].metric("Estimated cycle cost", _charge_value(
        TariffEstimate.model_validate(cycle["tariff_estimate"])
    ))
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
    st.caption(
        "Electrical averages use valid samples; cost is a partial estimate, not a household bill."
    )
    st.text(cycle["quality_note"])
    if not cycle["eligible_for_totals"]:
        st.warning(
            "This cycle's recorded values are shown, but excluded from report energy/cost totals."
        )
    st.subheader("Recorded readings")
    figures = build_report_traces(cycle) if not manual else []
    if figures:
        for index, figure in enumerate(figures):
            render_resettable_chart(
                figure, chart_key=f"report_trace_{report['report_id']}_{selected}_{index}"
            )
        if not any(v is not None for v in figures[2].data[0].y):
            st.warning("Energy trace unavailable: the saved meter counter moves backwards.")
        st.caption(
            "Blank sections mark invalid samples or gaps longer than 15 seconds. "
            "Energy includes the cycle's recorded starting increment; gaps are not interpolated."
        )
        with st.expander("Reading values"):
            st.dataframe(pd.DataFrame(cycle["readings"]).rename(columns={
                "timestamp": "Recorded at", "voltage_v": "Voltage (V)",
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
    st.subheader("Insights & next steps")
    st.caption("Rule-based guidance from saved evidence—not an AI diagnosis or model score.")
    headings = {"observations": "What the readings show", "possibilities": "Possible explanations",
                "actions": "What to try next"}
    for section, messages in report_insights(evidence).items():
        st.markdown(f"**{headings[section]}**")
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
    with st.expander("Cost and data limitations"):
        st.write("Household usage and applicable tariff adjustments may be unresolved. "
                 "Taxes, incentives, fixed charges and other bill components may be excluded.")
        st.write(
            "The anomaly model is not trained; no physical fault or proven efficiency is inferred."
        )


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
        else updated_at.strftime("%d %b, %H:%M:%S UTC")
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


def render_live_state(view: LiveView) -> None:
    if view.state == "empty":
        st.info("No telemetry is stored yet. Run the normal simulator scenario, then refresh.")
        return

    state_labels = {
        "idle": ("Monitor ready", "Waiting for the next load cycle", "state-idle"),
        "heating": ("Active load detected", "A load cycle is being measured", "state-heating"),
        "cycle_complete": (
            "Cycle complete",
            "The latest load cycle has ended",
            "state-complete",
        ),
    }
    title, caption, css_class = state_labels[view.state]
    origin = view.recent_readings[-1].data_origin if view.recent_readings else "unknown"
    st.caption(f"Latest telemetry origin: {origin} · sensor mode is not calibration evidence")
    st.markdown(
        f'<section class="state-panel {css_class}" role="status" aria-live="polite">'
        f'<h2>{title}</h2><p>{caption}</p></section>',
        unsafe_allow_html=True,
    )
    primary = st.columns(3)
    primary[0].metric("Active power now", f"{view.latest_power_w or 0:,.0f} W")
    primary[1].metric("Voltage now", f"{view.latest_voltage_v or 0:.1f} V")
    primary[2].metric("Current now", f"{view.latest_current_a or 0:.2f} A")

    render_resettable_chart(
        build_live_power_figure(view, start_threshold_w=1000.0),
        chart_key="live_power_chart",
    )
    st.caption(
        "Cyan: recorded power · magenta dashed: 1,000 W start threshold · orange band: "
        "detected cycle. Five-second sampling."
    )

    if view.active_cycle is not None:
        origin = view.active_cycle.data_origin
        active = st.columns(3)
        active[0].metric("Cycle elapsed so far", f"{view.elapsed_seconds or 0} s")
        active[1].metric(
            f"{'Simulated' if origin == 'simulated' else 'Recorded'} cycle energy so far",
            f"{view.energy_so_far_kwh or 0:.5f} kWh",
        )
        active[2].metric(
            "Estimated gross cycle charge so far",
            _charge_value(view.charge_so_far),
        )
    elif view.last_cycle is not None:
        st.subheader("Last completed cycle")
        receipt = st.columns(3)
        receipt[0].metric("Cycle duration", f"{view.last_cycle.duration_seconds} s")
        receipt[1].metric(view.last_cycle.energy_label, f"{view.last_cycle.energy_kwh:.5f} kWh")
        receipt[2].metric("Estimated gross cycle charge", view.last_cycle.charge_text)
    energy_figure = build_cycle_energy_figure(view)
    if energy_figure is not None:
        render_resettable_chart(
            energy_figure,
            chart_key="live_cycle_energy_chart",
        )

    if view.recent_readings:
        latest = view.recent_readings[-1]
        st.subheader("Power quality")
        quality = st.columns(2)
        quality[0].metric("Frequency now", f"{latest.frequency_hz:.2f} Hz")
        quality[1].metric("Power factor now", f"{latest.power_factor:.3f}")
        render_resettable_chart(
            build_power_quality_figure(view),
            chart_key="live_power_quality_chart",
        )
        st.caption(
            "Magenta: frequency · green: power factor · dashed: nominal 50 Hz reference. "
            "This is not an automatic pass/fail assessment."
        )
    st.caption(view.quality_note)


def build_live_power_figure(view: LiveView, start_threshold_w: float) -> go.Figure:
    readings = view.recent_readings
    timestamps = [reading.timestamp.astimezone(MALAYSIA_TZ) for reading in readings]
    power = [reading.active_power_w for reading in readings]
    custom_hover = [
        [reading.voltage_v, reading.current_a, reading.quality_status.value]
        for reading in readings
    ]
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=timestamps,
            y=power,
            mode="lines",
            name="Active power",
            line={"color": "#3BD7FF", "width": 3},
            fill="tozeroy",
            fillcolor="rgba(59, 215, 255, 0.10)",
            customdata=custom_hover,
            hovertemplate=(
                "%{x|%H:%M:%S}<br>%{y:,.0f} W"
                "<br>Voltage %{customdata[0]:.1f} V"
                "<br>Current %{customdata[1]:.2f} A"
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
                hovertemplate="Current %{y:,.0f} W<extra></extra>",
            )
        )
        figure.add_hline(
            y=start_threshold_w,
            line={"color": "#E22DFF", "width": 2, "dash": "dash"},
        )
        cycle = view.active_cycle or view.last_cycle
        if cycle is not None:
            cycle_end = cycle.ended_at or readings[-1].timestamp
            chart_start = readings[0].timestamp
            chart_end = readings[-1].timestamp
            if cycle_end >= chart_start and cycle.started_at <= chart_end:
                figure.add_vrect(
                    x0=max(cycle.started_at, chart_start).astimezone(MALAYSIA_TZ),
                    x1=min(cycle_end, chart_end).astimezone(MALAYSIA_TZ),
                    fillcolor="rgba(255, 116, 72, 0.10)",
                    line_width=0,
                    layer="below",
                )
    invalid = [
        (timestamp, reading.active_power_w)
        for timestamp, reading in zip(timestamps, readings, strict=True)
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
            "title": "Malaysia time",
            "gridcolor": "rgba(184,196,255,0.10)",
            "tickformat": "%H:%M",
            "automargin": True,
        },
        yaxis={
            "title": "Power (W)",
            "gridcolor": "rgba(184,196,255,0.12)",
            "rangemode": "tozero",
            "automargin": True,
        },
        showlegend=False,
    )
    return figure


def build_power_quality_figure(view: LiveView) -> go.Figure:
    readings = view.recent_readings
    timestamps = [reading.timestamp.astimezone(MALAYSIA_TZ) for reading in readings]
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.14,
    )
    figure.add_trace(
        go.Scatter(
            x=timestamps,
            y=[reading.frequency_hz for reading in readings],
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
            y=[reading.power_factor for reading in readings],
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
        title_text="Malaysia time",
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


def build_cycle_energy_figure(view: LiveView) -> go.Figure | None:
    readings = view.cycle_readings
    if not readings:
        return None
    started_at = readings[0].timestamp
    elapsed = [(reading.timestamp - started_at).total_seconds() for reading in readings]
    baseline = readings[0].cumulative_energy_kwh
    measured_total = (
        view.active_cycle.meter_energy_kwh
        if view.active_cycle is not None
        else view.last_cycle.energy_kwh
        if view.last_cycle is not None
        else 0.0
    )
    observed_span = max(0.0, readings[-1].cumulative_energy_kwh - baseline)
    first_increment = max(0.0, measured_total - observed_span)
    energy = [
        first_increment + max(0.0, reading.cumulative_energy_kwh - baseline)
        for reading in readings
    ]
    state = "In progress" if view.active_cycle is not None else "Completed cycle"
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


def render_cycle_filters(store: SQLiteTelemetryStore) -> tuple[str, str]:
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
    return str(status or "All"), str(label or "All labels")


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
                "Start time (Malaysia)",
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
    if not items:
        st.info("No cycles match these filters.")
        return None
    st.caption("Select anywhere within a row to inspect its full receipt. Times use Malaysia time.")
    frame = pd.DataFrame(
        [
            {
                "Start (MYT)": item.started_at.astimezone(MALAYSIA_TZ).strftime(
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
    event = st.dataframe(
        frame,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-cell",
        key="cycle_history",
    )
    cells = event.selection.cells
    return items[cells[0][0]].cycle_id if cells else None


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
        f'<p class="eyebrow">SELECTED CYCLE · UTC AUDIT TIME</p>'
        f"<h2>{summary.started_at:%d %b %Y, %H:%M UTC}</h2>"
        f"<p>{summary.status.value.title()} · "
        f"{summary.assessment.value.replace('_', ' ').title()}</p></section>",
        unsafe_allow_html=True,
    )
    metrics = st.columns(3)
    metrics[0].metric("Cycle duration", f"{summary.duration_seconds} s")
    metrics[1].metric(detail.energy_label, f"{summary.meter_energy_kwh:.5f} kWh")
    st.caption(f"Data origin: {summary.data_origin}. Sensor mode is not calibration evidence.")
    metrics[2].metric("Estimated gross cycle charge", _charge_value(detail.tariff_estimate))
    secondary = st.columns(3)
    if detail.record_source == "manual":
        secondary[0].metric("Record source", "Manual entry")
        secondary[1].metric("Assessment", "Not evaluated")
    else:
        secondary[0].metric("Average power", f"{summary.average_power_w:,.0f} W")
        secondary[1].metric("Peak power", f"{summary.peak_power_w:,.0f} W")
    secondary[2].metric("Cycle label", detail.cycle_label or "Unlabelled")
    st.caption(detail.comparison_text)
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
                "<br>Current %{customdata[1]:.2f} A"
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
        showlegend=False,
    )
    return figure


def tariff_disclosure_text(
    estimate: TariffEstimate,
    energy_label: str = "Measured cycle energy",
) -> str:
    month = estimate.occurred_at.strftime("%B %Y")
    amounts = "Unavailable"
    if estimate.amount_rm is not None:
        amounts = f"RM {estimate.amount_rm:.4f}"
    elif estimate.amount_range_rm is not None:
        low, high = estimate.amount_range_rm
        amounts = f"RM {low:.4f} to RM {high:.4f}"
    afa_rate = (
        estimate.afa_rate_sen_per_kwh
        if estimate.afa_rate_sen_per_kwh is not None
        else "eligibility unresolved"
    )
    rate_lines = (
        f"Energy rate: {estimate.energy_rate_sen_per_kwh or 'tier unresolved'} sen/kWh; "
        f"capacity: {estimate.capacity_rate_sen_per_kwh or 'unavailable'} sen/kWh; "
        f"network: {estimate.network_rate_sen_per_kwh or 'unavailable'} sen/kWh; "
        f"AFA: {afa_rate} sen/kWh."
    )
    afa_name = f"{month} AFA" if estimate.afa_period else "AFA unavailable"
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
        f"Tariff version: {estimate.tariff_version or 'unavailable'}; effective "
        f"{estimate.tariff_effective_from or 'unknown'} to "
        f"{estimate.tariff_effective_to or 'unknown'}.  \n"
        f"{afa_name}. {energy_label}: {estimate.energy_kwh} kWh.  \n"
        f"{rate_lines}  \nEstimated gross variable cycle charge: {amounts}.  \n"
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


def render_cycle_label(
    store: SQLiteTelemetryStore,
    cycle_id: UUID,
    current: str | None,
) -> None:
    st.subheader("Label this cycle")
    st.caption(
        "Create reusable labels for an appliance, volume, operating mode, or test condition."
    )
    select_key = f"cycle_label_{cycle_id}"
    new_label_key = f"new_cycle_label_{cycle_id}"
    error_key = f"cycle_label_error_{cycle_id}"

    with st.expander("Add a reusable label", expanded=not store.list_cycle_labels()):
        st.text_input(
            "New label",
            max_chars=40,
            placeholder="For example: 0.5 L or Full kettle",
            key=new_label_key,
        )
        if st.button("Add label", key=f"add_cycle_label_{cycle_id}"):
            try:
                canonical = store.add_cycle_label(st.session_state[new_label_key])
                st.session_state[select_key] = canonical
                st.session_state[error_key] = None
                st.rerun()
            except ValueError as exc:
                st.session_state[error_key] = str(exc)

    if st.session_state.get(error_key):
        st.error(st.session_state[error_key])

    labels = store.list_cycle_labels()
    if not labels:
        st.info("Add your first reusable label before assigning this cycle.")
        return

    if select_key not in st.session_state:
        st.session_state[select_key] = current if current in labels else labels[0]
    selected = st.selectbox("Saved labels", labels, key=select_key)

    def save_selected() -> None:
        try:
            store.save_cycle_label(cycle_id, str(selected))
            st.session_state[error_key] = None
            st.success("Cycle label saved")
        except Exception:
            st.session_state[error_key] = "Label was not saved"
            st.error(st.session_state[error_key])

    save, later = st.columns(2)
    if save.button("Save to cycle", key=f"save_cycle_label_{cycle_id}", type="primary"):
        save_selected()
    if later.button("Label later", key=f"skip_cycle_label_{cycle_id}"):
        st.caption("This cycle will remain unlabelled for now.")

    with st.expander("Manage reusable labels"):
        st.caption(
            "Removing a label hides it from future choices. Existing cycle records keep it "
            "for audit history. Adding the same label later restores it."
        )
        remove_key = f"remove_cycle_label_{cycle_id}"
        label_to_remove = st.selectbox(
            "Label to remove",
            labels,
            key=remove_key,
        )
        if st.button(
            "Remove label",
            icon=":material/delete:",
            key=f"archive_cycle_label_{cycle_id}",
        ):
            try:
                store.archive_cycle_label(str(label_to_remove))
                st.session_state.pop(select_key, None)
                if st.session_state.get("label_filter") == label_to_remove:
                    st.session_state["label_filter"] = "All labels"
                st.session_state[error_key] = None
                st.success("Label removed from future choices")
                st.rerun()
            except ValueError as exc:
                st.session_state[error_key] = str(exc)
                st.error(st.session_state[error_key])
