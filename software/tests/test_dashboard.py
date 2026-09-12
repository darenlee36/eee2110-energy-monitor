from datetime import UTC, datetime
from pathlib import Path

from streamlit.testing.v1 import AppTest

from energy_monitor.models import CycleDetectionSettings
from energy_monitor.simulator import batch_readings, generate_cycle_scenario
from energy_monitor.storage import SQLiteTelemetryStore

DASHBOARD_PATH = Path(__file__).parents[1] / "dashboard" / "app.py"


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
