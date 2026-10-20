from datetime import UTC, datetime, timedelta

import httpx
import pytest

from energy_monitor.cloud_edits import SupabaseCycleStore
from energy_monitor.models import ManualCycleRecord
from energy_monitor.storage import SQLiteTelemetryStore
from energy_monitor.supabase_sync import SupabaseConfig, SupabaseSyncError

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


def report_fixture(tmp_path):
    from test_reporting import make_report, seeded_cycles

    store, ids = seeded_cycles(tmp_path)
    return make_report(store, cycle_id=ids[0])


def test_public_store_cannot_generate_report(tmp_path, monkeypatch):
    report = report_fixture(tmp_path)
    store = SupabaseCycleStore(tmp_path / "viewer.db", CONFIG)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("Unexpected cloud write"))
    with pytest.raises(ValueError, match="Owner login required"):
        store.save_report(report)
    assert SQLiteTelemetryStore.fetch_reports(store, "tefal-kettle", "monitor-001") == []


def test_cloud_report_save_retry_and_fresh_cache(tmp_path, monkeypatch):
    report = report_fixture(tmp_path)
    rows = {}

    def post(url, *, headers, params, json, timeout):
        assert url == CONFIG.url + "/rest/v1/reports"
        assert headers["apikey"] == CONFIG.service_role_key
        assert "resolution=ignore-duplicates" in headers["Prefer"]
        assert params == {"on_conflict": "report_id"}
        first = json["report_id"] not in rows
        rows.setdefault(json["report_id"], json)
        return httpx.Response(
            201 if first else 200, json=[rows[json["report_id"]]] if first else [],
            request=httpx.Request("POST", url),
        )

    def get(url, *, headers, params, timeout):
        assert url == CONFIG.url + "/rest/v1/reports"
        result = list(rows.values())
        if "report_id" in params:
            result = [r for r in result if "eq." + r["report_id"] == params["report_id"]]
        return httpx.Response(200, json=result, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    store = SupabaseCycleStore(tmp_path / "cloud-report.db", CONFIG, can_edit=True)
    assert store.save_report(report) == str(report.report_id)
    assert store.save_report(report) == str(report.report_id)
    assert len(rows) == 1
    restarted = SupabaseCycleStore(tmp_path / "fresh-report.db", CONFIG)
    assert restarted.fetch_reports("tefal-kettle", "monitor-001") == [
        report.model_dump(mode="json")
    ]
    assert SQLiteTelemetryStore.fetch_report(restarted, str(report.report_id)) == (
        report.model_dump(mode="json")
    )


def test_uncertain_cloud_report_write_can_be_retried(tmp_path, monkeypatch):
    report = report_fixture(tmp_path)
    rows = {}

    def post(url, **kwargs):
        row = kwargs["json"]
        if not rows:
            rows[row["report_id"]] = row
            raise httpx.ReadTimeout("response lost")
        return httpx.Response(200, json=[], request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(
        200, json=list(rows.values()), request=httpx.Request("GET", url)
    ))
    store = SupabaseCycleStore(tmp_path / "uncertain.db", CONFIG, can_edit=True)
    with pytest.raises(SupabaseSyncError, match="not confirmed"):
        store.save_report(report)
    assert SQLiteTelemetryStore.fetch_reports(store, "tefal-kettle", "monitor-001") == []
    assert store.save_report(report) == str(report.report_id)
    assert len(rows) == 1


@pytest.mark.parametrize("response", [{}, [{"invalid": "report"}]])
def test_malformed_cloud_report_does_not_enter_cache(tmp_path, monkeypatch, response):
    store = SupabaseCycleStore(tmp_path / "invalid-report.db", CONFIG)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(
        200, json=response, request=httpx.Request("GET", url)
    ))
    with pytest.raises(SupabaseSyncError):
        store.fetch_reports("tefal-kettle", "monitor-001")
    assert SQLiteTelemetryStore.fetch_reports(store, "tefal-kettle", "monitor-001") == []


def test_cloud_failure_preserves_explicit_cache_fallback(tmp_path, monkeypatch):
    report = report_fixture(tmp_path)
    store = SupabaseCycleStore(tmp_path / "cached-report.db", CONFIG)
    SQLiteTelemetryStore.save_report(store, report)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(
        503, request=httpx.Request("GET", url)
    ))
    with pytest.raises(SupabaseSyncError):
        store.fetch_reports("tefal-kettle", "monitor-001")
    assert len(SQLiteTelemetryStore.fetch_reports(store, "tefal-kettle", "monitor-001")) == 1


def test_cloud_report_scope_mismatch_is_rejected(tmp_path, monkeypatch):
    report = report_fixture(tmp_path)
    store = SupabaseCycleStore(tmp_path / "wrong-scope.db", CONFIG)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(
        200, json=[report.model_dump(mode="json")], request=httpx.Request("GET", url)
    ))
    with pytest.raises(SupabaseSyncError):
        store.fetch_reports("tefal-kettle", "another-device")


def test_cloud_report_history_can_discover_saved_reports(tmp_path, monkeypatch):
    report = report_fixture(tmp_path)
    def get(url, **kwargs):
        assert "profile_id" not in kwargs["params"]
        assert "device_id" not in kwargs["params"]
        assert kwargs["params"]["limit"] == "100"
        return httpx.Response(200, json=[report.model_dump(mode="json")],
                              request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", get)
    store = SupabaseCycleStore(tmp_path / "report-discovery.db", CONFIG)
    assert store.fetch_reports() == [report.model_dump(mode="json")]
    with pytest.raises(ValueError, match="scope"):
        store.fetch_reports(device_id="monitor-001")
