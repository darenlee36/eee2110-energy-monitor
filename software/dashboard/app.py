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
    render_cycle_label,
    render_cycle_receipt,
    render_live_state,
    render_manual_cycle_entry,
    render_resettable_chart,
    render_tariff_disclosure,
)
from dotenv import load_dotenv

from energy_monitor.dashboard_queries import get_cycle_detail, get_live_view, list_cycle_items
from energy_monitor.models import CycleStatus
from energy_monitor.storage import SQLiteTelemetryStore
from energy_monitor.supabase_sync import (
    SupabaseConfig,
    SupabaseSyncError,
    sync_supabase_telemetry,
)

PROJECT_ROOT = Path(__file__).parents[1]
load_dotenv(PROJECT_ROOT / ".env")
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "telemetry.db"
DEFAULT_CLOUD_CACHE = PROJECT_ROOT / "data" / "supabase-cache.db"
DEFAULT_SESSION_STATE = {
    "mode": "Live",
    "auto_refresh": True,
    "selected_cycle_id": None,
    "status_filter": "All",
    "label_filter": "All labels",
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
    page_title="Energy Monitor",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)
load_styles()
for key, value in DEFAULT_SESSION_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value

source_mode = os.environ.get("ENERGY_MONITOR_SOURCE", "auto").strip().lower()
supabase_config = SupabaseConfig.from_environment()
use_supabase = source_mode == "supabase" or (
    source_mode == "auto" and supabase_config is not None
)
database_default = DEFAULT_CLOUD_CACHE if use_supabase else DEFAULT_DATABASE
database_path = Path(os.environ.get("ENERGY_MONITOR_DB", database_default))
store = SQLiteTelemetryStore(database_path)


def sync_cloud_source() -> None:
    if not use_supabase:
        return
    if supabase_config is None:
        st.warning("Supabase source selected, but server credentials are missing.")
        return
    try:
        sync_supabase_telemetry(store, supabase_config)
    except SupabaseSyncError:
        st.warning("Cloud sync unavailable. Showing locally cached data.")

st.title("Energy Monitor")
st.caption("Live electrical telemetry · automatic load-cycle detection · usage insights")
st.caption(
    "Data source: Supabase cloud with local cache"
    if use_supabase
    else "Data source: local development database"
)

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
        sync_cloud_source()
        view = get_live_view(store, datetime.now(UTC))
        st.markdown(
            render_command_bar(view.connection_state, view.updated_at),
            unsafe_allow_html=True,
        )
        render_live_state(view)

    live_panel()
else:
    sync_cloud_source()
    live_status = get_live_view(store, datetime.now(UTC))
    st.markdown(
        render_command_bar(live_status.connection_state, live_status.updated_at),
        unsafe_allow_html=True,
    )
    manual_cycle_id = render_manual_cycle_entry(store)
    if manual_cycle_id is not None:
        st.session_state.selected_cycle_id = str(manual_cycle_id)
    status_filter, label_filter = render_cycle_filters(store)
    label_value = {
        "All labels": "all",
        "Unlabelled": "__unlabelled__",
    }.get(label_filter, label_filter)
    items = list_cycle_items(
        store,
        status_filter=status_filter.lower(),
        label_filter=label_value,
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
        if detail.readings:
            render_resettable_chart(
                build_cycle_figure(detail, start_threshold_w=1000.0),
                chart_key="cycle_detail_chart",
            )
            st.caption(
                f"Cyan: measured power · magenta dashed: 1,000 W start threshold · "
                f"green rings: cycle boundaries. {detail.chart_summary}"
            )
        else:
            st.info(detail.chart_summary)
        render_cycle_label(store, detail.summary.cycle_id, detail.cycle_label)
        render_tariff_disclosure(
            detail.tariff_estimate,
            "Entered cycle energy"
            if detail.record_source == "manual"
            else "Measured cycle energy",
        )

st.caption(
    "Simulation evidence only. Unusual behaviour means different from the recorded baseline; "
    "it does not confirm a physical fault."
)
