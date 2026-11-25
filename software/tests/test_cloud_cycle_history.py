from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import httpx
from test_dashboard_queries import populated_store

from energy_monitor.cloud_edits import SupabaseCycleStore
from energy_monitor.dashboard_queries import list_cycle_items
from energy_monitor.models import CycleDetectionSettings, CycleSummary
from energy_monitor.supabase_sync import SupabaseConfig


def test_cold_cache_uses_saved_sensor_cycles_and_labels(tmp_path, monkeypatch):
    source = populated_store(tmp_path / "source")
    fixture = CycleSummary.model_validate({
        name: source.fetch_cycles()[0][name] for name in CycleSummary.model_fields
    })
    rows, assignments = [], []
    for i in range(39):
        start = datetime(2026, 11, 5, 5, tzinfo=UTC) + timedelta(hours=i)
        cycle_id = str(UUID(int=i + 1))
        summary = fixture.model_copy(update={
            "cycle_id": UUID(cycle_id), "started_at": start,
            "ended_at": start + timedelta(seconds=fixture.duration_seconds),
            "start_sequence": i * 1000, "end_sequence": i * 1000 + 40,
            "data_origin": "sensor",
        }).model_dump(mode="json")
        rows.append({"cycle_id": cycle_id, "summary": summary})
        assignments.append({"cycle_id": cycle_id,
                            "label": "Incomplete" if i in (4, 15, 22) else "Complete"})
    state = {"manual_cycles": [], "labels": [{"label": name, "is_active": True}
             for name in ("Complete", "Incomplete")], "assignments": assignments}

    def get(url, *, headers, params, timeout):
        assert url.endswith("/rest/v1/appliance_cycles")
        return httpx.Response(200, json=rows, request=httpx.Request("GET", url))

    store = SupabaseCycleStore(tmp_path / "fresh.db",
                               SupabaseConfig("https://project.supabase.co", "test"))
    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(store, "_rpc", lambda *args: state)
    store.refresh_cycle_history()
    store.refresh_edits()
    assert store.count_readings() == 0
    items = list_cycle_items(store, source_filter="sensor")
    assert [i.cycle_number for i in reversed(items) if i.cycle_number] == list(range(1, 37))
    assert len(items) == 39
    incomplete = list_cycle_items(store, source_filter="sensor", status_filter="incomplete")
    assert len(incomplete) == 3
    assert all(i.cycle_number is None and i.cycle_label == "Incomplete" for i in incomplete)
    filtered = list_cycle_items(store, date_filter="Custom date range",
                               date_start=date(2026, 11, 6), date_end=date(2026, 11, 6))
    assert len(filtered) < len(items) and filtered[0].cycle_number > 1
    assert store.fetch_cycle(rows[-1]["cycle_id"])["custom_label"] == "Complete"


def test_appliance_rule_can_arrive_after_confirmed_summary(tmp_path):
    row = populated_store(tmp_path / "source").fetch_cycles()[0]
    summary = CycleSummary.model_validate({name: row[name] for name in CycleSummary.model_fields})
    version = "appliance-" + "a" * 32
    store = SupabaseCycleStore(tmp_path / "fresh.db",
                               SupabaseConfig("https://project.supabase.co", "test"))
    store.restore_confirmed_sensor_cycles([summary.model_copy(update={
        "data_origin": "sensor", "detection_version": version,
    })])
    settings = CycleDetectionSettings.simulation_defaults().model_copy(update={"version": version})
    rules = [{"version": version, "device_id": summary.device_id,
              "profile_id": summary.profile_id, "appliance_name": "Kettle",
              "effective_at": summary.started_at.isoformat(), "settings": settings.model_dump()}]
    store.restore_appliance_rules(rules)
    store.restore_appliance_rules(rules)
    assert store.cycle_rule(version) == ("Kettle", settings)
