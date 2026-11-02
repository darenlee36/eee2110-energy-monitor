"""A1 report integration; providers and scored outcomes are explicit test doubles."""
import json

import pytest
from test_anomaly import case
from test_report_worker import quota
from test_reporting import make_report, seeded_cycles


def test_test_only_model_is_unavailable_in_saved_report_and_ai_context(tmp_path):
    from energy_monitor.anomaly import train_model
    from energy_monitor.report_narrative import narrative_catalog
    from energy_monitor.reporting import build_report, validate_report
    store, ids = seeded_cycles(tmp_path)
    model = train_model([case(i) for i in range(1, 41)], test_only=True)
    report = build_report(store, profile_id="tefal-kettle", device_id="monitor-001",
                          cycle_id=ids[0], anomaly_model=model)
    assert report.reporting_version == "cycle-report-template-v4"
    prediction = report.evidence_snapshot["model"]["predictions"][0]
    assert prediction["anomaly_score"] is None
    assert prediction["status"] == "test_only_model"
    assert validate_report(report) == report
    package = narrative_catalog(report)
    assert "context" in package and package["context"]["model_findings"]
    assert "monitor-001" not in json.dumps(package)
    assert "not a failure probability" in report.report_text


def test_cycle_event_duplicate_guard_and_failed_ai_preserve_numerical_report(tmp_path):
    from energy_monitor.report_narrative import NarrativeError
    from energy_monitor.report_worker import handle_completed_cycle
    store, ids = seeded_cycles(tmp_path)
    calls = []
    def provider(evidence):
        calls.append(evidence)
        raise NarrativeError("timeout")
    kw = dict(state_dir=tmp_path / "worker", provider=provider, ai_authorized=True,
              usage_reader=lambda: quota())
    first = handle_completed_cycle(store, ids[0], **kw)
    second = handle_completed_cycle(store, ids[0], **kw)
    assert first["numerical_report_id"] == second["numerical_report_id"]
    assert len(calls) == 1
    assert store.fetch_report(first["numerical_report_id"])["engine"] == "template"
    assert len(store.fetch_reports()) == 2  # numerical + durable failure, not repeated calls


def test_cycle_event_requires_authorization_and_no_browser_ai(tmp_path):
    from energy_monitor.report_worker import handle_completed_cycle
    store, ids = seeded_cycles(tmp_path)
    def forbidden(_):
        raise AssertionError("unauthorized provider call")
    result = handle_completed_cycle(store, ids[0], provider=forbidden)
    assert result["ai_status"] == "not_authorized"
    assert len(store.fetch_reports()) == 1


def test_uncertain_provider_crash_holds_reservation_and_recovers_template(tmp_path):
    from energy_monitor.report_worker import handle_completed_cycle
    store, ids = seeded_cycles(tmp_path)
    calls = []
    def provider(_):
        calls.append(1)
        raise RuntimeError("uncertain completion")
    kw = dict(state_dir=tmp_path / "worker", provider=provider, ai_authorized=True,
              usage_reader=lambda: quota())
    first = handle_completed_cycle(store, ids[0], **kw)
    assert first["ai_status"] == "needs_review"
    second = handle_completed_cycle(store, ids[0], **kw)
    assert second["ai_status"] == "needs_review" and len(calls) == 1
    assert len(store.fetch_reports()) == 1


@pytest.mark.parametrize("count", [5, 10, 20])
def test_a1_retains_exact_batches_and_legacy_history(tmp_path, count):
    from energy_monitor.anomaly import CycleAnomalyModel
    from energy_monitor.reporting import build_report, validate_report
    store, ids = seeded_cycles(tmp_path, count)
    legacy = make_report(store, cycle_count=count)
    store.save_report(legacy)
    report = build_report(store, profile_id="tefal-kettle", device_id="monitor-001",
                          cycle_count=count, anomaly_model=CycleAnomalyModel())
    store.save_report(report)
    assert len(report.evidence_snapshot["model"]["predictions"]) == count
    assert validate_report(store.fetch_report(str(legacy.report_id))) == legacy
    assert len(store.fetch_reports()) == 2


def test_dashboard_displays_saved_a1_unavailable_without_inference(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from test_dashboard import DASHBOARD_PATH

    from energy_monitor.anomaly import CycleAnomalyModel
    from energy_monitor.report_worker import handle_completed_cycle
    store, ids = seeded_cycles(tmp_path)
    handle_completed_cycle(store, ids[0], anomaly_model=CycleAnomalyModel())
    monkeypatch.setenv("ENERGY_MONITOR_SOURCE", "local")
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(tmp_path / "reports.db"))
    monkeypatch.setattr("energy_monitor.report_narrative.run_codex", lambda _: pytest.fail("AI"))
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=15)
    app.session_state["mode"] = "Reports"
    app.run()
    assert not app.exception
    assert any(m.label == "Anomaly score" and m.value == "Unavailable" for m in app.metric)
    assert any("Training status" in c.value for c in app.caption)
    app.run()
    assert len(store.fetch_reports()) == 1


def test_local_ingestion_completes_one_report_without_ai(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    import httpx
    from test_api import start_test_server_with_store

    from energy_monitor.simulator import batch_readings, generate_cycle_scenario
    from energy_monitor.storage import SQLiteTelemetryStore
    monkeypatch.setattr("energy_monitor.report_narrative.run_codex", lambda _: pytest.fail("AI"))
    store = SQLiteTelemetryStore(tmp_path / "events.db")
    server, url = start_test_server_with_store(store)
    try:
        for batch in batch_readings(generate_cycle_scenario("normal", datetime(2026, 11, 1,
                                                                             tzinfo=UTC))):
            response = httpx.post(url + "/api/v1/telemetry/batches",
                json=batch.model_dump(mode="json"),
                headers={"Idempotency-Key": str(batch.batch_id)})
            assert response.status_code == 201
        import time
        deadline = time.monotonic() + 5
        while not store.fetch_reports() and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(store.fetch_reports()) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_a1_large_batch_provider_context_is_bounded(tmp_path):
    from energy_monitor.anomaly import CycleAnomalyModel
    from energy_monitor.report_narrative import narrative_catalog
    from energy_monitor.reporting import build_report
    store, _ = seeded_cycles(tmp_path, 20)
    report = build_report(store, profile_id="tefal-kettle", device_id="monitor-001",
                          cycle_count=20, anomaly_model=CycleAnomalyModel())
    package = narrative_catalog(report)
    assert len(package["context"]["model_findings"]) == 20
    assert len(json.dumps(package)) < 18000


def test_a1_mocked_model_findings_and_codex_round_trip(tmp_path, monkeypatch):
    from test_reporting import change_cycle

    from energy_monitor.anomaly import AnomalyPrediction, CycleAnomalyModel
    from energy_monitor.report_narrative import (
        build_codex_report,
        narrative_catalog,
        narrative_sections,
        validate_codex_report,
    )
    from energy_monitor.reporting import build_report
    store, ids = seeded_cycles(tmp_path)
    change_cycle(store, ids[0], data_origin="sensor")
    with store._connect() as connection:
        connection.execute("UPDATE telemetry SET firmware_version = 'pzem-test-fixture'")
    # Explicit output double only: no production model is trained on fixtures.
    model = CycleAnomalyModel(metadata={"status": "trained", "model_version": "fixture-model"})
    prediction = AnomalyPrediction(status="scored", data_origin="sensor", training_status="trained",
        anomaly_score=97, raw_decision=-.1, classification="anomalous",
        model_version="fixture-model",
        comparisons={"average_power_w": {"value": 2050, "baseline_median": 1900,
                                        "delta": 150, "percent_delta": 7.8947}})
    monkeypatch.setattr(model, "predict", lambda _: prediction)
    report = build_report(store, profile_id="tefal-kettle", device_id="monitor-001",
                          cycle_id=ids[0], anomaly_model=model)
    package = narrative_catalog(report)
    assert "97.0/100" in package["observations"]["model_0"]
    assert "hypotheses" in package["observations"]["hypothesis_0"]
    review = build_codex_report(report, {"observations": ["data_origin", "model_0", "hypothesis_0"],
                                        "actions": ["review_baseline"]})
    assert validate_codex_report(review) == review
    assert narrative_sections(review)["observations"]
    assert "not trained" not in review.report_text


def test_cycle_event_respects_existing_quota_cutoff(tmp_path):
    from energy_monitor.report_worker import handle_completed_cycle
    store, ids = seeded_cycles(tmp_path)
    def forbidden(_):
        pytest.fail("quota-blocked AI call")
    result = handle_completed_cycle(store, ids[0], provider=forbidden, ai_authorized=True,
                                    state_dir=tmp_path / "state", usage_reader=lambda: quota(95, 0))
    assert result["ai_status"] != "completed"
    assert len(store.fetch_reports()) == 1


def test_dashboard_scored_view_is_a_saved_fixture_not_live_inference(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from test_dashboard import DASHBOARD_PATH
    from test_reporting import change_cycle

    from energy_monitor.anomaly import AnomalyPrediction, CycleAnomalyModel
    from energy_monitor.reporting import build_report
    store, ids = seeded_cycles(tmp_path)
    change_cycle(store, ids[0], data_origin="sensor")
    with store._connect() as connection:
        connection.execute("UPDATE telemetry SET firmware_version = 'pzem-test-fixture'")
    model = CycleAnomalyModel(metadata={"status": "trained", "model_version": "fixture-model"})
    prediction = AnomalyPrediction(status="scored", data_origin="sensor", training_status="trained",
        anomaly_score=97, raw_decision=-.1, classification="anomalous",
        model_version="fixture-model",
        comparisons={"average_power_w": {"value": 2050, "baseline_median": 1900,
                                        "delta": 150, "percent_delta": 7.8947}})
    monkeypatch.setattr(model, "predict", lambda _: prediction)
    store.save_report(build_report(store, profile_id="tefal-kettle", device_id="monitor-001",
                                   cycle_id=ids[0], anomaly_model=model))
    monkeypatch.setenv("ENERGY_MONITOR_SOURCE", "local")
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(tmp_path / "reports.db"))
    monkeypatch.setattr("energy_monitor.report_narrative.run_codex", lambda _: pytest.fail("AI"))
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=15)
    app.session_state["mode"] = "Reports"
    app.run()
    assert not app.exception
    assert any(m.label == "Anomaly score" and m.value == "97.0 / 100" for m in app.metric)
    assert any("probability of equipment failure" in c.value for c in app.caption)
    app.run()
    assert len(store.fetch_reports()) == 1


def test_local_ack_does_not_wait_for_report_work(tmp_path, monkeypatch):
    import threading
    from datetime import UTC, datetime

    import httpx
    from test_api import start_test_server_with_store

    from energy_monitor.simulator import batch_readings, generate_cycle_scenario
    from energy_monitor.storage import SQLiteTelemetryStore
    release = threading.Event()
    entered = threading.Event()
    def blocked(*_args, **_kwargs):
        entered.set()
        release.wait(3)
    monkeypatch.setattr("energy_monitor.report_worker.handle_completed_cycle", blocked)
    store = SQLiteTelemetryStore(tmp_path / "ack.db")
    server, url = start_test_server_with_store(store)
    try:
        for batch in batch_readings(generate_cycle_scenario("normal", datetime(2026, 11, 1,
                                                                             tzinfo=UTC))):
            response = httpx.post(url + "/api/v1/telemetry/batches", timeout=1,
                json=batch.model_dump(mode="json"),
                headers={"Idempotency-Key": str(batch.batch_id)})
            assert response.status_code == 201
        assert entered.wait(1)
    finally:
        release.set()
        server.shutdown()
        server.server_close()


def test_twenty_scored_fixture_cycles_fit_bounded_provider_contract(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from energy_monitor.anomaly import AnomalyPrediction, CycleAnomalyModel
    from energy_monitor.models import CycleDetectionSettings
    from energy_monitor.report_narrative import narrative_catalog
    from energy_monitor.reporting import build_report
    from energy_monitor.simulator import batch_readings, generate_cycle_scenario
    from energy_monitor.storage import SQLiteTelemetryStore
    store = SQLiteTelemetryStore(tmp_path / "twenty-fixtures.db")
    for i in range(20):
        readings = [r.model_copy(update={"sample_sequence": r.sample_sequence + i * 1000,
                    "firmware_version": "pzem-test-fixture"})
                    for r in generate_cycle_scenario("normal", datetime(2026, 11, 1, tzinfo=UTC)
                                                     + timedelta(minutes=5 * i))]
        for batch in batch_readings(readings):
            store.insert_batch(batch)
    store.process_cycles(CycleDetectionSettings.simulation_defaults())
    # Fixture-only model output double, never a trained production model.
    model = CycleAnomalyModel(metadata={"status": "trained", "model_version": "fixture-model"})
    def prediction(features):
        return AnomalyPrediction(status="scored", data_origin="sensor", training_status="trained",
            anomaly_score=97.1234567, raw_decision=-.1234567, classification="anomalous",
            model_version="fixture-model", comparisons={key: {
                "value": value, "baseline_median": value * .9,
                "delta": value * .1, "percent_delta": 11.11111111111111 if value else None}
                for key, value in features.values.items()})
    monkeypatch.setattr(model, "predict", prediction)
    report = build_report(store, profile_id="tefal-kettle", device_id="monitor-001",
                          cycle_count=20, anomaly_model=model)
    package = narrative_catalog(report)
    assert len(package["context"]["model_findings"]) == 20
    assert len(json.dumps(package, ensure_ascii=True)) < 18000
