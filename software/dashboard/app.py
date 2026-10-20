from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

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

from energy_monitor.cloud_edits import SupabaseCycleStore
from energy_monitor.dashboard_queries import get_cycle_detail, get_live_view, list_cycle_items
from energy_monitor.models import CycleDetectionSettings, CycleStatus
from energy_monitor.reporting import build_report
from energy_monitor.simulator import batch_readings, generate_readings
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


def owner_can_edit() -> bool:
    if not use_supabase:
        return True
    password = os.environ.get("ENERGY_MONITOR_EDIT_PASSWORD", "")
    fingerprint = hashlib.sha256(password.encode()).hexdigest() if password else ""
    authorized = bool(password) and hmac.compare_digest(
        st.session_state.get("owner_session", ""), fingerprint
    )
    with st.expander("Owner controls"):
        if authorized:
            st.caption("Editing unlocked for this session.")
            if st.button("Lock editing"):
                st.session_state.pop("owner_session", None)
                st.rerun()
        elif not password:
            st.caption("Viewing only. Owner editing is not configured.")
        else:
            with st.form("owner_login", clear_on_submit=True):
                entered = st.text_input("Owner password", type="password")
                submitted = st.form_submit_button("Unlock editing")
            if submitted:
                if hmac.compare_digest(entered.encode(), password.encode()):
                    st.session_state.owner_session = fingerprint
                    st.rerun()
                st.error("Incorrect owner password")
    return authorized


def demo_database_path() -> Path:
    if "demo_database_path" not in st.session_state:
        # ponytail: per-session temp DB; use managed demo storage only if public traffic grows.
        st.session_state.demo_database_path = str(
            Path(tempfile.gettempdir()) / f"energy-monitor-demo-{uuid4().hex}.db"
        )
    return Path(st.session_state.demo_database_path)


def seed_demo_store(store: SQLiteTelemetryStore) -> None:
    if store.count_readings() > 0:
        return
    count = 65
    readings = generate_readings(
        count,
        datetime.now(UTC) - timedelta(seconds=(count - 1) * 5),
    )
    for batch in batch_readings(readings):
        store.insert_batch(batch)
    store.process_cycles(CycleDetectionSettings.simulation_defaults())


st.set_page_config(
    page_title="Energy Monitor",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)
load_styles()
source_mode = os.environ.get("ENERGY_MONITOR_SOURCE", "auto").strip().lower()
for key, value in DEFAULT_SESSION_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = False if key == "auto_refresh" and source_mode == "demo" else value

supabase_config = SupabaseConfig.from_environment()
use_demo = source_mode == "demo"
use_supabase = source_mode == "supabase" or (
    source_mode == "auto" and supabase_config is not None
)
database_default = (
    demo_database_path()
    if use_demo
    else DEFAULT_CLOUD_CACHE
    if use_supabase
    else DEFAULT_DATABASE
)
database_path = Path(os.environ.get("ENERGY_MONITOR_DB", database_default))
store = (
    SupabaseCycleStore(database_path, supabase_config)
    if use_supabase and supabase_config is not None
    else SQLiteTelemetryStore(database_path)
)
if use_demo:
    seed_demo_store(store)


def dashboard_now() -> datetime:
    if use_demo:
        return store.latest_telemetry_timestamp() or datetime.now(UTC)
    return datetime.now(UTC)


def sync_cloud_source() -> None:
    if not use_supabase:
        return
    if supabase_config is None:
        st.warning("Supabase source selected, but server credentials are missing.")
        return
    try:
        sync_supabase_telemetry(store, supabase_config)
        if isinstance(store, SupabaseCycleStore):
            store.refresh_edits()
    except SupabaseSyncError:
        st.warning("Cloud sync unavailable. Showing locally cached data.")
    except ValueError:
        st.warning("Cloud edits unavailable. Showing locally cached records.")


def render_reports() -> None:
    st.header("Reports")
    st.caption("Saved cycle evidence · template engine · no AI calls or reporting schedule enabled")

    def history(profile=None, device=None):
        try:
            return store.fetch_reports(profile, device)
        except SupabaseSyncError:
            st.warning(
                "Cloud history unavailable. Showing cached reports; history may be incomplete."
            )
            return SQLiteTelemetryStore.fetch_reports(store, profile, device)

    def malaysia_time(value: str) -> str:
        return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Kuala_Lumpur")).strftime(
            "%d %b %Y, %H:%M:%S"
        )

    try:
        recent = history()
        completed = [c for c in store.fetch_cycles() if c["status"] == "completed"]
        pairs = sorted({(r["profile_id"], r["device_id"]) for r in [*recent, *completed]})
        st.caption(
            "History: latest 100 reports per selected device/profile. Times use Malaysia time."
        )
        if not pairs:
            st.info(
                "No saved reports or completed cycles yet. "
                "Run the laptop worker after cycles arrive."
            )
            return
        profile, device = st.selectbox(
            "Report device / profile", pairs, format_func=lambda p: f"{p[1]} / {p[0]}"
        )
        cycles = store.fetch_report_cycles(profile, device)
        selected_report = None
        if can_edit and cycles:
            with st.expander("Generate template report", expanded=True):
                st.caption(
                    "Generate explicitly. Refreshing or opening Reports never generates reports."
                )
                cycle_by_id = {c["cycle_id"]: c for c in cycles}
                cycle_id = st.selectbox(
                    "Completed cycle", list(cycle_by_id), format_func=lambda cid:
                    f"{malaysia_time(cycle_by_id[cid]['started_at'])} · {cid[-8:]}")
                count = st.selectbox("Batch size (cycles)", [5, 10, 20])
                individual = st.button("Generate cycle report")
                batch = st.button("Generate batch report")
                if individual or batch:
                    try:
                        draft = build_report(
                            store, profile_id=profile, device_id=device,
                            cycle_id=cycle_id if individual else None,
                            cycle_count=1 if individual else count,
                        )
                        selected_report = store.save_report(draft)
                        st.success(
                            "Template report saved (identical retries reuse the saved report)."
                        )
                    except SupabaseSyncError:
                        st.error(
                            "Cloud save not confirmed. Retry safely; duplicates are prevented."
                        )
                    except ValueError as exc:
                        st.error(str(exc))
        elif not can_edit:
            st.caption(
                "Viewing saved reports only. "
                "Owner controls unlock generation; viewing stays public."
            )
        reports = history(profile, device)
        if not reports:
            st.info(
                "No saved reports for this device/profile. Generate one or run the laptop worker."
            )
            return
        by_id = {r["report_id"]: r for r in reports}
        if selected_report in by_id:
            st.session_state.report_history_id = selected_report
        if st.session_state.get("report_history_id") not in by_id:
            st.session_state.report_history_id = reports[0]["report_id"]
        report_id = st.selectbox("Saved report", list(by_id), key="report_history_id",
            format_func=lambda rid: (
                f"{by_id[rid]['kind'].title()} · {by_id[rid]['cycle_count']} cycle(s) · "
                f"{malaysia_time(by_id[rid]['generated_at'])} · {rid[-8:]}"
            ))
        report = by_id[report_id]
        sources = ", ".join(sorted(report["evidence_snapshot"]["source_counts"]))
        st.caption(f"Source: {sources} · Engine: {report['engine']} · Status: {report['status']}")
        st.caption(f"Coverage: {malaysia_time(report['period_start'])} – "
                   f"{malaysia_time(report['period_end'])} MYT")
        st.markdown(
            report["report_text"].replace("# Energy Monitor report", "### Cycle report")
            .replace("## Evidence and qualified totals", "#### Evidence and qualified totals")
            .replace("## Cycle quality notes", "#### Cycle quality notes")
        )
        if report["reporting_version"] == "cycle-report-template-v1":
            st.caption("Cycle quality notes retained with this legacy template:")
            for cycle in report["evidence_snapshot"]["cycles"]:
                st.text(
                    f"Cycle {cycle['cycle_id']} · {cycle['assessment']} (rule-based, not ML)\n"
                    f"{cycle['quality_note']}"
                )
        with st.expander("Inspect saved evidence"):
            st.caption(f"Report {report_id} · Version {report['reporting_version']} · "
                       f"SHA-256 {report['evidence_hash']}")
            st.json(report["evidence_snapshot"])
    except ValueError:
        st.error("Report validation failed. Invalid history was not displayed.")

st.title("Energy Monitor")
st.caption("Live electrical telemetry · automatic load-cycle detection · usage insights")
if use_demo:
    st.caption("Data source: demonstration mode · simulated snapshot · not a live measurement")
elif use_supabase:
    st.caption("Data source: Supabase cloud with local cache")
else:
    st.caption("Data source: local development database")

can_edit = owner_can_edit()
if isinstance(store, SupabaseCycleStore):
    store.can_edit = can_edit

controls = st.columns([2, 2, 1])
with controls[0]:
    st.segmented_control(
        "Dashboard mode",
        ["Live", "Cycles", "Reports"],
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
        view = get_live_view(store, dashboard_now())
        st.markdown(
            render_command_bar(view.connection_state, view.updated_at),
            unsafe_allow_html=True,
        )
        render_live_state(view)

    live_panel()
elif st.session_state.mode == "Cycles":
    sync_cloud_source()
    live_status = get_live_view(store, dashboard_now())
    st.markdown(
        render_command_bar(live_status.connection_state, live_status.updated_at),
        unsafe_allow_html=True,
    )
    manual_cycle_id = render_manual_cycle_entry(store) if can_edit else None
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
                f"Cyan: recorded power · magenta dashed: 1,000 W start threshold · "
                f"green rings: cycle boundaries. {detail.chart_summary}"
            )
        else:
            st.info(detail.chart_summary)
        if can_edit:
            render_cycle_label(store, detail.summary.cycle_id, detail.cycle_label)
        render_tariff_disclosure(
            detail.tariff_estimate,
            detail.energy_label,
        )
else:
    sync_cloud_source()
    render_reports()

st.caption(
    "Unusual behaviour means different from the recorded baseline; "
    "it does not confirm a physical fault."
)
