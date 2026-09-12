from __future__ import annotations

import argparse
import random
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

from energy_monitor.models import TelemetryBatch, TelemetryReading

SAMPLE_INTERVAL_SECONDS = 5
UPLOAD_BATCH_SIZE = 6


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
    args = parser.parse_args()

    start_at = datetime.now(UTC) - timedelta(seconds=(args.count - 1) * 5)
    batches = batch_readings(generate_readings(args.count, start_at, args.seed))
    accepted, duplicates = upload_batches(args.url, batches)
    print(
        f"Uploaded {len(batches)} batches: {accepted} accepted readings, "
        f"{duplicates} duplicate readings"
    )


if __name__ == "__main__":
    main()
