from pathlib import Path

from streamlit.testing.v1 import AppTest
from test_models import valid_reading

from energy_monitor.models import TelemetryBatch
from energy_monitor.storage import SQLiteTelemetryStore

DASHBOARD_PATH = Path(__file__).parents[1] / "dashboard" / "app.py"


def test_dashboard_has_clear_empty_state(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(tmp_path / "empty.db"))

    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "Appliance energy monitor"
    assert "No telemetry" in app.info[0].value


def test_dashboard_renders_latest_metrics_from_storage(tmp_path: Path, monkeypatch: object) -> None:
    database = tmp_path / "telemetry.db"
    store = SQLiteTelemetryStore(database)
    store.insert_batch(
        TelemetryBatch.model_validate(
            {
                "batch_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "readings": [valid_reading()],
            }
        )
    )
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(database))

    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10).run()

    assert not app.exception
    labels = [metric.label for metric in app.metric]
    assert labels[:3] == ["Active power", "Voltage", "Current"]
    assert app.metric[0].value == "2,058 W"
