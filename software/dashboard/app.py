from __future__ import annotations

import hmac
import os
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

import streamlit as st
from components import (
    build_cycle_figure,
    build_electrical_figure,
    format_myt,
    render_anomaly_assessment,
    render_command_bar,
    render_cycle_annotation,
    render_cycle_assessment,
    render_cycle_filters,
    render_cycle_history,
    render_cycle_receipt,
    render_cycle_totals,
    render_live_state,
    render_manual_cycle_entry,
    render_report_dashboard,
    render_resettable_chart,
    render_tariff_disclosure,
)
from dotenv import load_dotenv

from energy_monitor.cloud_edits import SupabaseCycleStore
from energy_monitor.dashboard_queries import (
    complete_sensor_cycle_numbers,
    get_cycle_detail,
    get_live_view,
    list_cycle_items,
    matches_source,
)
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


def render_owner_cycle_settings() -> None:
    password = os.environ.get("ENERGY_MONITOR_EDIT_PASSWORD", "")
    if not (source_mode == "local" or isinstance(store, SupabaseCycleStore)):
        return
    with st.expander("Owner cycle settings"):
        if not password:
            st.caption("An owner password must be configured to change shared cycle thresholds.")
            return
        supplied = st.text_input("Owner password", type="password")
        if not hmac.compare_digest(supplied.encode(), password.encode()):
            return
        if isinstance(store, SupabaseCycleStore):
            store.can_edit = True
            try:
                store.refresh_appliance_rules()
            except SupabaseSyncError:
                st.warning("Owner rules are unavailable until the cloud registry is reachable.")
                return
        streams = store.telemetry_scopes()
        if not streams:
            st.info("Record at least one reading before choosing a monitor.")
            return
        device, profile = st.selectbox("Telemetry stream", streams,
                                       format_func=lambda pair: " / ".join(pair))
        name, settings = store.active_appliance_rule(device, profile, datetime.now(UTC))
        st.caption(f"Current rules: {name} · {settings.version}")
        st.caption("Select the appliance connected to this meter. Continuous loads may never "
                   "produce a completed on/off cycle.")
        choices = ["Electric kettle", "Rice cooker", "Fan", "Phone charger", "Custom"]
        with st.form(f"cycle-rule-{device}-{profile}-{settings.version}"):
            appliance = st.selectbox("Appliance", choices,
                                     index=choices.index(name) if name in choices else 4)
            custom_name = st.text_input("Custom appliance name", value=name, max_chars=64)
            start = st.number_input("Start threshold (W)", min_value=0.1, max_value=25_000.0,
                                    value=settings.start_power_w)
            stop = st.number_input("Stop threshold (W)", min_value=0.1, max_value=25_000.0,
                                   value=settings.stop_power_w)
            start_count = st.number_input("Start confirmation samples", min_value=1,
                                          value=settings.start_confirm_samples)
            stop_count = st.number_input("Stop confirmation samples", min_value=1,
                                         value=settings.stop_confirm_samples)
            st.caption("Start requires readings at or above its threshold. Stop requires readings "
                       "below its threshold. Saved changes apply to future cycles.")
            save = st.form_submit_button("Save rules", type="primary")
            restore = st.form_submit_button("Restore kettle defaults")
        if save or restore:
            proposed = CycleDetectionSettings.simulation_defaults() if restore else (
                settings.model_copy(update={
                    "start_power_w": start, "stop_power_w": stop,
                    "start_confirm_samples": start_count, "stop_confirm_samples": stop_count,
                })
            )
            selected_name = "Electric kettle" if restore else (
                custom_name if appliance == "Custom" else appliance
            )
            try:
                revision = store.save_appliance_rule(device, profile, selected_name, proposed)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success(f"{selected_name} rules saved for future cycles · {revision}")


def render_reports() -> None:
    st.header("Reports")

    def history(profile=None, device=None):
        try:
            return store.fetch_reports(profile, device)
        except SupabaseSyncError:
            st.warning(
                "Cloud history unavailable. Showing cached reports; history may be incomplete."
            )
            return SQLiteTelemetryStore.fetch_reports(store, profile, device)

    def report_matches_source(report):
        return all(matches_source(c["data_origin"], c["record_source"], source_filter)
                   for c in report["evidence_snapshot"]["cycles"])

    try:
        recent = [report for report in history() if report_matches_source(report)]
        all_cycles = store.fetch_cycles(limit=None)
        cycle_names = {
            cycle_id: f"Cycle {number}"
            for cycle_id, number in complete_sensor_cycle_numbers(all_cycles).items()
        }
        completed = [c for c in all_cycles
                     if c["status"] == "completed" and matches_source(
                         c.get("data_origin", "unknown"), c.get("record_source", "measured"),
                         source_filter)]
        pairs = sorted({(r["profile_id"], r["device_id"]) for r in [*recent, *completed]})
        if not pairs:
            st.info("No saved reports or completed cycles yet.")
            return
        with st.expander("Monitor / appliance"):
            profile, device = st.selectbox(
                "Report device / profile", pairs, format_func=lambda p: f"{p[1]} / {p[0]}"
            )
        cycles = [c for c in store.fetch_report_cycles(profile, device)
                  if matches_source(c.get("data_origin", "unknown"),
                                    c.get("record_source", "measured"), source_filter)]
        selected_report = None
        if can_edit and cycles:
            with st.expander("Generate template report", expanded=True):
                cycle_by_id = {c["cycle_id"]: c for c in cycles}
                cycle_id = st.selectbox(
                    "Completed cycle", list(cycle_by_id), format_func=lambda cid:
                    f"{format_myt(cycle_by_id[cid]['started_at'])}")
                count = st.selectbox("Batch size (cycles)", [5, 10, 20])
                individual = st.button("Generate cycle report")
                batch = st.button("Generate batch report")
                if individual or batch:
                    try:
                        if batch and any(not matches_source(
                            c.get("data_origin", "unknown"), c.get("record_source", "measured"),
                            source_filter) for c in
                                store.fetch_report_cycles(profile, device)[:count]):
                            raise ValueError(
                                "The latest batch includes other data sources. "
                                "Choose All records to generate a mixed-source comparison, "
                                "or generate an individual report."
                            )
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
        scope_reports = history(profile, device)
        reports = [report for report in scope_reports if report_matches_source(report)]
        if not reports:
            st.info(
                "No saved reports for this device/profile. Open Cycles for its assessment."
            )
            return
        reported_cycle_ids = {
            str(cycle["cycle_id"]) for report in scope_reports
            for cycle in report["evidence_snapshot"]["cycles"]
        }
        unnumbered_ids = (
            str(cycle["cycle_id"]) for cycle in reversed(all_cycles)
            if str(cycle["cycle_id"]) in reported_cycle_ids
            and str(cycle["cycle_id"]) not in cycle_names
        )
        cycle_names.update({
            cycle_id: f"Incomplete cycle {number}"
            for number, cycle_id in enumerate(unnumbered_ids, start=1)
        })
        by_id = {r["report_id"]: r for r in reports}
        report_labels = {}
        for rid, saved in by_id.items():
            saved_cycles = saved["evidence_snapshot"]["cycles"]
            for cycle in saved_cycles:
                cycle_id = str(cycle["cycle_id"])
                if cycle_id not in cycle_names:
                    cycle_names[cycle_id] = f"Archived cycle · {format_myt(cycle['started_at'])}"
            if saved["kind"] == "cycle":
                report_labels[rid] = f"{cycle_names[str(saved_cycles[0]['cycle_id'])]} report"
            else:
                report_labels[rid] = (
                    f"{saved['cycle_count']}-cycle comparison report · "
                    f"{format_myt(saved['generated_at'])}"
                )
        occurrences = Counter(report_labels.values())
        for rid, label in report_labels.items():
            if occurrences[label] > 1:
                saved = by_id[rid]
                report_labels[rid] = (
                    f"{label} · {saved['engine'].title()} · {format_myt(saved['generated_at'])}"
                )
        if selected_report in by_id:
            st.session_state.report_history_id = selected_report
        if st.session_state.get("report_history_id") not in by_id:
            st.session_state.report_history_id = reports[0]["report_id"]
        report_id = st.selectbox("Saved report", list(by_id), key="report_history_id",
                                 format_func=report_labels.__getitem__)
        report = by_id[report_id]
        sources = ", ".join(sorted(report["evidence_snapshot"]["source_counts"]))
        st.caption(f"Coverage: {format_myt(report['period_start'])} – "
                   f"{format_myt(report['period_end'])}")
        render_report_dashboard(report, store, cycle_names=cycle_names)
        with st.expander("Technical details and saved evidence"):
            st.markdown("**Model evidence at report generation**")
            render_anomaly_assessment(report)
            st.caption(
                f"Source: {sources} · Engine: {report['engine']} · Status: {report['status']}"
            )
            st.caption(f"Report {report_id} · Version {report['reporting_version']} · "
                       f"SHA-256 {report['evidence_hash']}")
            st.text(report["report_text"])
            st.caption("Raw saved evidence retains UTC API timestamps for audit.")
            st.json(report["evidence_snapshot"])
    except ValueError:
        st.error("Report validation failed. Invalid history was not displayed.")

st.title("Energy Monitor")
render_owner_cycle_settings()
source_options = {
    "Sensor-mode records": "sensor", "Simulated test records": "simulated",
    "Manual entries": "manual", "Unverified / mixed": "unverified", "All records": "all",
}
source_filter = source_options[st.selectbox(
    "Data source filter", list(source_options), index=0 if use_supabase else 4,
    key="data_source_filter",
)]
# Local report/manual tools stay available for offline development.
can_edit = source_mode in ("local", "demo")

controls = st.columns([2, 2, 1])
with controls[0]:
    st.segmented_control(
        "Dashboard mode",
        ["Live", "Cycles", "Reports"],
        key="mode",
        label_visibility="collapsed",
    )
with controls[1]:
    st.toggle("Auto-refresh every 3 seconds", key="auto_refresh")
with controls[2]:
    if st.button("Refresh now", type="primary", width="stretch"):
        st.rerun()


if st.session_state.mode == "Live":

    @st.fragment(run_every="3s" if st.session_state.auto_refresh else None)
    def live_panel() -> None:
        sync_cloud_source()
        view = get_live_view(store, dashboard_now(), source_filter=source_filter)
        render_live_state(view, store)

    live_panel()
elif st.session_state.mode == "Cycles":
    sync_cloud_source()
    live_status = get_live_view(store, dashboard_now(), source_filter=source_filter)
    st.markdown(
        render_command_bar(live_status.connection_state, live_status.updated_at),
        unsafe_allow_html=True,
    )
    manual_cycle_id = render_manual_cycle_entry(store) if can_edit else None
    if manual_cycle_id is not None:
        st.session_state.selected_cycle_id = str(manual_cycle_id)
    status_filter, label_filter, date_time_options = render_cycle_filters(store, dashboard_now())
    label_value = {
        "All labels": "all",
        "Unlabelled": "__unlabelled__",
    }.get(label_filter, label_filter)
    try:
        items = list_cycle_items(
            store, status_filter=status_filter.lower(), label_filter=label_value,
            source_filter=source_filter, **date_time_options,
        )
    except ValueError as exc:
        st.warning(str(exc))
        items = []
    if st.session_state.selected_cycle_id not in {str(item.cycle_id) for item in items}:
        st.session_state.selected_cycle_id = None
    selected = render_cycle_history(items)
    render_cycle_totals(items)
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
        rule_name, rule = store.cycle_rule(detail.summary.detection_version)
        if detail.readings:
            st.caption(f"Cycle rules: {rule_name} · {detail.summary.detection_version}")
            render_resettable_chart(
                build_cycle_figure(detail, start_threshold_w=rule.start_power_w),
                chart_key="cycle_detail_chart",
            )
            for metric in ("Voltage", "Current"):
                render_resettable_chart(
                    build_electrical_figure(detail.readings, metric, elapsed=True),
                    chart_key=f"cycle_{metric.lower()}_{detail.summary.cycle_id}",
                )
        else:
            st.info(detail.chart_summary)
        if detail.summary.status is CycleStatus.COMPLETED and (
            can_edit or isinstance(store, SupabaseCycleStore)
        ):
            render_cycle_annotation(store, detail.summary.cycle_id)
        if detail.summary.data_origin == "sensor":
            selected_cycle = store.fetch_cycle(detail.summary.cycle_id)
            if selected_cycle is not None:
                render_cycle_assessment(selected_cycle, store)
        render_tariff_disclosure(
            detail.tariff_estimate,
            detail.energy_label,
        )
else:
    sync_cloud_source()
    render_reports()
