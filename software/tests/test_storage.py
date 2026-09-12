from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from test_models import valid_reading

from energy_monitor.models import CycleDetectionSettings, TelemetryBatch, VolumeClass
from energy_monitor.simulator import batch_readings, generate_readings
from energy_monitor.storage import IdempotencyConflict, SQLiteTelemetryStore


def make_batch(batch_id: str, start_sequence: int = 1) -> TelemetryBatch:
    return TelemetryBatch.model_validate(
        {
            "batch_id": batch_id,
            "readings": [
                valid_reading(sample_sequence=start_sequence + offset) for offset in range(2)
            ],
        }
    )


def test_store_inserts_readings_once_and_replays_same_batch(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "telemetry.db")
    batch = make_batch("11111111-1111-4111-8111-111111111111")

    first = store.insert_batch(batch)
    replay = store.insert_batch(batch)

    assert first.accepted == 2
    assert first.duplicates == 0
    assert first.replayed is False
    assert replay.accepted == 2
    assert replay.duplicates == 0
    assert replay.replayed is True
    assert store.count_readings() == 2


def test_store_rejects_reused_batch_id_with_different_payload(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "telemetry.db")
    batch_id = "22222222-2222-4222-8222-222222222222"
    store.insert_batch(make_batch(batch_id, start_sequence=1))

    with pytest.raises(IdempotencyConflict):
        store.insert_batch(make_batch(batch_id, start_sequence=20))


def test_store_treats_existing_device_sequences_as_duplicates(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "telemetry.db")
    store.insert_batch(make_batch("33333333-3333-4333-8333-333333333333"))

    result = store.insert_batch(make_batch("44444444-4444-4444-8444-444444444444"))

    assert result.accepted == 0
    assert result.duplicates == 2
    assert result.replayed is False


def test_store_returns_recent_readings_in_chart_order(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "telemetry.db")
    store.insert_batch(make_batch("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", start_sequence=10))

    rows = store.fetch_recent_readings(limit=2)

    assert [row["sample_sequence"] for row in rows] == [10, 11]
    assert rows[-1]["active_power_w"] == 2058.0


def store_with_completed_cycle(tmp_path: Path) -> tuple[SQLiteTelemetryStore, str]:
    store = SQLiteTelemetryStore(tmp_path / "cycles.db")
    readings = generate_readings(36, datetime(2026, 9, 12, tzinfo=UTC))
    for batch in batch_readings(readings):
        store.insert_batch(batch)
    cycles = store.process_cycles(CycleDetectionSettings.simulation_defaults())
    return store, str(cycles[0].cycle_id)


def test_processing_cycles_is_idempotent(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "cycles.db")
    readings = generate_readings(36, datetime(2026, 9, 12, tzinfo=UTC))
    for batch in batch_readings(readings):
        store.insert_batch(batch)
    settings = CycleDetectionSettings.simulation_defaults()

    first = store.process_cycles(settings)
    second = store.process_cycles(settings)

    assert first == second
    assert len(store.fetch_cycles()) == 1
    assert store.fetch_cycles()[0]["status"] == "completed"


def test_manual_volume_correction_preserves_audit_history(tmp_path: Path) -> None:
    store, cycle_id = store_with_completed_cycle(tmp_path)

    store.save_volume_label(cycle_id, VolumeClass.ONE_LITRE)
    store.save_volume_label(cycle_id, VolumeClass.HALF_LITRE)

    assert store.fetch_effective_volume(cycle_id) == VolumeClass.HALF_LITRE
    history = store.fetch_volume_label_history(cycle_id)
    assert [row["volume_class"] for row in history] == ["1.0_l", "0.5_l"]
    assert [row["is_active"] for row in history] == [0, 1]


def test_fetch_cycle_and_its_readings(tmp_path: Path) -> None:
    store, cycle_id = store_with_completed_cycle(tmp_path)

    cycle = store.fetch_cycle(cycle_id)
    rows = store.fetch_cycle_readings(cycle_id)

    assert cycle is not None
    assert cycle["cycle_id"] == cycle_id
    assert rows[0]["sample_sequence"] == cycle["start_sequence"]
    assert rows[-1]["sample_sequence"] == cycle["end_sequence"]


def test_volume_filter_uses_active_manual_label(tmp_path: Path) -> None:
    store, cycle_id = store_with_completed_cycle(tmp_path)
    store.save_volume_label(cycle_id, VolumeClass.ONE_LITRE)

    assert len(store.fetch_cycles(volume="1.0_l")) == 1
    assert store.fetch_cycles(volume="0.5_l") == []


def test_volume_label_requires_an_existing_cycle(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "cycles.db")

    with pytest.raises(ValueError, match="cycle does not exist"):
        store.save_volume_label(uuid4(), VolumeClass.ONE_LITRE)
