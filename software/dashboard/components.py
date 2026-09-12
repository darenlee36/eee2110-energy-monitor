from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from energy_monitor.dashboard_queries import CycleDetail, CycleListItem, LiveView, display_volume
from energy_monitor.models import ConnectionState, TariffEstimate, VolumeClass
from energy_monitor.storage import SQLiteTelemetryStore


def render_command_bar(connection: ConnectionState, updated_at: datetime | None) -> str:
    updated = (
        "Waiting for data"
        if updated_at is None
        else updated_at.strftime("%d %b, %H:%M:%S UTC")
    )
    return (
        f'<div class="command-bar"><span class="connection connection-{connection.value}">'
        f"● {connection.value.title()}</span><span>Last reading: {updated}</span></div>"
    )


def _charge_value(estimate: TariffEstimate | None) -> str:
    if estimate is None:
        return "Unavailable"
    if estimate.amount_rm is not None:
        return f"RM {estimate.amount_rm:.4f}"
    if estimate.amount_range_rm is not None:
        low, high = estimate.amount_range_rm
        return f"RM {low:.4f}–{high:.4f}"
    return "Unavailable"


def render_live_state(view: LiveView) -> None:
    if view.state == "empty":
        st.info("No telemetry is stored yet. Run the normal simulator scenario, then refresh.")
        return

    state_labels = {
        "idle": ("Kettle idle", "Waiting for the next boil", "state-idle"),
        "heating": ("Heating", "A cycle is being measured", "state-heating"),
        "cycle_complete": ("Cycle complete", "The latest boil has ended", "state-complete"),
    }
    title, caption, css_class = state_labels[view.state]
    st.markdown(
        f'<section class="state-panel {css_class}"><p class="eyebrow">LIVE STATE</p>'
        f'<h2>{title}</h2><p>{caption}</p></section>',
        unsafe_allow_html=True,
    )
    primary = st.columns(3)
    primary[0].metric("Active power now", f"{view.latest_power_w or 0:,.0f} W")
    primary[1].metric("Voltage now", f"{view.latest_voltage_v or 0:.1f} V")
    primary[2].metric("Current now", f"{view.latest_current_a or 0:.2f} A")

    if view.active_cycle is not None:
        active = st.columns(3)
        active[0].metric("Cycle elapsed so far", f"{view.elapsed_seconds or 0} s")
        active[1].metric(
            "Measured cycle energy so far",
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
        receipt[1].metric("Measured cycle energy", f"{view.last_cycle.energy_kwh:.5f} kWh")
        receipt[2].metric("Estimated gross cycle charge", view.last_cycle.charge_text)
    st.caption(view.quality_note)


def render_cycle_filters() -> tuple[str, str]:
    status = st.segmented_control(
        "Cycle status",
        ["All", "Normal", "Unusual", "Incomplete"],
        key="status_filter",
    )
    volume = st.pills(
        "Water volume",
        ["All volumes", "0.5 L", "1.0 L", "1.5 L", "Unknown"],
        key="volume_filter",
    )
    return str(status or "All"), str(volume or "All volumes")


def render_cycle_history(items: list[CycleListItem]) -> UUID | None:
    if not items:
        st.info("No cycles match these filters.")
        return None
    frame = pd.DataFrame(
        [
            {
                "Start": item.started_at,
                "Duration": item.duration_seconds,
                "Energy": item.energy_kwh,
                "Charge": item.charge_text,
                "Volume": display_volume(item.effective_volume),
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
        selection_mode="single-row",
        key="cycle_history",
    )
    selected_rows = event.selection.rows
    return items[selected_rows[0]].cycle_id if selected_rows else None


def render_cycle_receipt(detail: CycleDetail) -> None:
    summary = detail.summary
    status_class = {
        "normal": "status-normal",
        "unusual": "status-unusual",
        "insufficient_data": "status-warning",
        "not_evaluated": "status-warning",
    }[summary.assessment.value]
    st.markdown(
        f'<section class="cycle-receipt {status_class}"><p class="eyebrow">SELECTED CYCLE</p>'
        f"<h2>{summary.started_at:%d %b %Y, %H:%M UTC}</h2>"
        f"<p>{summary.status.value.title()} · "
        f"{summary.assessment.value.replace('_', ' ').title()}</p></section>",
        unsafe_allow_html=True,
    )
    metrics = st.columns(3)
    metrics[0].metric("Cycle duration", f"{summary.duration_seconds} s")
    metrics[1].metric("Measured cycle energy", f"{summary.meter_energy_kwh:.5f} kWh")
    metrics[2].metric("Estimated gross cycle charge", _charge_value(detail.tariff_estimate))
    secondary = st.columns(3)
    secondary[0].metric("Average power", f"{summary.average_power_w:,.0f} W")
    secondary[1].metric("Peak power", f"{summary.peak_power_w:,.0f} W")
    secondary[2].metric("Water volume", display_volume(detail.effective_volume))
    st.caption(detail.comparison_text)
    st.caption(summary.quality_note)


def build_cycle_figure(detail: CycleDetail, start_threshold_w: float) -> go.Figure:
    elapsed = [
        (reading.timestamp - detail.summary.started_at).total_seconds()
        for reading in detail.readings
    ]
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=elapsed,
            y=[reading.active_power_w for reading in detail.readings],
            mode="lines",
            name="Selected cycle",
        )
    )
    figure.add_hline(
        y=start_threshold_w,
        line_dash="dash",
        line_color="#E22DFF",
        annotation_text="Detection threshold",
    )
    figure.update_layout(
        xaxis_title="Elapsed time (s)",
        yaxis_title="Active power (W)",
        hovermode="x unified",
        height=380,
    )
    return figure


def render_tariff_disclosure(estimate: TariffEstimate) -> None:
    with st.expander("How this estimate was calculated"):
        st.write(f"{estimate.provider} {estimate.scheme} · {estimate.tariff_version}")
        st.write(f"Measured cycle energy: {estimate.energy_kwh} kWh")
        st.write("This is not a complete household bill.")


def render_volume_label(
    store: SQLiteTelemetryStore,
    cycle_id: UUID,
    current: VolumeClass,
) -> None:
    st.subheader("Label the water volume")
    options = {
        "0.5 L": VolumeClass.HALF_LITRE,
        "1.0 L": VolumeClass.ONE_LITRE,
        "1.5 L": VolumeClass.ONE_AND_HALF_LITRES,
        "Unknown": VolumeClass.UNKNOWN,
    }
    selected = st.pills(
        "Water used in this cycle",
        list(options),
        default=display_volume(current),
        key=f"volume_label_{cycle_id}",
    )
    save, skip = st.columns(2)
    if save.button("Save label", key=f"save_volume_{cycle_id}", type="primary"):
        store.save_volume_label(cycle_id, options[str(selected)])
        st.success("Volume label saved")
    if skip.button("Skip", key=f"skip_volume_{cycle_id}"):
        st.caption("You can label this cycle later.")
