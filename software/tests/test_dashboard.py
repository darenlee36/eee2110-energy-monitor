from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from streamlit.testing.v1 import AppTest

from dashboard.components import build_cycle_figure, tariff_disclosure_text
from energy_monitor.dashboard_queries import get_cycle_detail
from energy_monitor.models import CycleDetectionSettings, QualityStatus
from energy_monitor.simulator import batch_readings, generate_cycle_scenario
from energy_monitor.storage import SQLiteTelemetryStore
from energy_monitor.tariffs import estimate_cycle_charge, load_tariff_catalog

DASHBOARD_PATH = Path(__file__).parents[1] / "dashboard" / "app.py"
CATALOG_PATH = (
    Path(__file__).parents[1] / "config" / "tariffs" / "tnb-domestic-general-rp4.json"
)


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
    assert app.title[0].value == "Kettle cycle monitor"
    assert any(button.label == "Refresh now" for button in app.button)
    assert "No telemetry" in app.info[0].value
    labels = [metric.label for metric in app.metric]
    assert "Energy in view" not in labels
    assert "Estimated cost" not in labels


def test_completed_cycle_renders_one_cycle_receipt(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    database = build_dashboard_cycle_database(tmp_path)
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(database))

    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10).run()

    assert not app.exception
    labels = [metric.label for metric in app.metric]
    assert "Measured cycle energy" in labels
    assert "Estimated gross cycle charge" in labels
    assert all("Development tariff rate" not in item.label for item in app.number_input)


def test_dashboard_exposes_live_and_cycles_modes(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(build_dashboard_cycle_database(tmp_path)))

    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10).run()

    assert not app.exception
    assert app.segmented_control[0].options == ["Live", "Cycles"]
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
    assert "Measured cycle energy" in [metric.label for metric in app.metric]
    assert any(button.label == "Save label" for button in app.button)


def test_cycle_figure_uses_elapsed_seconds_threshold_and_quality_markers(
    tmp_path: Path,
) -> None:
    database = build_dashboard_cycle_database(tmp_path)
    store = SQLiteTelemetryStore(database)
    cycle_id = str(store.fetch_cycles()[0]["cycle_id"])
    detail = get_cycle_detail(store, cycle_id, tariff_context=None)
    detail.readings[3].quality_status = QualityStatus.READ_ERROR

    figure = build_cycle_figure(detail, start_threshold_w=1000.0)

    assert figure.layout.xaxis.title.text == "Elapsed time (s)"
    assert figure.layout.yaxis.title.text == "Active power (W)"
    assert figure.layout.hovermode == "x unified"
    assert any(trace.name == "Detection threshold" for trace in figure.data)
    assert any(trace.name == "Invalid/missing sample" for trace in figure.data)


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


def test_volume_label_save_from_cycles_mode_is_persisted(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    database = build_dashboard_cycle_database(tmp_path)
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(database))
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10).run()
    app.segmented_control[0].set_value("Cycles").run()

    app.pills[-1].set_value("1.0 L").run()
    next(button for button in app.button if button.label == "Save label").click().run()

    store = SQLiteTelemetryStore(database)
    cycle_id = str(store.fetch_cycles()[0]["cycle_id"])
    assert store.fetch_effective_volume(cycle_id).value == "1.0_l"
    assert app.success[0].value == "Volume label saved"
