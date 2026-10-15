from datetime import UTC, datetime, timedelta

import httpx
import pytest

from energy_monitor.cloud_edits import SupabaseCycleStore
from energy_monitor.models import ManualCycleRecord
from energy_monitor.supabase_sync import SupabaseConfig

CONFIG = SupabaseConfig("https://project.supabase.co", "test-server-key")


def test_public_store_cannot_write(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("Unexpected cloud write"))
    store = SupabaseCycleStore(tmp_path / "public.db", CONFIG)
    with pytest.raises(ValueError, match="Owner login required"):
        store.add_cycle_label("1 litre")
    assert store.list_cycle_labels() == []


def test_manual_record_and_archived_label_survive_fresh_cache(tmp_path, monkeypatch):
    state = {"labels": [], "manual_cycles": [], "assignments": []}

    def post(url, *, headers, json, timeout):
        assert headers["apikey"] == "test-server-key"
        if url.endswith("dashboard_edit_state"):
            result = state
        else:
            operation, payload = json["operation"], json["payload"]
            label = payload["label"]
            if operation == "manual_cycle":
                summary = payload["summary"]
                state["labels"] = [{"label": label, "is_active": True}]
                state["manual_cycles"] = [{"summary": summary, "notes": payload["notes"]}]
                state["assignments"] = [{"cycle_id": summary["cycle_id"], "label": label}]
                result = summary["cycle_id"]
            else:
                assert operation == "archive_label"
                state["labels"][0]["is_active"] = False
                result = label
        return httpx.Response(200, json=result, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    first = SupabaseCycleStore(tmp_path / "first.db", CONFIG, can_edit=True)
    record = ManualCycleRecord(
        started_at=datetime.now(UTC) - timedelta(hours=1),
        duration_seconds=180, energy_kwh=0.08, label="1 litre", notes="Reference reading",
    )
    cycle_id = first.add_manual_cycle(record)
    assert first.add_manual_cycle(record) == cycle_id
    first.archive_cycle_label("1 litre")
    restarted = SupabaseCycleStore(tmp_path / "restarted.db", CONFIG)
    restarted.refresh_edits()
    restarted.refresh_edits()
    assert restarted.list_cycle_labels() == []
    assert len(restarted.fetch_cycles()) == 1
    cycle = restarted.fetch_cycle(cycle_id)
    assert cycle["record_source"] == "manual"
    assert cycle["custom_label"] == "1 litre"
    assert cycle["manual_notes"] == "Reference reading"
    assert len(restarted.fetch_cycle_label_history(cycle_id)) == 1


def test_failed_cloud_write_does_not_create_local_success(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: httpx.Response(
        503, request=httpx.Request("POST", url)
    ))
    store = SupabaseCycleStore(tmp_path / "cache.db", CONFIG, can_edit=True)
    with pytest.raises(ValueError, match="No edit was confirmed"):
        store.add_cycle_label("1 litre")
    assert store.list_cycle_labels() == []
