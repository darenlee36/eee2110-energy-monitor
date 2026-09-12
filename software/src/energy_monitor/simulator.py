from __future__ import annotations

import argparse
import random
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

import httpx

from energy_monitor.models import TelemetryBatch, TelemetryReading

SAMPLE_INTERVAL_SECONDS = 5
UPLOAD_BATCH_SIZE = 6
ScenarioName = Literal["normal", "incomplete_gap", "short_spike"]


def _scenario_from_powers(
    powers: list[float],
    elapsed_seconds: list[int],
    start_at: datetime,
    starting_energy_kwh: float = 0.125,
) -> list[TelemetryReading]:
    if start_at.tzinfo is None or start_at.utcoffset() is None:
        raise ValueError("start_at must include a timezone")
    energy_kwh = starting_energy_kwh
    readings: list[TelemetryReading] = []
    previous_seconds = 0
    for index, (power_w, seconds) in enumerate(
        zip(powers, elapsed_seconds, strict=True),
        start=1,
    ):
        interval = 5 if index == 1 else seconds - previous_seconds
        energy_kwh += power_w * interval / 3_600_000
        previous_seconds = seconds
        heating = power_w > 0
        readings.append(
            TelemetryReading.model_validate(
                {
                    "device_id": "monitor-001",
                    "profile_id": "tefal-kettle",
                    "timestamp": start_at.astimezone(UTC) + timedelta(seconds=seconds),
                    "sample_sequence": index,
                    "device_uptime_ms": seconds * 1000,
                    "voltage_v": 240.0,
                    "current_a": round(power_w / 240.0, 3),
                    "active_power_w": power_w,
                    "cumulative_energy_kwh": round(energy_kwh, 6),
                    "frequency_hz": 50.0,
                    "power_factor": 0.998 if heating else 0.0,
                    "appliance_state": "heating" if heating else "off",
                    "battery_voltage_v": 4.0,
                    "connection_state": "online",
                    "anomaly_status": "normal" if heating else "not_evaluated",
                    "quality_status": "valid",
                    "firmware_version": "sim-cycle-0.1.0",
                }
            )
        )
    return readings


def generate_cycle_scenario(
    name: ScenarioName,
    start_at: datetime,
) -> list[TelemetryReading]:
    """Return deterministic idle/heating/idle telemetry at five-second intervals."""
    if name == "normal":
        powers = [0.0] * 3 + [2050.0] * 30 + [0.0] * 3
        elapsed = [index * SAMPLE_INTERVAL_SECONDS for index in range(len(powers))]
    elif name == "incomplete_gap":
        powers = [0.0] * 3 + [2050.0] * 12 + [0.0] * 3
        elapsed = [index * SAMPLE_INTERVAL_SECONDS for index in range(15)]
        elapsed.extend([135, 140, 145])
    elif name == "short_spike":
        powers = [0.0] * 3 + [2050.0] + [0.0] * 4
        elapsed = [index * SAMPLE_INTERVAL_SECONDS for index in range(len(powers))]
    else:
        raise ValueError(f"unsupported scenario: {name}")
    return _scenario_from_powers(powers, elapsed, start_at)


def generate_readings(
    count: int,
    start_at: datetime,
    seed: int = 2110,
    starting_energy_kwh: float = 0.125,
) -> list[TelemetryReading]:
    if count < 1:
        raise ValueError("count must be at least 1")
    if start_at.tzinfo is None or start_at.utcoffset() is None:
        raise ValueError("start_at must include a timezone")

    rng = random.Random(seed)
    energy_kwh = starting_energy_kwh
    readings: list[TelemetryReading] = []

    for index in range(count):
        phase = index % 72
        is_heating = 6 <= phase <= 29 or 42 <= phase <= 71
        is_extended_anomaly = 66 <= phase <= 71
        voltage_v = round(239.0 + rng.uniform(-1.2, 1.2), 2)
        frequency_hz = round(50.0 + rng.uniform(-0.04, 0.04), 2)
        battery_voltage_v = round(max(3.72, 4.08 - index * 0.0014), 3)

        if is_heating:
            active_power_w = round(2050.0 + rng.uniform(-32.0, 32.0), 2)
            power_factor = round(0.997 + rng.uniform(-0.002, 0.002), 3)
            current_a = round(active_power_w / (voltage_v * power_factor), 3)
            appliance_state = "heating"
            anomaly_status = "anomaly" if is_extended_anomaly else "normal"
        else:
            active_power_w = 0.0
            power_factor = 0.0
            current_a = 0.0
            appliance_state = "off"
            anomaly_status = "not_evaluated"

        energy_kwh += active_power_w * SAMPLE_INTERVAL_SECONDS / 3_600_000
        readings.append(
            TelemetryReading.model_validate(
                {
                    "device_id": "monitor-001",
                    "profile_id": "tefal-kettle",
                    "timestamp": start_at.astimezone(UTC)
                    + timedelta(seconds=index * SAMPLE_INTERVAL_SECONDS),
                    "sample_sequence": index + 1,
                    "device_uptime_ms": index * SAMPLE_INTERVAL_SECONDS * 1000,
                    "voltage_v": voltage_v,
                    "current_a": current_a,
                    "active_power_w": active_power_w,
                    "cumulative_energy_kwh": round(energy_kwh, 6),
                    "frequency_hz": frequency_hz,
                    "power_factor": power_factor,
                    "appliance_state": appliance_state,
                    "battery_voltage_v": battery_voltage_v,
                    "connection_state": "online",
                    "anomaly_status": anomaly_status,
                    "quality_status": "valid",
                    "firmware_version": "sim-0.1.0",
                }
            )
        )
    return readings


def batch_readings(readings: Iterable[TelemetryReading]) -> list[TelemetryBatch]:
    materialized = list(readings)
    return [
        TelemetryBatch(batch_id=uuid4(), readings=materialized[index : index + UPLOAD_BATCH_SIZE])
        for index in range(0, len(materialized), UPLOAD_BATCH_SIZE)
    ]


def upload_batches(url: str, batches: Iterable[TelemetryBatch]) -> tuple[int, int]:
    accepted = 0
    duplicates = 0
    with httpx.Client(timeout=10.0) as client:
        for batch in batches:
            response = client.post(
                url,
                headers={"Idempotency-Key": str(batch.batch_id)},
                json=batch.model_dump(mode="json"),
            )
            response.raise_for_status()
            result = response.json()["data"]
            accepted += int(result["accepted"])
            duplicates += int(result["duplicates"])
    return accepted, duplicates


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload synthetic kettle telemetry")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000/api/v1/telemetry/batches",
    )
    parser.add_argument("--count", type=int, default=80)
    parser.add_argument("--seed", type=int, default=2110)
    parser.add_argument(
        "--scenario",
        choices=("normal", "incomplete_gap", "short_spike", "stream"),
        default="normal",
    )
    args = parser.parse_args()

    if args.scenario == "stream":
        start_at = datetime.now(UTC) - timedelta(seconds=(args.count - 1) * 5)
        readings = generate_readings(args.count, start_at, args.seed)
    else:
        scenario_lengths = {"normal": 36, "incomplete_gap": 18, "short_spike": 8}
        count = scenario_lengths[args.scenario]
        start_at = datetime.now(UTC) - timedelta(seconds=(count - 1) * 5)
        readings = generate_cycle_scenario(args.scenario, start_at)
    batches = batch_readings(readings)
    accepted, duplicates = upload_batches(args.url, batches)
    print(
        f"Uploaded {len(batches)} batches: {accepted} accepted readings, "
        f"{duplicates} duplicate readings"
    )


if __name__ == "__main__":
    main()
