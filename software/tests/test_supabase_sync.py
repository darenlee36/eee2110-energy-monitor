from __future__ import annotations

from pathlib import Path
from uuid import UUID

import httpx
import pytest
from test_models import valid_reading

from energy_monitor.storage import SQLiteTelemetryStore
from energy_monitor.supabase_sync import SupabaseConfig, SupabaseSyncError, sync_supabase_telemetry


def cloud_row(sequence: int) -> dict[str, object]:
    row = valid_reading(sample_sequence=sequence, battery_voltage_v=None)
    row["recorded_at"] = row.pop("timestamp")
    row["id"] = str(UUID(int=sequence))
    row["received_at"] = "2026-10-15T00:00:00+00:00"
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
        rows = [cloud_row(1), cloud_row(2)] if len(requests) == 1 else []
        return httpx.Response(200, json=rows)

    store = SQLiteTelemetryStore(tmp_path / "cloud-cache.db")
    config = SupabaseConfig("https://project.supabase.co", "server-secret")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert sync_supabase_telemetry(store, config, client=client) == 2
        assert sync_supabase_telemetry(store, config, client=client) == 0

    assert store.count_readings() == 2
    assert requests[0].headers["apikey"] == "server-secret"
    assert "recorded_at" not in requests[1].url.params
    assert "id.gt." in requests[1].url.params["or"]
    assert requests[1].headers["authorization"] == "Bearer server-secret"


def test_pages_keep_equal_timestamps_delayed_samples_and_multiple_devices(tmp_path: Path) -> None:
    path = tmp_path / "cache.db"
    config = SupabaseConfig("https://project.supabase.co", "server-secret")
    pages = [[cloud_row(1), cloud_row(2)], [cloud_row(3)], []]
    pages[0][1]["device_id"] = "second-device"
    pages[1][0]["recorded_at"] = "2026-09-01T00:00:00Z"
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=pages.pop(0))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for expected in (2, 1, 0):
            assert sync_supabase_telemetry(
                SQLiteTelemetryStore(path), config, limit=2, client=client
            ) == expected
    assert SQLiteTelemetryStore(path).count_readings() == 3
    assert requests[0].url.params["order"] == "received_at.asc,id.asc"
    assert str(UUID(int=2)) in requests[1].url.params["or"]
    assert str(UUID(int=3)) in requests[2].url.params["or"]


def test_invalid_page_does_not_advance_cursor_or_partially_import(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "cache.db")
    config = SupabaseConfig("https://project.supabase.co", "server-secret")
    bad = cloud_row(2)
    bad["voltage_v"] = 999
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=[cloud_row(1), bad])
    )) as client:
        with pytest.raises(SupabaseSyncError, match="invalid telemetry"):
            sync_supabase_telemetry(store, config, client=client)
    assert store.cloud_sync_cursor(config.url) is None
    assert store.count_readings() == 0


def test_network_failure_preserves_sync_position(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "cache.db")
    config = SupabaseConfig("https://project.supabase.co", "server-secret")
    store.save_cloud_sync_cursor(config.url, "2026-10-15T00:00:00+00:00", str(UUID(int=1)))
    before = store.cloud_sync_cursor(config.url)
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(503)
    )) as client:
        with pytest.raises(SupabaseSyncError, match="sync failed"):
            sync_supabase_telemetry(store, config, client=client)
    assert store.cloud_sync_cursor(config.url) == before
