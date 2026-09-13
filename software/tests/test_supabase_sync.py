from __future__ import annotations

from pathlib import Path

import httpx
from test_models import valid_reading

from energy_monitor.storage import SQLiteTelemetryStore
from energy_monitor.supabase_sync import SupabaseConfig, sync_supabase_telemetry


def cloud_row(sequence: int) -> dict[str, object]:
    row = valid_reading(sample_sequence=sequence, battery_voltage_v=None)
    row["recorded_at"] = row.pop("timestamp")
    return row


def test_supabase_config_prefers_current_server_secret(monkeypatch: object) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co/")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "current-secret")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "legacy-secret")

    assert SupabaseConfig.from_environment() == SupabaseConfig(
        "https://project.supabase.co", "current-secret"
    )


def test_supabase_sync_imports_new_rows_and_uses_local_cache(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        rows = [cloud_row(2), cloud_row(1)] if len(requests) == 1 else []
        return httpx.Response(200, json=rows)

    store = SQLiteTelemetryStore(tmp_path / "cloud-cache.db")
    config = SupabaseConfig("https://project.supabase.co", "server-secret")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert sync_supabase_telemetry(store, config, client=client) == 2
        assert sync_supabase_telemetry(store, config, client=client) == 0

    assert store.count_readings() == 2
    assert requests[0].headers["apikey"] == "server-secret"
    assert "recorded_at=gt." in str(requests[1].url)
    assert requests[1].headers["authorization"] == "Bearer server-secret"
