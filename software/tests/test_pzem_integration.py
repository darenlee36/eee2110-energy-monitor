"""R1A wire fixtures only: no hardware, cloud writes, training or AI calls."""

import json
import shutil
import subprocess
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from streamlit.testing.v1 import AppTest
from test_dashboard import DASHBOARD_PATH

from energy_monitor.dashboard_queries import get_live_view
from energy_monitor.models import TelemetryBatch
from energy_monitor.storage import SQLiteTelemetryStore
from energy_monitor.supabase_sync import SupabaseConfig, sync_supabase_telemetry

FIXTURE_PATH = Path(__file__).parent / "fixtures/r1a-pzem-wire.json"


def fixture_batch():
    envelope = json.loads(FIXTURE_PATH.read_text())
    assert envelope["fixture_kind"] == "synthetic_native_pzem_api_double"
    return TelemetryBatch.model_validate(envelope["batch"])


@pytest.mark.skipif(not shutil.which("node"), reason="Node required for offline Edge predicate")
def test_native_fixture_satisfies_actual_edge_validation():
    edge = Path(__file__).parents[1] / "supabase/functions/ingest-telemetry/index.ts"
    # Execute the production reading predicate only. No Deno handler, imports or network.
    code = r"""
    const fs = require('node:fs');
    const {stripTypeScriptTypes} = require('node:module');
    let source = fs.readFileSync(process.argv[1], 'utf8');
    source = source.slice(
      source.indexOf('const allowedStates'), source.indexOf('async function sha256'));
    const isReading = new Function(stripTypeScriptTypes(source) + ';return isReading;')();
    const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
    const r = fixture.batch.readings[0];
    if (!isReading(r) || !isReading({...r, quality_status:'communication_gap'})) process.exit(1);
    for (const field of ['voltage_v','current_a','active_power_w','cumulative_energy_kwh',
                         'frequency_hz','power_factor']) {
      if (isReading({...r, [field]:NaN}) || isReading({...r, [field]:null})) process.exit(2);
    }
    console.log('offline production Edge predicate: PASS');
    """
    result = subprocess.run(
        [shutil.which("node"), "-e", code, str(edge), str(FIXTURE_PATH)],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout


def test_fixture_cloud_cache_replay_and_saved_streamlit_display(tmp_path, monkeypatch):
    batch = fixture_batch()
    r = batch.readings[0]
    assert r.data_origin == "sensor"  # Mode classification only; fixture remains synthetic.
    row = r.model_dump(mode="json")
    row["recorded_at"] = row.pop("timestamp")
    row.update(id=str(UUID(int=1)), received_at="2026-11-03T00:00:01+00:00")
    pages = [[row], [], []]
    store = SQLiteTelemetryStore(tmp_path / "fixture-only.db")
    config = SupabaseConfig("https://fixture.invalid", "dummy-not-a-credential")
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=pages.pop(0))
    )) as client:
        assert sync_supabase_telemetry(store, config, client=client) == 1
        assert sync_supabase_telemetry(store, config, client=client) == 0
    assert store.count_readings() == 1
    view = get_live_view(SQLiteTelemetryStore(store.database_path), r.timestamp)
    assert view.latest_voltage_v == 239.4
    assert view.latest_current_a == 8.5
    assert view.latest_power_w == 2030
    cached = view.recent_readings[0]
    assert cached.cumulative_energy_kwh == 12.345
    assert cached.frequency_hz == 50 and cached.power_factor == 0.997
    assert cached.battery_voltage_v is None
    monkeypatch.setenv("ENERGY_MONITOR_SOURCE", "local")
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(store.database_path))
    monkeypatch.setattr(
        "energy_monitor.report_narrative.run_codex", lambda _: pytest.fail("AI forbidden")
    )
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=15).run()
    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Voltage now"] == "239.4 V"
    assert metrics["Active power now"] == "2,030 W"
