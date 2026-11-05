"""Replay the 4 November R1B cloud receipts without a device, network or AI."""

import csv
from datetime import UTC, datetime, timedelta
from itertools import groupby
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from dashboard.components import build_electrical_figure, build_report_trace, live_status
from energy_monitor.cloud_edits import SupabaseCycleStore
from energy_monitor.dashboard_queries import get_live_view
from energy_monitor.reporting import generate_missing_reports
from energy_monitor.supabase_sync import (
    SupabaseConfig,
    SupabaseSyncError,
    sync_supabase_telemetry,
)

DEVICE = "sim-esp32-u3-20261102-a"
PROFILE = "tefal-kettle"
CYCLE = "c57da0c5-4c27-5412-8876-714c93da7f30"
CONFIG = SupabaseConfig("https://r1b.invalid", "test-only")


def test_real_batch_replay_and_numerical_report(tmp_path, monkeypatch):
    path = Path(__file__).parent / "fixtures/r1b-20261104-arrivals.csv"
    with path.open(encoding="utf-8-sig", newline="") as fixture:
        rows = list(csv.DictReader(fixture))
    for row in rows:
        row["recorded_at"] = datetime.fromisoformat(row["recorded_at"]).isoformat()
        for key in ("sample_sequence", "device_uptime_ms"):
            row[key] = int(row[key])
        for key in ("voltage_v", "current_a", "active_power_w", "cumulative_energy_kwh",
                    "frequency_hz", "power_factor"):
            row[key] = float(row[key])
        row.update(
            id=str(UUID(int=row["sample_sequence"])), device_id=DEVICE, profile_id=PROFILE,
            firmware_version="pzem-fw-0.2.5", appliance_state="unknown",
            connection_state="online", anomaly_status="not_evaluated", battery_voltage_v=None,
        )
    store = SupabaseCycleStore(tmp_path / "cache.db", CONFIG, can_edit=True)

    class ReplayClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return observed.astimezone(tz)

    monkeypatch.setattr("energy_monitor.supabase_sync.datetime", ReplayClock)
    for received_at, batch in groupby(rows, lambda row: row["received_at"]):
        # The dashboard first sees each batch at a three-second polling tick.
        arrival = datetime.fromisoformat(received_at)
        observed = datetime.fromtimestamp((int(arrival.timestamp()) // 3 + 1) * 3, UTC)
        page = list(batch)
        with httpx.Client(transport=httpx.MockTransport(
            lambda _, page=page: httpx.Response(200, json=page)
        )) as client:
            assert sync_supabase_telemetry(store, CONFIG, client=client) == 6
        view = get_live_view(store, observed, source_filter="sensor")
        assert view.received_at == arrival
        if page[-1]["sample_sequence"] in (63000006, 63000012, 63000030, 63000036):
            assert view.state == {
                63000006: "idle", 63000012: "heating",
                63000030: "finalizing", 63000036: "cycle_complete",
            }[page[-1]["sample_sequence"]]
            assert live_status(view)[0] == {
                "idle": "Ready", "heating": "Active Load Detected",
                "finalizing": "Finalizing", "cycle_complete": "Cycle Complete",
            }[view.state]
        if view.state == "cycle_complete" and page[-1]["sample_sequence"] == 63000036:
            first_confirmation = observed
            assert view.last_cycle.ended_at.isoformat() == "2026-11-04T08:09:19+00:00"
            assert view.last_cycle.energy_kwh == pytest.approx(0.047)
        if page[-1]["sample_sequence"] >= 63000048:
            # The R1B replay includes a low-power charging tail after the kettle cycle.
            assert view.state == ("idle" if page[-1]["sample_sequence"] == 63000066
                                  else "monitoring")

    store = SupabaseCycleStore(store.database_path, CONFIG, can_edit=True)
    assert store.count_readings() == 78
    assert len(store.fetch_cycles()) == 1
    cycle = store.fetch_cycle(CYCLE)
    assert cycle["completion_seen_at"] == first_confirmation.isoformat()
    assert cycle["meter_energy_kwh"] == pytest.approx(0.047)
    assert cycle["integrated_energy_kwh"] == pytest.approx(0.04414764)
    stale = get_live_view(store, observed + timedelta(seconds=121))
    assert stale.state == "unconfirmed"
    assert live_status(stale)[0] == "Offline"
    for metric, field in (("Voltage", "voltage_v"), ("Current", "current_a")):
        figure = build_electrical_figure(stale.recent_readings, metric, elapsed=False)
        assert list(figure.data[0].y) == [getattr(r, field) for r in stale.recent_readings]

    reports = {}

    def rpc(name, payload):
        assert name == "save_completed_sensor_report"
        report = payload["report"]
        evidence = report["evidence_snapshot"]["cycles"][0]
        assert evidence["cycle_id"] == CYCLE
        assert evidence["tariff_estimate"]["status"] == "partial"
        assert evidence["tariff_estimate"]["amount_range_rm"] == ["0.0209", "0.0256"]
        assert evidence["meter_energy_kwh"] == pytest.approx(0.047)
        assert report["engine"] == "template"
        first = report["report_id"] not in reports
        reports.setdefault(report["report_id"], report)
        if first:
            raise ValueError("Lost response after save")
        return report["report_id"]

    def get(url, *, headers, params, timeout):
        rows = list(reports.values())
        if "report_id" in params:
            rows = [r for r in rows if params["report_id"] == "eq." + r["report_id"]]
        return httpx.Response(200, json=rows, request=httpx.Request("GET", url))

    monkeypatch.setattr(SupabaseCycleStore, "_rpc", lambda self, name, payload: rpc(name, payload))
    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(SupabaseSyncError, match="not confirmed"):
        generate_missing_reports(store, profile_id=PROFILE, device_id=DEVICE)
    assert generate_missing_reports(store, profile_id=PROFILE, device_id=DEVICE) == 0
    fresh_cache = SupabaseCycleStore(tmp_path / "fresh.db", CONFIG)
    assert len(fresh_cache.fetch_reports(PROFILE, DEVICE)) == 1
    report = next(iter(reports.values()))
    assert store.save_report(report) == report["report_id"]
    assert len(reports) == 1
    evidence = report["evidence_snapshot"]["cycles"][0]
    expected = rows[10:29]
    for metric, field in (("Power", "active_power_w"), ("Voltage", "voltage_v"),
                          ("Current", "current_a")):
        figure = build_report_trace(evidence, metric)
        assert list(figure.data[0].y) == [row[field] for row in expected]
    energy = build_report_trace(evidence, "Energy")
    assert energy.data[0].y[-1] == pytest.approx(0.047)
