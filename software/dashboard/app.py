from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from energy_monitor.metrics import (
    build_alerts,
    calculate_cost_rm,
    calculate_energy_used_kwh,
    derive_connection_state,
)
from energy_monitor.models import TelemetryReading
from energy_monitor.storage import SQLiteTelemetryStore

PROJECT_ROOT = Path(__file__).parents[1]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "telemetry.db"
DISPLAY_TIMEZONE = ZoneInfo("Asia/Kuala_Lumpur")


def reading_from_row(row: dict[str, object]) -> TelemetryReading:
    fields = TelemetryReading.model_fields
    return TelemetryReading.model_validate({key: row[key] for key in fields})


def load_styles() -> None:
    css = (Path(__file__).parent / "styles.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def render_alert(level: str, title: str, message: str) -> None:
    body = f"**{title}.** {message}"
    if level == "error":
        st.error(body)
    elif level == "warning":
        st.warning(body)
    else:
        st.info(body)


st.set_page_config(
    page_title="Appliance energy monitor",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)
load_styles()

st.title("Appliance energy monitor")
st.caption(
    "Software-development view · synthetic kettle telemetry · no 230 V integration"
)

with st.sidebar:
    st.header("Estimate settings")
    tariff_rate = st.number_input(
        "Development tariff rate (RM/kWh)",
        min_value=0.0,
        max_value=5.0,
        value=0.60,
        step=0.01,
        help="Configurable demonstration value. It is not an official TNB bill rate.",
    )
    history_limit = st.slider("Recent readings", 30, 1000, 240, 30)
    st.caption("Cost is a development estimate, not an official TNB bill.")

database_path = Path(os.environ.get("ENERGY_MONITOR_DB", DEFAULT_DATABASE))
store = SQLiteTelemetryStore(database_path)
rows = store.fetch_recent_readings(limit=history_limit)

if not rows:
    st.info(
        "No telemetry is stored yet. Start the local API, then run the simulator to populate "
        "this development dashboard."
    )
    st.stop()

latest = reading_from_row(rows[-1])
connection_state = derive_connection_state(latest.timestamp, datetime.now(UTC))
last_update = latest.timestamp.astimezone(DISPLAY_TIMEZONE)
energy_used_kwh = calculate_energy_used_kwh(
    [float(row["cumulative_energy_kwh"]) for row in rows]
)
estimated_cost_rm = calculate_cost_rm(energy_used_kwh, tariff_rate)

st.markdown(
    (
        '<div class="monitor-status"><span class="monitor-dot"></span>'
        f"Connection: <strong>{connection_state}</strong> · Last reading: "
        f"{last_update:%d %b %Y, %H:%M:%S} MYT</div>"
    ),
    unsafe_allow_html=True,
)

primary = st.columns(3)
primary[0].metric("Active power", f"{latest.active_power_w:,.0f} W")
primary[1].metric("Voltage", f"{latest.voltage_v:.1f} V")
primary[2].metric("Current", f"{latest.current_a:.2f} A")

secondary = st.columns(3)
secondary[0].metric("Energy in view", f"{energy_used_kwh:.4f} kWh")
secondary[1].metric("Estimated cost", f"RM {estimated_cost_rm:.3f}")
secondary[2].metric("Controller battery", f"{latest.battery_voltage_v:.2f} V")

alerts = build_alerts(latest, connection_state)
if alerts:
    st.subheader("Alerts")
    for alert in alerts:
        render_alert(alert.level.value, alert.title, alert.message)

st.subheader("Recent power history")
frame = pd.DataFrame(rows)
frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(
    DISPLAY_TIMEZONE
)
figure = go.Figure()
figure.add_trace(
    go.Scatter(
        x=frame["timestamp"],
        y=frame["active_power_w"],
        mode="lines",
        name="Active power",
        line={"color": "#0b7285", "width": 2.5},
        fill="tozeroy",
        fillcolor="rgba(11, 114, 133, 0.10)",
        hovertemplate="%{x|%H:%M:%S}<br>%{y:,.0f} W<extra></extra>",
    )
)
figure.update_layout(
    height=360,
    margin={"l": 10, "r": 10, "t": 10, "b": 10},
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="#ffffff",
    hovermode="x unified",
    xaxis={"title": None, "gridcolor": "#eef1f3"},
    yaxis={"title": "Active power (W)", "rangemode": "tozero", "gridcolor": "#e4e9ed"},
    legend={"orientation": "h", "y": 1.08, "x": 0},
)
st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})

st.subheader("Latest measurement")
detail_columns = st.columns(3)
detail_columns[0].metric("Frequency", f"{latest.frequency_hz:.2f} Hz")
detail_columns[1].metric("Power factor", f"{latest.power_factor:.3f}")
detail_columns[2].metric("Appliance state", latest.appliance_state.value.title())

with st.expander("Inspect recent records"):
    st.dataframe(
        frame[
            [
                "timestamp",
                "active_power_w",
                "voltage_v",
                "current_a",
                "cumulative_energy_kwh",
                "appliance_state",
                "anomaly_status",
                "quality_status",
            ]
        ].tail(20),
        width="stretch",
        hide_index=True,
        column_config={
            "timestamp": st.column_config.DatetimeColumn("Time", format="HH:mm:ss"),
            "active_power_w": st.column_config.NumberColumn("Power", format="%.1f W"),
            "voltage_v": st.column_config.NumberColumn("Voltage", format="%.1f V"),
            "current_a": st.column_config.NumberColumn("Current", format="%.2f A"),
            "cumulative_energy_kwh": st.column_config.NumberColumn(
                "Cumulative energy", format="%.5f kWh"
            ),
            "appliance_state": "Appliance",
            "anomaly_status": "Anomaly status",
            "quality_status": "Data quality",
        },
    )

st.caption(
    "Synthetic anomaly labels exercise the interface only. Isolation Forest scoring will be "
    "added after validated operating cycles are available."
)
