from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import streamlit as st
from components import (
    build_cycle_figure,
    render_command_bar,
    render_cycle_filters,
    render_cycle_history,
    render_cycle_receipt,
    render_live_state,
    render_tariff_disclosure,
    render_volume_label,
)

from energy_monitor.dashboard_queries import get_cycle_detail, get_live_view, list_cycle_items
from energy_monitor.models import CycleStatus
from energy_monitor.storage import SQLiteTelemetryStore

PROJECT_ROOT = Path(__file__).parents[1]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "telemetry.db"
DEFAULT_SESSION_STATE = {
    "mode": "Live",
    "auto_refresh": True,
    "selected_cycle_id": None,
    "status_filter": "All",
    "volume_filter": "All volumes",
}


def load_styles() -> None:
    css = (Path(__file__).parent / "styles.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def monthly_usage_context() -> Decimal | None:
    raw = os.environ.get("ENERGY_MONITOR_MONTHLY_KWH", "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return value if value >= 0 else None


st.set_page_config(
    page_title="Kettle cycle monitor",
    page_icon="♨️",
    layout="wide",
    initial_sidebar_state="collapsed",
)
load_styles()
for key, value in DEFAULT_SESSION_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value

database_path = Path(os.environ.get("ENERGY_MONITOR_DB", DEFAULT_DATABASE))
store = SQLiteTelemetryStore(database_path)

st.title("Kettle cycle monitor")
st.caption("Household kettle · automatic cycle detection · software simulation")

controls = st.columns([2, 2, 1])
with controls[0]:
    st.segmented_control(
        "Dashboard mode",
        ["Live", "Cycles"],
        key="mode",
        label_visibility="collapsed",
    )
with controls[1]:
    st.toggle("Auto-refresh every 5 seconds", key="auto_refresh")
with controls[2]:
    if st.button("Refresh now", type="primary", width="stretch"):
        st.rerun()


if st.session_state.mode == "Live":

    @st.fragment(run_every="5s" if st.session_state.auto_refresh else None)
    def live_panel() -> None:
        view = get_live_view(store, datetime.now(UTC))
        st.markdown(
            render_command_bar(view.connection_state, view.updated_at),
            unsafe_allow_html=True,
        )
        render_live_state(view)

    live_panel()
else:
    live_status = get_live_view(store, datetime.now(UTC))
    st.markdown(
        render_command_bar(live_status.connection_state, live_status.updated_at),
        unsafe_allow_html=True,
    )
    status_filter, volume_filter = render_cycle_filters()
    volume_values = {
        "All volumes": "all",
        "0.5 L": "0.5_l",
        "1.0 L": "1.0_l",
        "1.5 L": "1.5_l",
        "Unknown": "unknown",
    }
    items = list_cycle_items(
        store,
        status_filter=status_filter.lower(),
        volume_filter=volume_values[volume_filter],
    )
    selected = render_cycle_history(items)
    if selected is not None:
        st.session_state.selected_cycle_id = str(selected)
    if st.session_state.selected_cycle_id is None:
        newest_completed = next(
            (item for item in items if item.status is CycleStatus.COMPLETED),
            None,
        )
        if newest_completed is not None:
            st.session_state.selected_cycle_id = str(newest_completed.cycle_id)

    if st.session_state.selected_cycle_id is not None:
        detail = get_cycle_detail(
            store,
            st.session_state.selected_cycle_id,
            monthly_usage_context(),
        )
        render_cycle_receipt(detail)
        st.plotly_chart(
            build_cycle_figure(detail, start_threshold_w=1000.0),
            width="stretch",
            config={"displayModeBar": False},
        )
        st.caption(detail.chart_summary)
        render_volume_label(store, detail.summary.cycle_id, detail.effective_volume)
        render_tariff_disclosure(detail.tariff_estimate)

st.caption(
    "Simulation evidence only. Unusual behaviour means different from the recorded baseline; "
    "it does not confirm a physical fault."
)
