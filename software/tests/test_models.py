from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from energy_monitor.models import TelemetryBatch, TelemetryReading


def valid_reading(**overrides: object) -> dict[str, object]:
    reading: dict[str, object] = {
        "device_id": "monitor-001",
        "profile_id": "tefal-kettle",
        "timestamp": "2026-09-12T04:00:00Z",
        "sample_sequence": 42,
        "device_uptime_ms": 210_000,
        "voltage_v": 239.4,
        "current_a": 8.62,
        "active_power_w": 2058.0,
        "cumulative_energy_kwh": 1.2456,
        "frequency_hz": 50.0,
        "power_factor": 0.997,
        "appliance_state": "heating",
        "battery_voltage_v": 3.91,
        "connection_state": "online",
        "anomaly_status": "not_evaluated",
        "quality_status": "valid",
        "firmware_version": "sim-0.1.0",
    }
    reading.update(overrides)
    return reading


def test_valid_reading_normalizes_timestamp_to_utc() -> None:
    reading = TelemetryReading.model_validate(valid_reading())

    assert reading.timestamp == datetime(2026, 9, 12, 4, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("voltage_v", -1),
        ("current_a", -0.1),
        ("active_power_w", -5),
        ("cumulative_energy_kwh", -0.001),
        ("frequency_hz", 90),
        ("power_factor", 1.1),
        ("battery_voltage_v", 5.5),
    ],
)
def test_invalid_electrical_ranges_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        TelemetryReading.model_validate(valid_reading(**{field: value}))


def test_batch_requires_one_to_six_readings() -> None:
    with pytest.raises(ValidationError):
        TelemetryBatch.model_validate(
            {"batch_id": "f04a9d04-fb55-4b1d-b777-13f47186b3dd", "readings": []}
        )

    batch = TelemetryBatch.model_validate(
        {
            "batch_id": "f04a9d04-fb55-4b1d-b777-13f47186b3dd",
            "readings": [valid_reading(sample_sequence=index) for index in range(6)],
        }
    )
    assert len(batch.readings) == 6

    with pytest.raises(ValidationError):
        TelemetryBatch.model_validate(
            {
                "batch_id": "f04a9d04-fb55-4b1d-b777-13f47186b3dd",
                "readings": [valid_reading(sample_sequence=index) for index in range(7)],
            }
        )


def test_batch_rejects_mixed_devices() -> None:
    with pytest.raises(ValidationError, match="one device"):
        TelemetryBatch.model_validate(
            {
                "batch_id": "f04a9d04-fb55-4b1d-b777-13f47186b3dd",
                "readings": [valid_reading(), valid_reading(device_id="monitor-002")],
            }
        )
