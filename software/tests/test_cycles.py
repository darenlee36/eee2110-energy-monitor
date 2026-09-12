from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from energy_monitor.cycles import detect_cycles
from energy_monitor.models import (
    CycleAssessment,
    CycleDetectionSettings,
    CycleStatus,
    QualityStatus,
    TelemetryReading,
)

SETTINGS = CycleDetectionSettings.simulation_defaults()
START = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)


def reading(
    sequence: int,
    seconds: int,
    power_w: float,
    energy_kwh: float,
    quality: QualityStatus = QualityStatus.VALID,
) -> TelemetryReading:
    heating = power_w >= SETTINGS.start_power_w
    return TelemetryReading.model_validate(
        {
            "device_id": "monitor-001",
            "profile_id": "tefal-kettle",
            "timestamp": START + timedelta(seconds=seconds),
            "sample_sequence": sequence,
            "device_uptime_ms": seconds * 1000,
            "voltage_v": 240.0,
            "current_a": power_w / 240.0,
            "active_power_w": power_w,
            "cumulative_energy_kwh": energy_kwh,
            "frequency_hz": 50.0,
            "power_factor": 1.0 if heating else 0.0,
            "appliance_state": "heating" if heating else "off",
            "battery_voltage_v": 4.0,
            "connection_state": "online",
            "anomaly_status": "not_evaluated",
            "quality_status": quality,
            "firmware_version": "sim-0.1.0",
        }
    )


def scenario_readings(
    idle: int,
    heating: int,
    trailing_idle: int,
    power_w: float,
) -> list[TelemetryReading]:
    powers = [0.0] * idle + [power_w] * heating + [0.0] * trailing_idle
    energy = 1.0
    result: list[TelemetryReading] = []
    for index, power in enumerate(powers):
        energy += power * 5 / 3_600_000
        result.append(reading(index + 1, index * 5, power, energy))
    return result


def test_detect_cycles_groups_one_boiling_period_into_one_cycle() -> None:
    readings = scenario_readings(idle=3, heating=30, trailing_idle=3, power_w=2000.0)

    cycles = detect_cycles(readings, SETTINGS)

    assert len(cycles) == 1
    cycle = cycles[0]
    assert cycle.status == CycleStatus.COMPLETED
    assert cycle.start_sequence == 4
    assert cycle.end_sequence == 33
    assert cycle.duration_seconds == 145
    assert cycle.sample_count == 30
    assert cycle.peak_power_w == 2000.0
    assert cycle.meter_energy_kwh == pytest.approx(0.083333, abs=1e-6)


def test_detect_cycles_rejects_one_sample_power_spike() -> None:
    readings = scenario_readings(idle=3, heating=1, trailing_idle=4, power_w=2000.0)

    assert detect_cycles(readings, SETTINGS) == []


def test_long_gap_closes_active_cycle_as_incomplete() -> None:
    readings = scenario_readings(idle=2, heating=8, trailing_idle=0, power_w=2000.0)
    last = readings[-1]
    readings.append(reading(11, 110, 0.0, last.cumulative_energy_kwh))

    cycle = detect_cycles(readings, SETTINGS)[0]

    assert cycle.status is CycleStatus.INCOMPLETE
    assert cycle.assessment is CycleAssessment.INSUFFICIENT_DATA
    assert cycle.largest_gap_seconds == 65


def test_cumulative_meter_reset_never_creates_negative_energy() -> None:
    values = [1.0, 1.01, 0.001, 0.011]
    samples = [reading(index + 1, index * 5, 2000.0, value) for index, value in enumerate(values)]

    cycle = detect_cycles(samples, SETTINGS)[0]

    assert cycle.meter_energy_kwh == pytest.approx(0.02)


def test_cycle_id_is_stable_when_same_readings_are_processed_twice() -> None:
    readings = scenario_readings(idle=3, heating=30, trailing_idle=3, power_w=2000.0)

    first_id = detect_cycles(readings, SETTINGS)[0].cycle_id
    second_id = detect_cycles(readings, SETTINGS)[0].cycle_id

    assert first_id == second_id


def test_energy_cross_check_over_twenty_percent_sets_quality_note() -> None:
    readings = scenario_readings(idle=2, heating=8, trailing_idle=3, power_w=2000.0)
    for item in readings[2:10]:
        item.cumulative_energy_kwh += (item.sample_sequence - 2) * 0.01

    cycle = detect_cycles(readings, SETTINGS)[0]

    assert cycle.energy_difference_ratio > 0.20
    assert "Energy cross-check differs" in cycle.quality_note


@pytest.mark.parametrize(("gap_seconds", "missing"), [(10, 0), (20, 3)])
def test_short_gaps_report_missing_samples_without_ending_cycle(
    gap_seconds: int,
    missing: int,
) -> None:
    readings = scenario_readings(idle=2, heating=8, trailing_idle=3, power_w=2000.0)
    for item in readings[6:]:
        item.timestamp += timedelta(seconds=gap_seconds - 5)

    cycle = detect_cycles(readings, SETTINGS)[0]

    assert cycle.status is CycleStatus.COMPLETED
    assert cycle.missing_sample_count == missing


def test_invalid_samples_do_not_confirm_start_or_stop() -> None:
    readings = scenario_readings(idle=2, heating=8, trailing_idle=5, power_w=2000.0)
    readings[2].quality_status = QualityStatus.READ_ERROR
    readings[10].quality_status = QualityStatus.READ_ERROR

    cycle = detect_cycles(readings, SETTINGS)[0]

    assert cycle.start_sequence == 4
    assert cycle.end_sequence == 10
    assert cycle.status is CycleStatus.COMPLETED


def test_heating_over_maximum_duration_is_incomplete() -> None:
    readings = scenario_readings(idle=2, heating=123, trailing_idle=0, power_w=2000.0)

    cycle = detect_cycles(readings, SETTINGS)[0]

    assert cycle.status is CycleStatus.INCOMPLETE
    assert cycle.duration_seconds == 605
