"""Cadence quality uses measurement time, never HTTP/cloud arrival time."""
from datetime import timedelta

import pytest
from test_cycles import SETTINGS, reading, scenario_readings

from dashboard.components import build_electrical_figure, build_report_trace, cycle_energy_points
from energy_monitor.anomaly import extract_cycle_features
from energy_monitor.cycles import detect_cycles, summarize_cycle
from energy_monitor.metrics import sampling_gap_counts
from energy_monitor.models import CycleStatus


def trace(cadence=3, firmware="pzem-fw-0.2.7"):
    return [r.model_copy(update={
        "timestamp": r.timestamp + timedelta(seconds=i * (cadence - 5)),
        "device_uptime_ms": i * cadence * 1000, "firmware_version": firmware,
    }) for i, r in enumerate(scenario_readings(3, 30, 3, 1800))]


@pytest.mark.parametrize("cadence,firmware", [
    (3, "pzem-fw-0.2.6"), (3, "pzem-fw-0.2.7"),
    (5, "pzem-fw-0.2.5"), (5, "sim-0.1.0"), (3, "unknown-fixture"),
])
def test_regular_intervals_and_one_missing_reading(cadence, firmware):
    values = trace(cadence, firmware)
    assert not any(sampling_gap_counts(values))
    cycle, = detect_cycles(values, SETTINGS)
    missing = values[:12] + values[13:]
    changed, = detect_cycles(missing, SETTINGS)
    assert changed.gap_count == 1
    assert changed.missing_sample_count == 1
    for field in ("started_at", "ended_at", "duration_seconds", "meter_energy_kwh"):
        assert getattr(cycle, field) == getattr(changed, field)
    assert sampling_gap_counts(missing)[12] == 1


def test_source_transition_clock_and_sequence_reset_are_not_false_loss():
    a = reading(1, 0, 0, 1).model_copy(update={"firmware_version": "pzem-fw-0.2.5"})
    b = reading(2, 5, 0, 1).model_copy(update={"firmware_version": "pzem-fw-0.2.7"})
    c = reading(3, 8, 0, 1).model_copy(update={"firmware_version": "pzem-fw-0.2.7"})
    assert sampling_gap_counts([a, b, c]) == [0, 0, 0]
    other = c.model_copy(update={"device_id": "other", "sample_sequence": 999})
    assert sampling_gap_counts([a, other]) == [0, 0]
    synthetic = c.model_copy(update={"firmware_version": "sim-0.1.0"})
    assert sampling_gap_counts([a, synthetic]) == [0, 0]
    reset = c.model_copy(update={"sample_sequence": 1, "device_uptime_ms": 0})
    assert sampling_gap_counts([b, reset]) == [0, 0]


def test_delayed_cloud_receipt_does_not_change_quality_or_old_report():
    values = trace()
    cycle, = detect_cycles(values, SETTINGS)
    raw = [r.model_dump(mode="json") for r in values
           if cycle.start_sequence <= r.sample_sequence <= cycle.end_sequence]
    stored = cycle.model_dump(mode="json")
    before = extract_cycle_features(stored, raw)
    delayed = [{**r, "received_at": "2027-01-01T00:00:00Z"} for r in raw]
    assert extract_cycle_features(stored, delayed) == before
    damaged = raw[:10] + raw[11:]
    # Even if a historical stored summary missed this six-second gap, raw extraction rejects it.
    assert "irregular_sampling" in extract_cycle_features(stored, damaged).issues
    assert stored == cycle.model_dump(mode="json")


def test_charts_break_at_three_second_missing_slot_without_interpolating():
    values = trace()
    values.pop(12)
    figure = build_electrical_figure(values, "Current", elapsed=True)
    assert None in figure.data[0].y
    _, energy = cycle_energy_points(values, 0.1)
    assert None in energy
    cycle = summarize_cycle(values, None, SETTINGS, CycleStatus.COMPLETED, "Fixture")
    report = {**cycle.model_dump(mode="json"),
              "readings": [r.model_dump(mode="json") for r in values]}
    assert None in build_report_trace(report, "Power").data[0].y


@pytest.mark.parametrize("seconds,firmware,invalid", [
    ([0, 3, 9], "pzem-fw-0.2.7", True),
    ([0, 3, 9], "pzem-fw-0.2.7", False),
    ([0, 3, 6], "pzem-fw-0.2.7", True),
    ([0, 5, 10], "pzem-fw-0.2.5", True),
])
def test_live_power_gap_markers_do_not_misalign_quality_markers(
    tmp_path, seconds, firmware, invalid,
):
    from dashboard.components import build_live_power_figure, malaysia_time
    from energy_monitor.dashboard_queries import get_live_view
    from energy_monitor.models import QualityStatus
    from energy_monitor.storage import SQLiteTelemetryStore

    values = [reading(i + 1, t, 10, 1).model_copy(update={"firmware_version": firmware})
              for i, t in enumerate(seconds)]
    if invalid:
        values[-1].quality_status = QualityStatus.READ_ERROR
    view = get_live_view(SQLiteTelemetryStore(tmp_path / "empty.db"), values[-1].timestamp)
    view = view.model_copy(update={"recent_readings": values})
    figure = build_live_power_figure(view, 1000)
    assert (None in figure.data[0].y) is (seconds == [0, 3, 9])
    assert figure.data[0].connectgaps is False
    markers = [t for t in figure.data if t.name == "Invalid/missing sample"]
    assert len(markers) == int(invalid)
    if invalid:
        assert list(markers[0].x) == [malaysia_time(values[-1].timestamp)]
        assert list(markers[0].y) == [values[-1].active_power_w]
