from datetime import UTC, datetime, timedelta

from energy_monitor.simulator import batch_readings, generate_readings


def test_simulator_emits_five_second_samples_with_monotonic_energy() -> None:
    start = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)

    readings = generate_readings(count=13, start_at=start, seed=17)

    assert [reading.timestamp for reading in readings] == [
        start + timedelta(seconds=index * 5) for index in range(13)
    ]
    assert all(
        later.cumulative_energy_kwh >= earlier.cumulative_energy_kwh
        for earlier, later in zip(readings, readings[1:], strict=False)
    )
    assert all(reading.firmware_version == "sim-0.1.0" for reading in readings)


def test_simulator_batches_at_most_six_readings() -> None:
    readings = generate_readings(
        count=13,
        start_at=datetime(2026, 9, 12, 6, 0, tzinfo=UTC),
        seed=23,
    )

    batches = batch_readings(readings)

    assert [len(batch.readings) for batch in batches] == [6, 6, 1]
    assert len({batch.batch_id for batch in batches}) == 3


def test_simulator_marks_only_extended_heating_samples_as_synthetic_anomaly() -> None:
    readings = generate_readings(
        count=80,
        start_at=datetime(2026, 9, 12, 6, 0, tzinfo=UTC),
        seed=31,
    )

    anomaly_readings = [reading for reading in readings if reading.anomaly_status == "anomaly"]

    assert anomaly_readings
    assert all(reading.appliance_state == "heating" for reading in anomaly_readings)

