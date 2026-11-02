"""A3 offline integration. All readings, annotations and providers are fixtures."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from test_report_worker import quota

from energy_monitor.dataset import CollectionRecord, fit_prepared, make_split, prepare_dataset
from energy_monitor.models import CycleDetectionSettings
from energy_monitor.simulator import batch_readings, generate_cycle_scenario
from energy_monitor.storage import SQLiteTelemetryStore


@pytest.fixture
def rehearsal(tmp_path):
    store = SQLiteTelemetryStore(tmp_path / "rehearsal.db")
    batches = []
    for i in range(60):
        start = datetime(2026, 11, 9, tzinfo=UTC) + timedelta(days=i // 10, minutes=i % 10 * 5)
        scale = 1 + (i % 7) * 0.005
        readings = [
            r.model_copy(
                update={
                    "sample_sequence": r.sample_sequence + i * 1000,
                    "active_power_w": r.active_power_w * scale,
                    "current_a": r.current_a * scale,
                    "cumulative_energy_kwh": r.cumulative_energy_kwh * scale,
                }
            )
            for r in generate_cycle_scenario("normal", start)
        ]
        for batch in batch_readings(readings):
            store.insert_batch(batch)
            batches.append(batch)
    store.process_cycles(CycleDetectionSettings.simulation_defaults())
    cycles = sorted(store.fetch_cycles(), key=lambda c: c["started_at"])
    assert len(cycles) == 60
    records = [
        CollectionRecord(
            cycle_id=c["cycle_id"],
            session_id=f"S{i // 10 + 1:02}",
            session_started_at=datetime(2026, 11, 9, tzinfo=UTC) + timedelta(days=i // 10),
            water_volume_ml=1000,
            start_temperature_band="room",
            operating_condition="standard_boil",
            baseline_group="baseline",
            reviewed=True,
            label="normal",
            label_basis="normal_observation",
            operator="PRIVATE_OPERATOR",
            notes="PRIVATE_NOTE_DO_NOT_SEND",
        )
        for i, c in enumerate(cycles)
    ]
    dataset = prepare_dataset(store, records, kind="synthetic_fixture")
    assert all(not c.quality_flags for c in dataset.cycles)
    plan = make_split(dataset, baseline_group="baseline")
    model = fit_prepared(dataset, plan, test_only=True)
    assert model.status == "test_only"
    return store, dataset, model, batches


def test_full_cycle_context_mock_review_restart_and_saved_display(rehearsal, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from test_dashboard import DASHBOARD_PATH

    from energy_monitor.report_worker import handle_completed_cycle

    store, dataset, model, batches = rehearsal
    cid = str(dataset.cycles[-1].collection.cycle_id)
    calls = []

    def provider(package):
        calls.append(package)
        assert package["context"]["collection_conditions"][0]["water_volume_ml"] == 1000
        assert "PRIVATE_" not in json.dumps(package)
        return {"observations": ["data_origin", "conditions_0"], "actions": ["collect_calibration"]}

    result = handle_completed_cycle(
        store,
        cid,
        dataset_context=dataset,
        anomaly_model=model,
        provider=provider,
        ai_authorized=True,
        state_dir=tmp_path / "state",
        usage_reader=quota,
    )
    assert result["ai_status"] == "completed"
    base = store.fetch_report(result["numerical_report_id"])
    cycle = base["evidence_snapshot"]["cycles"][0]
    binding = cycle["collection_context"]
    assert binding["status"] == "bound" and binding["session_id"] == "S06"
    assert binding["scope"]["water_volume_ml"] == 1000
    assert binding["quality_indicators"]["sample_count"] == cycle["sample_count"]
    assert base["evidence_snapshot"]["model"]["predictions"][0]["anomaly_score"] is None
    restarted = SQLiteTelemetryStore(tmp_path / "rehearsal.db")
    for batch in batches:
        assert restarted.insert_batch(batch).replayed
    restarted.process_cycles(CycleDetectionSettings.simulation_defaults())
    again = handle_completed_cycle(
        restarted,
        cid,
        dataset_context=dataset,
        anomaly_model=model,
        provider=provider,
        ai_authorized=True,
        state_dir=tmp_path / "state",
        usage_reader=quota,
    )
    assert again["numerical_report_id"] == result["numerical_report_id"] and len(calls) == 1
    assert len(restarted.fetch_reports()) == 2
    import subprocess
    import sys

    code = (
        "import json,sys; from energy_monitor.storage import SQLiteTelemetryStore; "
        "rows=SQLiteTelemetryStore(sys.argv[1]).fetch_reports(); "
        "print(json.dumps({'reports':len(rows),'context':rows[0]['evidence_snapshot']"
        "['cycles'][0]['collection_context']['status']}))"
    )
    fresh = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "rehearsal.db")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(fresh.stdout) == {"reports": 2, "context": "bound"}
    monkeypatch.setenv("ENERGY_MONITOR_SOURCE", "local")
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(tmp_path / "rehearsal.db"))
    monkeypatch.setattr(
        "energy_monitor.report_narrative.run_codex", lambda _: pytest.fail("live AI")
    )
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=15)
    app.session_state["mode"] = "Reports"
    app.run()
    assert not app.exception
    assert any("1,000 mL" in c.value for c in app.caption)
    assert any("S06" in c.value for c in app.caption)
    assert any(m.label == "Anomaly score" and m.value == "Unavailable" for m in app.metric)
    app.run()
    assert len(restarted.fetch_reports()) == 2


@pytest.mark.parametrize("count", [5, 10, 20])
def test_context_reports_keep_exact_manual_batch_sizes(rehearsal, count):
    from energy_monitor.reporting import build_report, validate_report

    store, dataset, model, _ = rehearsal
    report = build_report(
        store,
        profile_id="tefal-kettle",
        device_id="monitor-001",
        cycle_count=count,
        dataset_context=dataset,
        anomaly_model=model,
    )
    assert len(report.evidence_snapshot["cycles"]) == count
    assert all(
        c["collection_context"]["status"] == "bound" for c in report.evidence_snapshot["cycles"]
    )
    assert validate_report(report) == report
    from energy_monitor.report_narrative import narrative_catalog

    assert len(json.dumps(narrative_catalog(report))) < 19000


def test_stale_or_wrong_collection_cannot_bind_to_readings(rehearsal):
    from energy_monitor.reporting import build_report

    store, dataset, model, _ = rehearsal
    stale = dataset.model_copy(deep=True)
    stale.cycles[-1].features.values["average_power_w"] += 100
    report = build_report(
        store,
        profile_id="tefal-kettle",
        device_id="monitor-001",
        cycle_id=str(stale.cycles[-1].collection.cycle_id),
        dataset_context=stale,
        anomaly_model=model,
    )
    binding = report.evidence_snapshot["cycles"][0]["collection_context"]
    assert binding["status"] == "mismatch"
    assert report.evidence_snapshot["model"]["predictions"][0]["anomaly_score"] is None


def test_context_report_survives_unknown_ai_outcome_without_retry(rehearsal, tmp_path):
    from energy_monitor.report_worker import handle_completed_cycle

    store, dataset, model, _ = rehearsal
    calls = []

    def interrupted(_):
        calls.append(1)
        raise RuntimeError("fixture interruption")

    options = dict(
        dataset_context=dataset,
        anomaly_model=model,
        provider=interrupted,
        ai_authorized=True,
        state_dir=tmp_path / "state",
        usage_reader=quota,
    )
    first = handle_completed_cycle(store, str(dataset.cycles[-1].collection.cycle_id), **options)
    second = handle_completed_cycle(
        SQLiteTelemetryStore(tmp_path / "rehearsal.db"),
        str(dataset.cycles[-1].collection.cycle_id),
        **options,
    )
    assert first["ai_status"] == second["ai_status"] == "needs_review"
    assert len(calls) == 1 and len(store.fetch_reports()) == 1


@pytest.mark.parametrize("committed_before_loss", [False, True])
def test_network_loss_keeps_local_numerical_and_retry_confirms_one_remote_copy(
    rehearsal, tmp_path, monkeypatch, committed_before_loss
):
    import httpx
    from test_cloud_edits import CONFIG

    from energy_monitor.cloud_edits import SupabaseCycleStore
    from energy_monitor.report_worker import handle_completed_cycle

    store, dataset, model, _ = rehearsal
    remote = {}
    calls = []

    def lost_post(url, **kwargs):
        if committed_before_loss:
            payload = kwargs["json"]
            remote.setdefault(payload["report_id"], payload)
        raise httpx.ConnectError("fixture offline")

    monkeypatch.setattr(httpx, "post", lost_post)
    cloud = SupabaseCycleStore(tmp_path / "rehearsal.db", CONFIG, can_edit=True)
    cid = str(dataset.cycles[-1].collection.cycle_id)

    def provider(package):
        calls.append(package)
        return {"observations": ["data_origin", "conditions_0"], "actions": ["collect_calibration"]}

    options = dict(
        dataset_context=dataset,
        anomaly_model=model,
        provider=provider,
        ai_authorized=True,
        state_dir=tmp_path / "state",
        usage_reader=quota,
    )
    offline = handle_completed_cycle(cloud, cid, **options)
    assert offline["storage_status"] == "local_only_cloud_unconfirmed"
    assert offline["ai_status"] == "network_unavailable" and not calls
    assert len(SQLiteTelemetryStore.fetch_reports(cloud)) == 1

    def post(url, **kwargs):
        payload = kwargs["json"]
        remote.setdefault(payload["report_id"], payload)
        return httpx.Response(201, request=httpx.Request("POST", url))

    def get(url, **kwargs):
        params = kwargs["params"]
        rid = params.get("report_id", "").removeprefix("eq.")
        rows = [remote[rid]] if rid in remote else []
        return httpx.Response(200, json=rows, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    restarted = SupabaseCycleStore(tmp_path / "rehearsal.db", CONFIG, can_edit=True)
    recovered = handle_completed_cycle(restarted, cid, **options)
    assert recovered["numerical_report_id"] == offline["numerical_report_id"]
    assert recovered["ai_status"] == "completed" and len(calls) == 1 and len(remote) == 2
    handle_completed_cycle(restarted, cid, **options)
    assert len(calls) == 1 and len(remote) == 2


@pytest.mark.parametrize("failure", ["provider_missing", "timeout", "invalid_selection"])
def test_provider_fallback_preserves_context_numerical_report(rehearsal, tmp_path, failure):
    from energy_monitor.report_narrative import NarrativeError
    from energy_monitor.report_worker import handle_completed_cycle

    store, dataset, model, _ = rehearsal
    calls = []

    def provider(_):
        calls.append(1)
        if failure == "timeout":
            raise NarrativeError("timeout")
        return {"observations": ["invented"], "actions": ["invented"], "anomaly_score": 99}

    result = handle_completed_cycle(
        store,
        str(dataset.cycles[-1].collection.cycle_id),
        dataset_context=dataset,
        anomaly_model=model,
        provider=None if failure == "provider_missing" else provider,
        ai_authorized=True,
        state_dir=tmp_path / "state",
        usage_reader=quota,
    )
    base = store.fetch_report(result["numerical_report_id"])
    assert base["evidence_snapshot"]["cycles"][0]["collection_context"]["status"] == "bound"
    assert all(
        p["anomaly_score"] is None for p in base["evidence_snapshot"]["model"]["predictions"]
    )
    assert result["ai_status"] == (
        "provider_unavailable" if failure == "provider_missing" else "review_failed"
    )
    assert len(calls) == (0 if failure == "provider_missing" else 1)


def test_configured_manifest_model_unavailable_and_saved_display(rehearsal, tmp_path, monkeypatch):
    from energy_monitor.anomaly import CycleAnomalyModel
    from energy_monitor.reporting import build_report

    store, dataset, _, _ = rehearsal
    manifest = tmp_path / "manifest.json"
    manifest.write_text(dataset.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("ENERGY_MONITOR_DATASET_MANIFEST", str(manifest))
    monkeypatch.setenv("ENERGY_MONITOR_ANOMALY_MODEL", str(tmp_path / "missing.zip"))
    options = dict(
        profile_id="tefal-kettle",
        device_id="monitor-001",
        cycle_id=str(dataset.cycles[-1].collection.cycle_id),
    )
    report = build_report(store, **options)
    assert report.evidence_snapshot["cycles"][0]["collection_context"]["status"] == "bound"
    prediction = report.evidence_snapshot["model"]["predictions"][0]
    assert "configured_model_unavailable" in prediction["quality_warnings"]
    assert prediction["anomaly_score"] is None
    manifest.write_text("not-json", encoding="utf-8")
    report = build_report(store, **options, anomaly_model=CycleAnomalyModel())
    assert report.evidence_snapshot["cycles"][0]["collection_context"]["status"] == "unavailable"
    assert report.evidence_snapshot["model"]["predictions"][0]["anomaly_score"] is None


def test_wrong_volume_context_does_not_reach_scoring(rehearsal):
    from energy_monitor.reporting import build_report

    store, dataset, model, _ = rehearsal
    corrected = dataset.model_copy(deep=True)
    corrected.cycles[-1].collection.water_volume_ml = 500
    report = build_report(
        store,
        profile_id="tefal-kettle",
        device_id="monitor-001",
        cycle_id=str(corrected.cycles[-1].collection.cycle_id),
        dataset_context=corrected,
        anomaly_model=model,
    )
    binding = report.evidence_snapshot["cycles"][0]["collection_context"]
    assert binding["scope"]["water_volume_ml"] == 500
    # Test-only score remains unavailable; check scope rejection at the model boundary too.
    assert (
        model.predict(
            corrected.cycles[-1].features, testing=True, collection_scope=binding["scope"]
        ).status
        == "scope_mismatch"
    )


@pytest.mark.parametrize("change", ["missing", "unreviewed"])
def test_missing_or_unreviewed_context_remains_unavailable(rehearsal, change):
    from energy_monitor.reporting import build_report

    store, dataset, model, _ = rehearsal
    cid = str(dataset.cycles[-1].collection.cycle_id)
    dataset = dataset.model_copy(deep=True)
    if change == "missing":
        dataset.cycles.pop()
    else:
        dataset.cycles[-1].collection.reviewed = False
    report = build_report(
        store,
        profile_id="tefal-kettle",
        device_id="monitor-001",
        cycle_id=cid,
        dataset_context=dataset,
        anomaly_model=model,
    )
    assert report.evidence_snapshot["cycles"][0]["collection_context"]["status"] == change
    assert report.evidence_snapshot["model"]["predictions"][0]["anomaly_score"] is None


def test_snapshot_context_rejects_wrong_cycle_measurements_and_quality(rehearsal):
    from copy import deepcopy

    from energy_monitor.dataset import validate_report_binding
    from energy_monitor.reporting import build_report

    store, dataset, model, _ = rehearsal
    report = build_report(
        store,
        profile_id="tefal-kettle",
        device_id="monitor-001",
        cycle_id=str(dataset.cycles[-1].collection.cycle_id),
        dataset_context=dataset,
        anomaly_model=model,
    )
    original = report.evidence_snapshot["cycles"][0]
    for field, value in [("average_power_w", 1), ("gap_count", 1)]:
        changed = deepcopy(original)
        changed[field] += value
        with pytest.raises(ValueError):
            validate_report_binding(changed)
    changed = deepcopy(original)
    changed["collection_context"]["cycle_id"] = str(dataset.cycles[0].collection.cycle_id)
    with pytest.raises(ValueError):
        validate_report_binding(changed)


def test_mock_only_trained_contract_receives_actual_bound_scope(tmp_path, monkeypatch):
    from energy_monitor.anomaly import AnomalyPrediction, CycleAnomalyModel
    from energy_monitor.reporting import build_report

    store = SQLiteTelemetryStore(tmp_path / "mock-contract.db")
    for batch in batch_readings(
        [
            r.model_copy(update={"firmware_version": "pzem-test-fixture"})
            for r in generate_cycle_scenario("normal", datetime(2026, 11, 9, tzinfo=UTC))
        ]
    ):
        store.insert_batch(batch)
    store.process_cycles(CycleDetectionSettings.simulation_defaults())
    cycle = store.fetch_cycles()[0]
    record = CollectionRecord(
        cycle_id=cycle["cycle_id"],
        session_id="FIXTURE",
        session_started_at=datetime(2026, 11, 9, tzinfo=UTC),
        water_volume_ml=1000,
        start_temperature_band="room",
        operating_condition="standard_boil",
        baseline_group="baseline",
        reviewed=True,
        label="normal",
        label_basis="normal_observation",
        operator="fixture",
    )
    # Sensor-shaped inputs and trained output are explicit temporary test doubles.
    # No production estimator is fitted, saved, activated or claimed here.
    dataset = prepare_dataset(store, [record])
    scope = dataset.cycles[0].scope()
    model = CycleAnomalyModel(
        metadata={"status": "trained", "model_version": "fixture-only", "collection_scope": scope}
    )
    calls = []

    def mock_predict(features, **kwargs):
        calls.append(kwargs["collection_scope"])
        return AnomalyPrediction(
            status="scored",
            classification="anomalous",
            anomaly_score=88,
            raw_decision=-0.1,
            model_version="fixture-only",
            training_status="trained",
            data_origin="sensor",
            comparisons={},
        )

    monkeypatch.setattr(model, "predict", mock_predict)
    report = build_report(
        store,
        profile_id="tefal-kettle",
        device_id="monitor-001",
        cycle_id=cycle["cycle_id"],
        dataset_context=dataset,
        anomaly_model=model,
    )
    assert calls == [scope] and model.estimator is None
    assert report.evidence_snapshot["model"]["predictions"][0]["anomaly_score"] == 88


def test_network_fallback_does_not_bypass_owner_permission(rehearsal, tmp_path, monkeypatch):
    import httpx
    from test_cloud_edits import CONFIG

    from energy_monitor.cloud_edits import SupabaseCycleStore
    from energy_monitor.report_worker import handle_completed_cycle

    _, dataset, model, _ = rehearsal
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("public cloud write"))
    cloud = SupabaseCycleStore(tmp_path / "rehearsal.db", CONFIG, can_edit=False)
    with pytest.raises(ValueError, match="Owner"):
        handle_completed_cycle(
            cloud,
            str(dataset.cycles[-1].collection.cycle_id),
            dataset_context=dataset,
            anomaly_model=model,
        )
    assert SQLiteTelemetryStore.fetch_reports(cloud) == []


def test_fresh_local_api_runs_complete_cycle_with_configured_fixture_context(
    rehearsal, tmp_path, monkeypatch
):
    import time

    import httpx
    from test_api import start_test_server_with_store

    store, dataset, model, batches = rehearsal
    manifest = tmp_path / "fixture-manifest.json"
    manifest.write_text(dataset.model_dump_json(), encoding="utf-8")
    model_path = tmp_path / "test-only-model.zip"
    model.save(model_path)
    monkeypatch.setenv("ENERGY_MONITOR_DATASET_MANIFEST", str(manifest))
    monkeypatch.setenv("ENERGY_MONITOR_ANOMALY_MODEL", str(model_path))
    monkeypatch.setattr(
        "energy_monitor.report_narrative.run_codex", lambda _: pytest.fail("live AI")
    )
    fresh = SQLiteTelemetryStore(tmp_path / "fresh-ingest.db")
    server, url = start_test_server_with_store(fresh)
    first_cycle = [b for b in batches if b.readings[0].sample_sequence < 1000]
    try:
        with httpx.Client(base_url=url) as client:
            for batch in first_cycle:
                response = client.post(
                    "/api/v1/telemetry/batches",
                    json=batch.model_dump(mode="json"),
                    headers={"Idempotency-Key": str(batch.batch_id)},
                )
                assert response.status_code == 201
            deadline = time.monotonic() + 10
            while not fresh.fetch_reports() and time.monotonic() < deadline:
                time.sleep(0.01)
            reports = fresh.fetch_reports()
            assert len(reports) == 1
            assert (
                reports[0]["evidence_snapshot"]["cycles"][0]["collection_context"]["status"]
                == "bound"
            )
            assert (
                reports[0]["evidence_snapshot"]["model"]["predictions"][0]["anomaly_score"] is None
            )
            last = first_cycle[-1]
            assert (
                client.post(
                    "/api/v1/telemetry/batches",
                    json=last.model_dump(mode="json"),
                    headers={"Idempotency-Key": str(last.batch_id)},
                ).status_code
                == 200
            )
            assert len(fresh.fetch_reports()) == 1
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("future", [True, False])
def test_invalid_session_annotation_falls_back_without_discarding_numerical(rehearsal, future):
    from energy_monitor.reporting import build_report, validate_report

    store, dataset, model, _ = rehearsal
    dataset = dataset.model_copy(deep=True)
    dataset.cycles[-1].collection.session_started_at += (
        timedelta(hours=1) if future else timedelta(minutes=5)
    )
    report = build_report(
        store,
        profile_id="tefal-kettle",
        device_id="monitor-001",
        cycle_id=str(dataset.cycles[-1].collection.cycle_id),
        dataset_context=dataset,
        anomaly_model=model,
    )
    assert report.evidence_snapshot["cycles"][0]["collection_context"]["status"] == "mismatch"
    assert validate_report(report) == report
