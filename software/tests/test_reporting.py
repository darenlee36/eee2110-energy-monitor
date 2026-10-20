import importlib
import json
import subprocess
import sys
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


def test_report_history_survives_reopen_and_retry(tmp_path):
    store, ids = seeded_cycles(tmp_path)
    report = make_report(store, cycle_id=ids[0])
    report_id = store.save_report(report)
    reopened = SQLiteTelemetryStore(store.database_path)
    assert reopened.fetch_report(report_id) == report.model_dump(mode="json")
    assert reopened.save_report(make_report(reopened, cycle_id=ids[0])) == report_id
    history = reopened.fetch_reports("tefal-kettle", "monitor-001")
    assert len(history) == 1
    assert history[0]["generated_at"] == report.model_dump(mode="json")["generated_at"]
    assert reopened.fetch_reports("tefal-kettle", "other") == []


def test_changed_evidence_and_version_preserve_earlier_reports(tmp_path, monkeypatch):
    store, ids = seeded_cycles(tmp_path)
    first = make_report(store, cycle_id=ids[0])
    store.save_report(first)
    change_cycle(store, ids[0], meter_energy_kwh=0.2)
    second = make_report(store, cycle_id=ids[0])
    store.save_report(second)
    module = importlib.import_module("energy_monitor.reporting")
    monkeypatch.setattr(module, "REPORTING_VERSION", "cycle-report-template-v2-test")
    third = make_report(store, cycle_id=ids[0])
    store.save_report(third)
    assert len({first.report_id, second.report_id, third.report_id}) == 3
    assert len(store.fetch_reports("tefal-kettle", "monitor-001")) == 3
    assert store.fetch_report(str(first.report_id)) == first.model_dump(mode="json")


@pytest.mark.parametrize("tamper", ["text", "snapshot", "device"])
def test_report_storage_rejects_tampered_drafts(tmp_path, tamper):
    store, ids = seeded_cycles(tmp_path)
    report = make_report(store, cycle_id=ids[0])
    if tamper == "text":
        report = report.model_copy(update={"report_text": "invented report"})
    elif tamper == "device":
        report = report.model_copy(update={"device_id": "other"})
    else:
        report.evidence_snapshot["model"]["status"] = "trained"
    with pytest.raises(ValueError, match="evidence"):
        store.save_report(report)
    assert store.fetch_reports("tefal-kettle", "monitor-001") == []


def test_missing_cycle_sweep_is_recoverable_and_idempotent(tmp_path):
    store, ids = seeded_cycles(tmp_path, 5)
    store.save_report(make_report(store, cycle_id=ids[0]))
    module = importlib.import_module("energy_monitor.reporting")
    assert module.generate_missing_reports(
        store, profile_id="tefal-kettle", device_id="monitor-001"
    ) == 4
    assert module.generate_missing_reports(
        store, profile_id="tefal-kettle", device_id="monitor-001"
    ) == 0
    assert len(store.fetch_reports("tefal-kettle", "monitor-001")) == 5
    assert module.generate_missing_reports(
        store, profile_id="tefal-kettle", device_id="other"
    ) == 0


@pytest.mark.parametrize("limit", [0, -1, 1001, True])
def test_report_history_limit_is_bounded(tmp_path, limit):
    store, _ = seeded_cycles(tmp_path)
    with pytest.raises(ValueError, match="limit"):
        store.fetch_reports("tefal-kettle", "monitor-001", limit=limit)


def test_report_history_is_viewable_without_raw_cycles(tmp_path):
    store, ids = seeded_cycles(tmp_path)
    report = make_report(store, cycle_id=ids[0])
    store.save_report(report)
    with closing(store._connect()) as connection, connection:
        connection.execute("DELETE FROM appliance_cycles")
    assert store.fetch_reports() == [report.model_dump(mode="json")]
    with pytest.raises(ValueError, match="scope"):
        store.fetch_reports(profile_id="tefal-kettle")


def test_cli_generates_missing_reports_and_replays(tmp_path, monkeypatch, capsys):
    store, _ = seeded_cycles(tmp_path, 5)
    monkeypatch.setenv("ENERGY_MONITOR_SOURCE", "local")
    module = importlib.import_module("energy_monitor.reporting")
    args = ["--database", str(store.database_path), "--profile-id", "tefal-kettle",
            "--device-id", "monitor-001", "--generate-missing"]
    assert module.main(args) == 0
    assert "5 new" in capsys.readouterr().out
    assert module.main(args) == 0
    assert "0 new" in capsys.readouterr().out
    assert len(store.fetch_reports()) == 5


@pytest.mark.parametrize("args", [[], ["--cycle-count", "2"],
    ["--cycle-count", "5", "--generate-missing"],
    ["--cycle-id", "invalid", "--cycle-count", "5"]])
def test_cli_rejects_invalid_argument_combinations(args):
    module = importlib.import_module("energy_monitor.reporting")
    with pytest.raises(SystemExit) as error:
        module.main(["--profile-id", "tefal-kettle", "--device-id", "monitor-001", *args])
    assert error.value.code == 2


def test_cli_cloud_syncs_all_pages_before_generating(tmp_path, monkeypatch, capsys):
    import energy_monitor.cloud_edits as cloud
    import energy_monitor.supabase_sync as sync

    store, _ = seeded_cycles(tmp_path)
    monkeypatch.setenv("ENERGY_MONITOR_SOURCE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "private-test-key")
    events = []

    def sync_page(cache, config):
        events.append("sync")
        # A page can advance the cursor while importing only duplicate telemetry.
        if len(events) == 1:
            cache.save_cloud_sync_cursor(config.url, "2026-10-01T00:00:00+00:00", str(UUID(int=1)))
        return 0

    monkeypatch.setattr(sync, "sync_supabase_telemetry", sync_page)
    monkeypatch.setattr(cloud.SupabaseCycleStore, "refresh_edits", lambda s: events.append("edits"))
    monkeypatch.setattr(cloud.SupabaseCycleStore, "fetch_report", lambda *a: None)

    def save(cache, report):
        assert cache.can_edit
        events.append("save")
        return SQLiteTelemetryStore.save_report(cache, report)

    monkeypatch.setattr(cloud.SupabaseCycleStore, "save_report", save)
    module = importlib.import_module("energy_monitor.reporting")
    assert module.main(["--database", str(store.database_path), "--profile-id", "tefal-kettle",
                        "--device-id", "monitor-001", "--generate-missing"]) == 0
    assert events == ["sync", "sync", "edits", "save"]
    assert "private-test-key" not in str(capsys.readouterr())


def test_module_worker_is_duplicate_safe_without_a_browser(tmp_path, monkeypatch):
    store, ids = seeded_cycles(tmp_path, 5)
    monkeypatch.setenv("ENERGY_MONITOR_SOURCE", "local")
    command = [sys.executable, "-m", "energy_monitor.reporting", "--database",
               str(store.database_path), "--profile-id", "tefal-kettle", "--device-id",
               "monitor-001"]
    for expected in ("5 new", "0 new"):
        result = subprocess.run([*command, "--generate-missing"], capture_output=True,
                                text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert expected in result.stdout
    for action in (["--cycle-id", ids[0]], ["--cycle-count", "5"]):
        result = subprocess.run([*command, *action], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
    assert len(store.fetch_reports()) == 6


def test_readable_report_preserves_cycle_quality_warning_and_v1_history(tmp_path, monkeypatch):
    store, ids = seeded_cycles(tmp_path)
    warning = "Energy cross-check differs from the meter result by 30.0%"
    change_cycle(store, ids[0], quality_note=warning, assessment="unusual")
    module = importlib.import_module("energy_monitor.reporting")
    with monkeypatch.context() as old:
        old.setattr(module, "REPORTING_VERSION", "cycle-report-template-v1")
        previous = make_report(store, cycle_id=ids[0])
        store.save_report(previous)
    report = make_report(store, cycle_id=ids[0])
    assert report.evidence_snapshot["excluded_quality_count"] == 0
    assert report.evidence_snapshot["totals_by_source"]["simulated"]["energy_kwh"] == "0.1"
    assert warning in report.report_text
    assert ids[0] in report.report_text
    assert "unusual" in report.report_text
    assert "rule-based, not ML" in report.report_text
    assert report.report_id != previous.report_id
    assert store.fetch_report(str(previous.report_id)) == previous.model_dump(mode="json")
    store.save_report(report)
    assert len(store.fetch_reports()) == 2


def test_report_quality_notes_cannot_embed_markdown_images(tmp_path):
    store, ids = seeded_cycles(tmp_path)
    change_cycle(store, ids[0], quality_note="![unsafe](https://example.invalid/image)")
    report = make_report(store, cycle_id=ids[0])
    assert "![unsafe](" not in report.report_text
    assert "unsafe" in report.report_text
