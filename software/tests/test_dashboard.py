from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from dashboard.components import (
    build_cycle_energy_figure,
    build_cycle_figure,
    build_live_power_figure,
    build_power_quality_figure,
    tariff_disclosure_text,
)
from energy_monitor.dashboard_queries import get_cycle_detail, get_live_view
from energy_monitor.models import CycleDetectionSettings, QualityStatus
from energy_monitor.simulator import batch_readings, generate_cycle_scenario
from energy_monitor.storage import SQLiteTelemetryStore
from energy_monitor.tariffs import estimate_cycle_charge, load_tariff_catalog

DASHBOARD_PATH = Path(__file__).parents[1] / "dashboard" / "app.py"
STYLES_PATH = Path(__file__).parents[1] / "dashboard" / "styles.css"
STREAMLIT_CONFIG_PATH = Path(__file__).parents[2] / ".streamlit" / "config.toml"
CATALOG_PATH = (
    Path(__file__).parents[1] / "config" / "tariffs" / "tnb-domestic-general-rp4.json"
)


@pytest.fixture(autouse=True)
def isolate_dashboard_source(monkeypatch: object) -> None:
    # Developer credentials in .env must never turn unit tests into cloud requests.
    monkeypatch.setenv("ENERGY_MONITOR_SOURCE", "local")


def test_cloud_edit_controls_require_owner_password(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("ENERGY_MONITOR_SOURCE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-server-key")
    monkeypatch.setenv("ENERGY_MONITOR_EDIT_PASSWORD", "test-owner-password")
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(tmp_path / "cloud.db"))
    monkeypatch.setattr(
        "energy_monitor.cloud_edits.SupabaseCycleStore.refresh_edits", lambda s: None
    )
    monkeypatch.setattr("energy_monitor.supabase_sync.sync_supabase_telemetry", lambda *a: 0)
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10)
    app.session_state["mode"] = "Cycles"
    app.run()
    assert not app.exception
    assert not any("Add manual cycle" in e.label for e in app.expander)
    app.text_input[0].set_value("wrong-password")
    next(b for b in app.button if b.label == "Unlock editing").click().run()
    assert app.error[0].value == "Incorrect owner password"
    assert not any("Add manual cycle" in e.label for e in app.expander)
    app.text_input[0].set_value("test-owner-password")
    next(b for b in app.button if b.label == "Unlock editing").click().run()
    assert not app.exception
    assert any("Add manual cycle" in e.label for e in app.expander)
    next(b for b in app.button if b.label == "Lock editing").click().run()
    assert not any("Add manual cycle" in e.label for e in app.expander)


def test_cloud_owner_configuration_missing_fails_closed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ENERGY_MONITOR_SOURCE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-server-key")
    monkeypatch.setenv("ENERGY_MONITOR_EDIT_PASSWORD", "")
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(tmp_path / "unconfigured.db"))
    monkeypatch.setattr(
        "energy_monitor.cloud_edits.SupabaseCycleStore.refresh_edits", lambda s: None
    )
    monkeypatch.setattr("energy_monitor.supabase_sync.sync_supabase_telemetry", lambda *a: 0)
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10)
    app.session_state["mode"] = "Cycles"
    app.run()
    assert not app.exception
    assert not any("Add manual cycle" in e.label for e in app.expander)
    assert any("Viewing only" in c.value for c in app.caption)


def build_dashboard_cycle_database(tmp_path: Path) -> Path:
    database = tmp_path / "dashboard-cycle.db"
    store = SQLiteTelemetryStore(database)
    readings = generate_cycle_scenario(
        "normal", datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
    )
    for batch in batch_readings(readings):
        store.insert_batch(batch)
    store.process_cycles(CycleDetectionSettings.simulation_defaults())
    return database


def test_dashboard_empty_state_has_refresh_controls_and_no_fake_metrics(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(tmp_path / "empty.db"))

    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "Energy Monitor"
    assert any(button.label == "Refresh now" for button in app.button)
    assert "No telemetry" in app.info[0].value
    labels = [metric.label for metric in app.metric]
    assert "Energy in view" not in labels
    assert "Estimated cost" not in labels


def test_demo_source_seeds_simulated_dashboard_data(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setenv("ENERGY_MONITOR_SOURCE", "demo")
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(tmp_path / "demo.db"))

    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10).run()

    assert not app.exception
    assert any("demonstration mode" in caption.value for caption in app.caption)
    assert any(metric.label == "Active power now" for metric in app.metric)
    assert len(app.get("plotly_chart")) == 3
    assert app.session_state["auto_refresh"] is False


def test_completed_cycle_renders_one_cycle_receipt(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    database = build_dashboard_cycle_database(tmp_path)
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(database))

    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10).run()

    assert not app.exception
    labels = [metric.label for metric in app.metric]
    assert "Simulated cycle energy" in labels
    assert "Estimated gross cycle charge" in labels
    assert sum(button.label == "Reset view" for button in app.button) == 3
    assert "Frequency now" in labels
    assert "Power factor now" in labels
    assert all("Development tariff rate" not in item.label for item in app.number_input)

    reset_power = next(
        button
        for button in app.button
        if button.key == "live_power_chart_reset_button"
    )
    reset_power.click().run()

    assert not app.exception
    assert app.session_state["live_power_chart_reset_count"] == 1


def test_dashboard_exposes_live_and_cycles_modes(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(build_dashboard_cycle_database(tmp_path)))

    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10).run()

    assert not app.exception
    assert app.segmented_control[0].options == ["Live", "Cycles", "Reports"]
    assert any(toggle.label == "Auto-refresh every 5 seconds" for toggle in app.toggle)


def test_cycles_mode_defaults_to_latest_completed_cycle(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(build_dashboard_cycle_database(tmp_path)))
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10).run()

    app.segmented_control[0].set_value("Cycles").run()

    assert not app.exception
    assert app.dataframe
    table = app.dataframe[0].value
    assert list(table.columns) == [
        "Start (MYT)",
        "Duration",
        "Energy",
        "Charge",
        "Label",
        "Source",
        "Assessment",
        "Status",
    ]
    assert table.iloc[0]["Start (MYT)"] == "12 Sep, 14:00"
    assert table.iloc[0]["Duration"] == "2m 25s"
    assert table.iloc[0]["Energy"] == "0.0854 kWh"
    assert "Simulated cycle energy" in [metric.label for metric in app.metric]
    assert any(button.label == "Add label" for button in app.button)
    assert any(button.label == "Add manual record" for button in app.button)
    assert any(button.key == "cycle_detail_chart_reset_button" for button in app.button)
    assert any("Times use Malaysia time" in caption.value for caption in app.caption)


def test_dashboard_adds_manual_cycle_without_raw_telemetry(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    database = build_dashboard_cycle_database(tmp_path)
    store = SQLiteTelemetryStore(database)
    store.add_cycle_label("Reference log")
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(database))
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10).run()
    app.segmented_control[0].set_value("Cycles").run()

    next(item for item in app.date_input if item.label == "Cycle date").set_value(
        date(2026, 9, 12)
    ).run()
    next(
        item for item in app.time_input if item.label == "Start time (Malaysia)"
    ).set_value(time(10, 30)).run()
    next(item for item in app.number_input if item.label == "Duration (seconds)").set_value(
        200
    ).run()
    next(item for item in app.number_input if item.label == "Energy (kWh)").set_value(
        0.095
    ).run()
    next(item for item in app.selectbox if item.label == "Record label").set_value(
        "Reference log"
    ).run()
    next(item for item in app.text_area if item.label == "Notes (optional)").set_value(
        "Reference meter"
    ).run()
    next(button for button in app.button if button.label == "Add manual record").click().run()

    manual = next(
        cycle
        for cycle in SQLiteTelemetryStore(database).fetch_cycles()
        if cycle["record_source"] == "manual"
    )
    assert manual["manual_notes"] == "Reference meter"
    assert manual["custom_label"] == "Reference log"
    assert "Entered cycle energy" in [metric.label for metric in app.metric]
    assert any("no raw telemetry" in info.value.lower() for info in app.info)


def test_cycle_figure_uses_elapsed_seconds_threshold_and_quality_markers(
    tmp_path: Path,
) -> None:
    database = build_dashboard_cycle_database(tmp_path)
    store = SQLiteTelemetryStore(database)
    cycle_id = str(store.fetch_cycles()[0]["cycle_id"])
    detail = get_cycle_detail(store, cycle_id, tariff_context=None)
    detail.readings[3].quality_status = QualityStatus.READ_ERROR

    figure = build_cycle_figure(detail, start_threshold_w=1000.0)

    assert figure.layout.xaxis.title.text == "Elapsed (s)"
    assert figure.layout.yaxis.title.text == "Power (W)"
    assert figure.layout.hovermode == "x unified"
    assert figure.layout.showlegend is False
    assert any(trace.name == "Detection threshold" for trace in figure.data)
    assert any(trace.name == "Invalid/missing sample" for trace in figure.data)


def test_live_figures_show_recent_power_threshold_and_measured_cycle_energy(
    tmp_path: Path,
) -> None:
    database = build_dashboard_cycle_database(tmp_path)
    store = SQLiteTelemetryStore(database)
    view = get_live_view(store, now=datetime(2026, 9, 12, 6, 5, tzinfo=UTC))

    power_figure = build_live_power_figure(view, start_threshold_w=1000.0)
    energy_figure = build_cycle_energy_figure(view)

    assert power_figure.layout.yaxis.title.text == "Power (W)"
    assert power_figure.layout.xaxis.title.text == "Malaysia time"
    assert power_figure.layout.title.text == "Live power · last 5 min"
    assert power_figure.layout.showlegend is False
    assert not power_figure.layout.annotations
    assert any(trace.name == "Active power" for trace in power_figure.data)
    assert any(trace.name == "Current reading" for trace in power_figure.data)
    assert any(shape.type == "line" for shape in power_figure.layout.shapes)
    assert any(shape.type == "rect" for shape in power_figure.layout.shapes)
    assert energy_figure is not None
    assert energy_figure.layout.yaxis.title.text == "Energy (kWh)"
    assert energy_figure.layout.xaxis.title.text == "Elapsed (s)"
    assert energy_figure.data[0].name == "Cycle energy"
    assert view.last_cycle is not None
    assert energy_figure.data[0].y[-1] == pytest.approx(view.last_cycle.energy_kwh)

    quality_figure = build_power_quality_figure(view)
    assert [trace.name for trace in quality_figure.data] == [
        "Frequency",
        "Power factor",
    ]
    assert quality_figure.layout.yaxis.title.text == "Frequency (Hz)"
    assert quality_figure.layout.yaxis2.title.text == "Power factor"
    assert list(quality_figure.layout.yaxis2.range) == [0, 1.05]
    assert quality_figure.layout.showlegend is False
    assert not quality_figure.layout.annotations


def test_tariff_disclosure_names_provenance_and_exclusions() -> None:
    estimate = estimate_cycle_charge(
        Decimal("0.087"),
        datetime(2026, 9, 12, tzinfo=UTC),
        Decimal("800"),
        load_tariff_catalog(CATALOG_PATH),
    )

    text = tariff_disclosure_text(estimate)

    assert "TNB Domestic General" in text
    assert "September 2026 AFA" in text
    assert "not a complete household bill" in text
    assert "energy-efficiency incentive" in text


def test_user_created_cycle_label_is_saved_and_reusable(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    database = build_dashboard_cycle_database(tmp_path)
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(database))
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10).run()
    app.segmented_control[0].set_value("Cycles").run()

    next(item for item in app.text_input if item.label == "New label").set_value(
        "Morning mug"
    ).run()
    next(button for button in app.button if button.label == "Add label").click().run()
    next(item for item in app.selectbox if item.label == "Saved labels").set_value(
        "Morning mug"
    ).run()
    next(button for button in app.button if button.label == "Save to cycle").click().run()

    store = SQLiteTelemetryStore(database)
    cycle_id = str(store.fetch_cycles()[0]["cycle_id"])
    assert store.fetch_cycle_label(cycle_id) == "Morning mug"
    assert app.success[-1].value == "Cycle label saved"


def test_dashboard_can_remove_reusable_label_without_erasing_cycle_history(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    database = build_dashboard_cycle_database(tmp_path)
    store = SQLiteTelemetryStore(database)
    cycle_id = str(store.fetch_cycles()[0]["cycle_id"])
    store.add_cycle_label("Morning mug")
    store.save_cycle_label(cycle_id, "Morning mug")
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(database))
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10).run()
    app.segmented_control[0].set_value("Cycles").run()

    next(button for button in app.button if button.label == "Remove label").click().run()

    updated = SQLiteTelemetryStore(database)
    assert not app.exception
    assert updated.list_cycle_labels() == []
    assert updated.fetch_cycle_label(cycle_id) == "Morning mug"


def test_neon_theme_is_local_responsive_and_motion_safe() -> None:
    css = STYLES_PATH.read_text(encoding="utf-8")
    config = STREAMLIT_CONFIG_PATH.read_text(encoding="utf-8")

    assert "--cyan: #3bd7ff" in css
    assert ".state-heating" in css
    assert ":focus-visible" in css
    assert "prefers-reduced-motion: reduce" in css
    assert "--font-technical:" in css
    assert '"Cascadia Code"' in css
    assert "url(" not in css.lower()
    assert 'base = "dark"' in config
    assert 'primaryColor = "#3BD7FF"' in config


def test_reports_empty_history_and_no_generation_on_refresh(tmp_path, monkeypatch):
    database = build_dashboard_cycle_database(tmp_path)
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(database))
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10)
    app.session_state["mode"] = "Reports"
    app.run()
    assert not app.exception
    assert any("No saved reports" in i.value for i in app.info)
    assert next(s for s in app.selectbox if s.label == "Batch size (cycles)").options == [
        "5", "10", "20"
    ]
    next(b for b in app.button if b.label == "Refresh now").click().run()
    assert SQLiteTelemetryStore(database).fetch_reports() == []
    next(b for b in app.button if b.label == "Generate batch report").click().run()
    assert any("1 of 5" in e.value for e in app.error)
    assert SQLiteTelemetryStore(database).fetch_reports() == []


def test_reports_owner_generates_and_displays_saved_evidence(tmp_path, monkeypatch):
    database = build_dashboard_cycle_database(tmp_path)
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(database))
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10)
    app.session_state["mode"] = "Reports"
    app.run()
    next(b for b in app.button if b.label == "Generate cycle report").click().run()
    assert not app.exception
    assert len(SQLiteTelemetryStore(database).fetch_reports()) == 1
    assert any("Source: simulated" in c.value for c in app.caption)
    assert any("Engine: template" in c.value for c in app.caption)
    assert any("not trained" in m.value for m in app.markdown)
    assert app.json
    next(b for b in app.button if b.label == "Generate cycle report").click().run()
    assert len(SQLiteTelemetryStore(database).fetch_reports()) == 1


def test_reports_public_cloud_reads_cache_without_generation(tmp_path, monkeypatch):
    from energy_monitor.cloud_edits import SupabaseCycleStore
    from energy_monitor.reporting import build_report
    from energy_monitor.supabase_sync import SupabaseSyncError

    database = build_dashboard_cycle_database(tmp_path)
    store = SQLiteTelemetryStore(database)
    cycle = store.fetch_cycles()[0]
    store.save_report(build_report(store, profile_id=cycle["profile_id"],
        device_id=cycle["device_id"], cycle_id=cycle["cycle_id"]))
    # Saved reports must remain viewable without their analytical cycle cache.
    with store._connect() as connection, connection:
        connection.execute("DELETE FROM appliance_cycles")
    monkeypatch.setenv("ENERGY_MONITOR_SOURCE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-server-key")
    monkeypatch.setenv("ENERGY_MONITOR_EDIT_PASSWORD", "")
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(database))
    monkeypatch.setattr(SupabaseCycleStore, "refresh_edits", lambda s: None)
    monkeypatch.setattr("energy_monitor.supabase_sync.sync_supabase_telemetry", lambda *a: 0)
    def unavailable(*args, **kwargs):
        raise SupabaseSyncError("Cloud unavailable")
    monkeypatch.setattr(SupabaseCycleStore, "fetch_reports", unavailable)
    monkeypatch.setattr(SupabaseCycleStore, "save_report",
                        lambda *a: pytest.fail("Public report generation"))
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10)
    app.session_state["mode"] = "Reports"
    app.run()
    assert not app.exception
    assert any("cached reports" in w.value for w in app.warning)
    assert any("Engine: template" in c.value for c in app.caption)
    assert not any(b.label.startswith("Generate") for b in app.button)
    assert app.json
