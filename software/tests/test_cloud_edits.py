from datetime import UTC, datetime, timedelta

import httpx
import pytest

from energy_monitor.cloud_edits import SupabaseCycleStore
from energy_monitor.models import CycleDetectionSettings, ManualCycleRecord
from energy_monitor.storage import SQLiteTelemetryStore
from energy_monitor.supabase_sync import SupabaseConfig, SupabaseSyncError

CONFIG = SupabaseConfig("https://project.supabase.co", "test-server-key")


def test_public_store_cannot_write(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("Unexpected cloud write"))
    store = SupabaseCycleStore(tmp_path / "public.db", CONFIG)
    with pytest.raises(ValueError, match="Owner login required"):
        store.add_cycle_label("1 litre")
    assert store.list_cycle_labels() == []


def test_cloud_rules_are_owner_written_and_survive_fresh_cache(tmp_path, monkeypatch):
    version = "appliance-" + "a" * 32
    base = CycleDetectionSettings.simulation_defaults()
    new = base.model_copy(update={"start_power_w": 30, "stop_power_w": 10})
    published = []

    def append(url, *, headers, json, timeout):
        assert url.endswith("/append_intelligence_appliance_rule")
        assert "version" not in json["p_settings"]
        published.append({
            "version": version, "device_id": json["p_device_id"],
            "profile_id": json["p_profile_id"], "appliance_name": json["p_appliance_name"],
            "effective_at": "2026-11-06T06:00:00+00:00",
            "settings": {**json["p_settings"], "version": version},
        })
        return httpx.Response(200, json=version, request=httpx.Request("POST", url))

    def read(url, **kwargs):
        return httpx.Response(200, json=published, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "post", append)
    monkeypatch.setattr(httpx, "get", read)
    guest = SupabaseCycleStore(tmp_path / "guest.db", CONFIG)
    with pytest.raises(ValueError, match="Owner login required"):
        guest.save_appliance_rule("meter", "tefal-kettle", "Fan", new)
    assert not published
    owner = SupabaseCycleStore(tmp_path / "owner.db", CONFIG, can_edit=True)
    assert owner.save_appliance_rule("meter", "tefal-kettle", "Fan", new) == version
    guest.refresh_appliance_rules()
    guest.refresh_appliance_rules()
    name, loaded = guest.cycle_rule(version)
    assert (name, loaded.start_power_w, loaded.stop_power_w) == ("Fan", 30, 10)
    published[0]["settings"]["start_power_w"] = 60
    with pytest.raises(SupabaseSyncError, match="verify appliance rules"):
        guest.refresh_appliance_rules()
    assert guest.cycle_rule(version)[1].start_power_w == 30


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

    state["assignments"] = []
    restarted.refresh_edits()
    restarted.refresh_edits()
    assert restarted.fetch_cycle_label(cycle_id) is None
    history = restarted.fetch_cycle_label_history(cycle_id)
    assert len(history) == 1
    assert not history[0]["is_active"]


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

def test_public_annotation_concurrency_and_idempotence(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from test_dashboard import build_dashboard_cycle_database

    database = build_dashboard_cycle_database(tmp_path)
    store = SQLiteTelemetryStore(database)
    cycle = store.fetch_cycles()[0]
    cycle_id = cycle["cycle_id"]
    readings_before = store.fetch_cycle_readings(cycle_id)

    def annotate(label):
        try:
            SQLiteTelemetryStore(database).save_cycle_annotation(
                cycle_id, label=label, note="Shared note", expected_id=None,
            )
        except ValueError as exc:
            return str(exc)
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(annotate, ["Morning", "Evening"])) == [
            "This annotation changed. Refresh before editing.", "saved",
        ]
    reopened = SQLiteTelemetryStore(database)
    active = reopened.fetch_cycle_annotation(cycle_id)
    reopened.save_cycle_annotation(cycle_id, label=active["label"], note=active["note"])
    assert len(reopened.fetch_cycle_label_history(cycle_id)) == 1
    reopened.save_cycle_annotation(
        cycle_id, label=active["label"], note="Revised", expected_id=active["id"],
    )
    history = reopened.fetch_cycle_label_history(cycle_id)
    assert [row["annotation_note"] for row in history] == ["Shared note", "Revised"]
    assert sum(row["is_active"] for row in history) == 1
    assert reopened.fetch_cycle_readings(cycle_id) == readings_before


def test_public_cloud_write_only_sends_annotation_and_scope(tmp_path, monkeypatch):
    from test_dashboard import build_dashboard_cycle_database

    store = SupabaseCycleStore(build_dashboard_cycle_database(tmp_path), CONFIG)
    cycle = store.fetch_cycles()[0]
    calls = []
    monkeypatch.setattr(store, "refresh_edits", lambda: None)

    def rpc(name, payload):
        calls.append((name, payload))
        return {"status": "saved"}

    monkeypatch.setattr(store, "_rpc", rpc)
    with pytest.raises(ValueError, match="plain-text"):
        store.save_cycle_annotation(cycle["cycle_id"], label="label", note="x" * 501)
    assert not calls
    store.save_cycle_annotation(cycle["cycle_id"], label=" Morning  mug ", note=" 0.5 L ")
    assert calls == [("save_public_cycle_annotation", {
        "p_cycle_id": cycle["cycle_id"], "p_device_id": cycle["device_id"],
        "p_profile_id": cycle["profile_id"], "p_origin": cycle["data_origin"],
        "p_label": "Morning mug", "p_note": "0.5 L", "p_expected_id": None,
    })]

def test_new_sensor_annotation_persists_cycle_before_retry(tmp_path, monkeypatch):
    from uuid import uuid4

    from energy_monitor.models import CycleDetectionSettings
    from energy_monitor.simulator import batch_readings, generate_cycle_scenario

    store = SupabaseCycleStore(tmp_path / "new-sensor.db", CONFIG)
    samples = [sample.model_copy(update={"firmware_version": "pzem-fw-0.2.6"})
               for sample in generate_cycle_scenario("normal", datetime(2026, 11, 5, tzinfo=UTC))]
    for batch in batch_readings(samples):
        store.insert_batch(batch)
    store.process_cycles(CycleDetectionSettings.simulation_defaults())
    cycle = store.fetch_cycles()[0]
    before = store.fetch_cycle_readings(cycle["cycle_id"])
    durable_cycles, saved_reports, history = {}, {}, []

    def get(url, *, headers, params, timeout):
        assert url == CONFIG.url + "/rest/v1/appliance_cycles"
        assert params == {"select": "cycle_id", "cycle_id": "eq." + cycle["cycle_id"]}
        assert headers["apikey"] == CONFIG.service_role_key
        rows = [{"cycle_id": key} for key in durable_cycles]
        return httpx.Response(200, json=rows, request=httpx.Request("GET", url))

    def rpc(name, payload):
        if name == "save_completed_sensor_report":
            report = payload["report"]
            evidence = report["evidence_snapshot"]["cycles"][0]
            assert report["engine"] == "template"
            assert evidence["cycle_id"] == cycle["cycle_id"]
            assert evidence["meter_energy_kwh"] == cycle["meter_energy_kwh"]
            durable_cycles[evidence["cycle_id"]] = evidence
            saved_reports[report["report_id"]] = report
            return report["report_id"]
        if name == "save_public_cycle_annotation":
            if payload["p_cycle_id"] not in durable_cycles:
                return {"status": "unavailable"}
            assert payload["p_device_id"] == cycle["device_id"]
            assert payload["p_profile_id"] == cycle["profile_id"]
            assert payload["p_origin"] == "sensor"
            current = history[-1] if history else {}
            if (current.get("label") == payload["p_label"]
                    and current.get("note") == payload["p_note"]):
                return {"status": "saved"}
            if payload["p_expected_id"] != current.get("id"):
                return {"status": "conflict"}
            history.append({"id": str(uuid4()), "label": payload["p_label"],
                            "note": payload["p_note"], "public": True})
            return {"status": "saved"}
        assert name == "dashboard_cycle_annotation"
        return history[-1] if history else {}

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(store, "_rpc", rpc)
    monkeypatch.setattr(store, "refresh_edits", lambda: None)
    store.save_cycle_annotation(cycle["cycle_id"], label="Normal", note="Approx. 700–800 mL")
    assert len(saved_reports) == 1
    report_before = next(iter(saved_reports.values())).copy()
    annotation = store.fetch_cycle_annotation(cycle["cycle_id"])
    store.save_cycle_annotation(cycle["cycle_id"], label="Normal", note="Volume not exact",
                                expected_id=annotation["id"])
    store.save_cycle_annotation(cycle["cycle_id"], label="Normal", note="Volume not exact")
    assert len(history) == 2
    assert len(saved_reports) == 1
    assert next(iter(saved_reports.values())) == report_before
    assert store.fetch_cycle_readings(cycle["cycle_id"]) == before

    reopened = SupabaseCycleStore(tmp_path / "fresh-annotations.db", CONFIG)
    monkeypatch.setattr(reopened, "_rpc", rpc)
    assert reopened.fetch_cycle_annotation(cycle["cycle_id"])["note"] == "Volume not exact"


@pytest.mark.parametrize("cloud_rows", [[{"cycle_id": "already-saved"}], {"invalid": "response"}])
def test_unavailable_saved_cycle_cannot_trigger_report_write(tmp_path, monkeypatch, cloud_rows):
    from energy_monitor.models import CycleDetectionSettings
    from energy_monitor.simulator import batch_readings, generate_cycle_scenario

    store = SupabaseCycleStore(tmp_path / "scope-error.db", CONFIG)
    for batch in batch_readings([
        s.model_copy(update={"firmware_version": "pzem-fw-0.2.6"})
        for s in generate_cycle_scenario("normal", datetime(2026, 11, 5, tzinfo=UTC))
    ]):
        store.insert_batch(batch)
    store.process_cycles(CycleDetectionSettings.simulation_defaults())
    calls = []
    def rpc(name, payload):
        calls.append(name)
        assert name == "save_public_cycle_annotation"
        return {"status": "unavailable"}
    monkeypatch.setattr(store, "_rpc", rpc)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(
        200, json=cloud_rows, request=httpx.Request("GET", url)))
    with pytest.raises(ValueError):
        store.save_cycle_annotation(store.fetch_cycles()[0]["cycle_id"], label="Normal", note="")
    assert calls == ["save_public_cycle_annotation"]
