from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean
from uuid import UUID, uuid5

from energy_monitor.models import (
    CycleAssessment,
    CycleDetectionSettings,
    CycleStatus,
    CycleSummary,
    QualityStatus,
    TelemetryReading,
)

CYCLE_NAMESPACE = UUID("7f5795e2-fd31-4cf1-8c68-093061951230")
EXPECTED_SAMPLE_SECONDS = 5


def cycle_uuid(device_id: str, profile_id: str, start_sequence: int) -> UUID:
    identity = f"{device_id}:{profile_id}:{start_sequence}"
    return uuid5(CYCLE_NAMESPACE, identity)


def _is_valid(reading: TelemetryReading) -> bool:
    return reading.quality_status is QualityStatus.VALID


def _meter_energy_kwh(
    samples: Sequence[TelemetryReading],
    predecessor: TelemetryReading | None,
) -> float:
    valid = [sample for sample in samples if _is_valid(sample)]
    if not valid:
        return 0.0
    prefix = [predecessor] if predecessor is not None and _is_valid(predecessor) else []
    checkpoints = prefix + valid
    return sum(
        max(0.0, current.cumulative_energy_kwh - previous.cumulative_energy_kwh)
        for previous, current in zip(checkpoints, checkpoints[1:], strict=False)
    )


def _integrated_energy_kwh(samples: Sequence[TelemetryReading]) -> float:
    valid = [sample for sample in samples if _is_valid(sample)]
    return sum(
        (previous.active_power_w + current.active_power_w)
        / 2
        * (current.timestamp - previous.timestamp).total_seconds()
        / 3_600_000
        for previous, current in zip(valid, valid[1:], strict=False)
    )


def _gap_evidence(
    samples: Sequence[TelemetryReading],
    warning_gap_seconds: int,
    terminal_gap_seconds: int,
) -> tuple[int, int, int]:
    gaps = [
        int((current.timestamp - previous.timestamp).total_seconds())
        for previous, current in zip(samples, samples[1:], strict=False)
    ]
    if terminal_gap_seconds:
        gaps.append(terminal_gap_seconds)
    warning_gaps = [gap for gap in gaps if gap > warning_gap_seconds]
    missing = sum(max(0, gap // EXPECTED_SAMPLE_SECONDS - 1) for gap in warning_gaps)
    return len(warning_gaps), max(gaps, default=0), missing


def summarize_cycle(
    samples: Sequence[TelemetryReading],
    predecessor: TelemetryReading | None,
    settings: CycleDetectionSettings,
    status: CycleStatus,
    quality_note: str,
    terminal_gap_seconds: int = 0,
) -> CycleSummary:
    valid = [sample for sample in samples if _is_valid(sample)]
    if not valid:
        raise ValueError("a cycle requires at least one valid sample")

    meter_energy = _meter_energy_kwh(samples, predecessor)
    integrated_energy = _integrated_energy_kwh(samples)
    if meter_energy:
        difference_ratio = abs(meter_energy - integrated_energy) / meter_energy
    else:
        difference_ratio = 0.0 if integrated_energy == 0 else 1.0

    gap_count, largest_gap, missing_count = _gap_evidence(
        samples,
        settings.warning_gap_seconds,
        terminal_gap_seconds,
    )
    notes = [quality_note]
    if gap_count:
        notes.append(f"{missing_count} reading(s) may be missing")
    if difference_ratio > settings.energy_difference_tolerance:
        notes.append(
            "Energy cross-check differs from the meter result by "
            f"{difference_ratio:.1%}"
        )

    if status is CycleStatus.ACTIVE:
        assessment = CycleAssessment.NOT_EVALUATED
    elif status is CycleStatus.INCOMPLETE:
        assessment = CycleAssessment.INSUFFICIENT_DATA
    elif difference_ratio > settings.energy_difference_tolerance:
        assessment = CycleAssessment.UNUSUAL
    else:
        assessment = CycleAssessment.NORMAL

    first = valid[0]
    last = valid[-1]
    ended_at = None if status is CycleStatus.ACTIVE else last.timestamp
    end_sequence = None if status is CycleStatus.ACTIVE else last.sample_sequence
    return CycleSummary(
        cycle_id=cycle_uuid(first.device_id, first.profile_id, first.sample_sequence),
        device_id=first.device_id,
        profile_id=first.profile_id,
        started_at=first.timestamp,
        ended_at=ended_at,
        start_sequence=first.sample_sequence,
        end_sequence=end_sequence,
        status=status,
        assessment=assessment,
        duration_seconds=int((last.timestamp - first.timestamp).total_seconds()),
        meter_energy_kwh=meter_energy,
        integrated_energy_kwh=integrated_energy,
        energy_difference_ratio=difference_ratio,
        average_power_w=fmean(sample.active_power_w for sample in valid),
        peak_power_w=max(sample.active_power_w for sample in valid),
        average_voltage_v=fmean(sample.voltage_v for sample in valid),
        minimum_voltage_v=min(sample.voltage_v for sample in valid),
        maximum_voltage_v=max(sample.voltage_v for sample in valid),
        average_current_a=fmean(sample.current_a for sample in valid),
        average_power_factor=fmean(sample.power_factor for sample in valid),
        sample_count=len(samples),
        valid_sample_count=len(valid),
        invalid_sample_count=len(samples) - len(valid),
        missing_sample_count=missing_count,
        gap_count=gap_count,
        largest_gap_seconds=largest_gap,
        quality_note="; ".join(notes),
        detection_version=settings.version,
    )


def detect_cycles(
    readings: Sequence[TelemetryReading],
    settings: CycleDetectionSettings,
) -> list[CycleSummary]:
    ordered = sorted(readings, key=lambda item: (item.timestamp, item.sample_sequence))
    cycles: list[CycleSummary] = []
    candidate_start: list[TelemetryReading] = []
    active: list[TelemetryReading] = []
    candidate_stop: list[TelemetryReading] = []
    idle_predecessor: TelemetryReading | None = None
    cycle_predecessor: TelemetryReading | None = None

    for reading in ordered:
        previous = active[-1] if active else idle_predecessor
        gap_seconds = (
            int((reading.timestamp - previous.timestamp).total_seconds())
            if previous is not None
            else 0
        )
        if active and gap_seconds > settings.terminating_gap_seconds:
            cycles.append(
                summarize_cycle(
                    active,
                    cycle_predecessor,
                    settings,
                    CycleStatus.INCOMPLETE,
                    f"Telemetry gap exceeded {settings.terminating_gap_seconds} seconds",
                    terminal_gap_seconds=gap_seconds,
                )
            )
            active = []
            candidate_stop = []
            candidate_start = []

        if not _is_valid(reading):
            if active:
                active.append(reading)
            continue

        if not active:
            if reading.active_power_w >= settings.start_power_w:
                candidate_start.append(reading)
            else:
                candidate_start = []
                idle_predecessor = reading
            if len(candidate_start) == settings.start_confirm_samples:
                active = candidate_start.copy()
                candidate_start = []
                cycle_predecessor = idle_predecessor
            continue

        active.append(reading)
        active_seconds = int((active[-1].timestamp - active[0].timestamp).total_seconds())
        if active_seconds > settings.maximum_duration_seconds:
            cycles.append(
                summarize_cycle(
                    active,
                    cycle_predecessor,
                    settings,
                    CycleStatus.INCOMPLETE,
                    f"Cycle exceeded {settings.maximum_duration_seconds} seconds",
                )
            )
            active = []
            candidate_stop = []
            continue

        if reading.active_power_w < settings.stop_power_w:
            candidate_stop.append(reading)
        else:
            candidate_stop = []
        if len(candidate_stop) == settings.stop_confirm_samples:
            stop_started_at = candidate_stop[0].timestamp
            heating = [sample for sample in active if sample.timestamp < stop_started_at]
            valid_heating = [sample for sample in heating if _is_valid(sample)]
            duration = int(
                (valid_heating[-1].timestamp - valid_heating[0].timestamp).total_seconds()
            )
            if duration >= settings.minimum_duration_seconds:
                cycles.append(
                    summarize_cycle(
                        heating,
                        cycle_predecessor,
                        settings,
                        CycleStatus.COMPLETED,
                        "Cycle completed",
                    )
                )
            idle_predecessor = candidate_stop[-1]
            active = []
            candidate_stop = []

    if active:
        cycles.append(
            summarize_cycle(
                active,
                cycle_predecessor,
                settings,
                CycleStatus.ACTIVE,
                "Cycle is still heating",
            )
        )
    return cycles
