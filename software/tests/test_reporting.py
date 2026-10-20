import importlib
import json
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from energy_monitor.models import CycleDetectionSettings
from energy_monitor.simulator import batch_readings, generate_cycle_scenario
from energy_monitor.storage import SQLiteTelemetryStore


def seeded_cycles(tmp_path: Path, count: int = 1):
    store = SQLiteTelemetryStore(tmp_path / "reports.db")
    readings = generate_cycle_scenario("normal", datetime(2026, 10, 1, tzinfo=UTC))
    for batch in batch_readings(readings):
        store.insert_batch(batch)
    store.process_cycles(CycleDetectionSettings.simulation_defaults())
    base = store.fetch_cycles()[0]
    ids = []
    with closing(store._connect()) as connection, connection:
        connection.execute("DELETE FROM appliance_cycles")
        for index in range(count):
            value = dict(base)
            value["cycle_id"] = str(UUID(int=index + 1))
            for name in ("started_at", "ended_at"):
                value[name] = (
                    datetime.fromisoformat(str(base[name])) + timedelta(minutes=5 * index)
                ).isoformat()
            value["start_sequence"] = 1000 * index + int(base["start_sequence"])
            value["end_sequence"] = 1000 * index + int(base["end_sequence"])
            value["meter_energy_kwh"] = 0.1
            value["integrated_energy_kwh"] = 0.1
            connection.execute(
                """INSERT INTO appliance_cycles
                (cycle_id, device_id, profile_id, started_at, ended_at, start_sequence,
                 end_sequence, status, assessment, summary_json, detection_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(value[k] for k in (
                    "cycle_id", "device_id", "profile_id", "started_at", "ended_at",
                    "start_sequence", "end_sequence", "status", "assessment",
                )) + (json.dumps(value), value["detection_version"]),
            )
            ids.append(value["cycle_id"])
    return store, ids


def make_report(store, **kwargs):
    # Import inside the test so an absent feature is a failing test, not collection failure.
    return importlib.import_module("energy_monitor.reporting").build_report(
        store, profile_id="tefal-kettle", device_id="monitor-001", **kwargs
    )


def change_cycle(store, cycle_id, **changes):
    with closing(store._connect()) as connection, connection:
        row = connection.execute(
            "SELECT summary_json FROM appliance_cycles WHERE cycle_id = ?", (cycle_id,)
        ).fetchone()
        summary = json.loads(row[0])
        summary.update(changes)
        connection.execute(
            "UPDATE appliance_cycles SET summary_json = ?, status = ?, ended_at = ? "
            "WHERE cycle_id = ?",
            (json.dumps(summary), summary["status"], summary["ended_at"], cycle_id),
        )


def test_individual_report_traces_cycle_evidence(tmp_path):
    store, ids = seeded_cycles(tmp_path)
    before = store.fetch_cycle(ids[0])
    report = make_report(store, cycle_id=ids[0])
    assert report.kind == "cycle"
    assert report.cycle_count == 1
    assert report.engine == "template"
    assert report.evidence_snapshot["cycles"][0]["cycle_id"] == ids[0]
    assert report.evidence_snapshot["totals_by_source"]["simulated"]["energy_kwh"] == "0.1"
    assert "0.1 kWh" in report.report_text
    assert "simulated" in report.report_text
    assert store.fetch_cycle(ids[0]) == before
    assert make_report(store, cycle_id=ids[0]).report_id == report.report_id


@pytest.mark.parametrize("count", [5, 10, 20])
def test_batch_uses_exact_count(tmp_path, count):
    store, ids = seeded_cycles(tmp_path, 21)
    report = make_report(store, cycle_count=count)
    assert report.kind == "batch"
    assert report.cycle_count == count
    assert [c["cycle_id"] for c in report.evidence_snapshot["cycles"]] == ids[-count:]
    assert report.evidence_snapshot["totals_by_source"]["simulated"]["energy_kwh"] == {
        5: "0.5", 10: "1.0", 20: "2.0",
    }[count]


def test_insufficient_history_is_explicit(tmp_path):
    store, _ = seeded_cycles(tmp_path, 4)
    with pytest.raises(ValueError, match="4 of 5"):
        make_report(store, cycle_count=5)


def test_equal_times_have_stable_order(tmp_path):
    store, ids = seeded_cycles(tmp_path, 6)
    for cycle_id in ids:
        change_cycle(store, cycle_id, ended_at="2026-10-01T02:00:00+00:00")
    first = make_report(store, cycle_count=5)
    assert [c["cycle_id"] for c in first.evidence_snapshot["cycles"]] == ids[1:]
    assert make_report(store, cycle_count=5).evidence_hash == first.evidence_hash


@pytest.mark.parametrize("origin", ["sensor", "unknown", "mixed"])
def test_provenance_is_not_claimed_as_measured(tmp_path, origin):
    store, ids = seeded_cycles(tmp_path)
    change_cycle(store, ids[0], data_origin=origin)
    report = make_report(store, cycle_id=ids[0])
    assert report.evidence_snapshot["cycles"][0]["data_origin"] == origin
    assert "calibration" in report.report_text
    assert "verified sensor consumption" not in report.report_text


def test_untrained_model_is_not_a_healthy_verdict(tmp_path):
    store, ids = seeded_cycles(tmp_path)
    report = make_report(store, cycle_id=ids[0])
    assert report.evidence_snapshot["model"] == {
        "status": "not_trained", "anomaly_evaluation": "not_evaluated",
    }
    assert "not trained" in report.report_text
    assert "healthy" not in report.report_text
    assert "anomaly_score" not in report.evidence_snapshot


def test_active_or_wrong_device_cycle_is_rejected(tmp_path):
    store, ids = seeded_cycles(tmp_path)
    with pytest.raises(ValueError, match="device/profile"):
        importlib.import_module("energy_monitor.reporting").build_report(
            store, profile_id="tefal-kettle", device_id="other", cycle_id=ids[0]
        )
    change_cycle(store, ids[0], status="active", ended_at=None)
    with pytest.raises(ValueError, match="completed"):
        make_report(store, cycle_id=ids[0])


@pytest.mark.parametrize("count", [0, 2, -1, True])
def test_invalid_cycle_count_is_rejected(tmp_path, count):
    store, _ = seeded_cycles(tmp_path)
    with pytest.raises(ValueError, match="count"):
        make_report(store, cycle_count=count)


def test_quality_errors_exclude_energy_and_cost(tmp_path):
    store, ids = seeded_cycles(tmp_path)
    change_cycle(store, ids[0], gap_count=1, largest_gap_seconds=20)
    report = make_report(store, cycle_id=ids[0])
    assert report.evidence_snapshot["excluded_quality_count"] == 1
    assert report.evidence_snapshot["totals_by_source"] == {}
    assert "excluded" in report.report_text


def test_manual_source_remains_entered_data(tmp_path):
    store, ids = seeded_cycles(tmp_path)
    with closing(store._connect()) as connection, connection:
        connection.execute(
            "UPDATE appliance_cycles SET record_source = 'manual', manual_notes = ? "
            "WHERE cycle_id = ?", ("![unsafe](https://example.invalid/image)", ids[0]),
        )
    report = make_report(store, cycle_id=ids[0])
    assert report.evidence_snapshot["cycles"][0]["record_source"] == "manual"
    assert "manual" in report.evidence_snapshot["totals_by_source"]
    assert "![unsafe]" not in report.report_text
