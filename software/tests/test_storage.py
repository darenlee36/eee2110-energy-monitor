from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from test_models import valid_reading

from energy_monitor.models import (
    CycleDetectionSettings,
    ManualCycleRecord,
    TelemetryBatch,
    VolumeClass,
)
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


def test_manual_cycle_is_auditable_and_has_no_sensor_readings(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "manual.db")

    cycle_id = store.add_manual_cycle(
        ManualCycleRecord(
            started_at=datetime(2026, 9, 12, 2, 30, tzinfo=UTC),
            duration_seconds=180,
            energy_kwh=0.09,
            label="Reference meter log",
            notes="Copied from paper log",
        )
    )

    cycle = store.fetch_cycle(cycle_id)
    assert cycle is not None
    assert cycle["record_source"] == "manual"
    assert cycle["manual_notes"] == "Copied from paper log"
    assert cycle["assessment"] == "not_evaluated"
    assert cycle["custom_label"] == "Reference meter log"
    assert store.fetch_cycle_readings(cycle_id) == []


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


def test_store_accepts_telemetry_without_battery_measurement(tmp_path: Path) -> None:
    batch = make_batch("12121212-1212-4212-8212-121212121212")
    batch.readings[0].battery_voltage_v = None
    store = SQLiteTelemetryStore(tmp_path / "optional-battery.db")

    assert store.insert_batch(batch).accepted == 2
    assert store.fetch_recent_readings()[0]["battery_voltage_v"] is None


def test_volume_filter_uses_active_manual_label(tmp_path: Path) -> None:
    store, cycle_id = store_with_completed_cycle(tmp_path)
    store.save_volume_label(cycle_id, VolumeClass.ONE_LITRE)

    assert len(store.fetch_cycles(volume="1.0_l")) == 1
    assert store.fetch_cycles(volume="0.5_l") == []


def test_volume_label_requires_an_existing_cycle(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "cycles.db")

    with pytest.raises(ValueError, match="cycle does not exist"):
        store.save_volume_label(uuid4(), VolumeClass.ONE_LITRE)


def test_user_managed_cycle_labels_are_reusable_and_case_insensitive(
    tmp_path: Path,
) -> None:
    store, cycle_id = store_with_completed_cycle(tmp_path)

    assert store.add_cycle_label("  Full kettle  ") == "Full kettle"
    assert store.add_cycle_label("full KETTLE") == "Full kettle"
    store.save_cycle_label(cycle_id, "Full kettle")

    assert store.list_cycle_labels() == ["Full kettle"]
    assert store.fetch_cycle_label(cycle_id) == "Full kettle"
    assert store.fetch_cycle(cycle_id)["custom_label"] == "Full kettle"


def test_cycle_label_assignment_preserves_audit_history(tmp_path: Path) -> None:
    store, cycle_id = store_with_completed_cycle(tmp_path)
    store.add_cycle_label("0.5 L")
    store.add_cycle_label("One mug")

    store.save_cycle_label(cycle_id, "0.5 L")
    store.save_cycle_label(cycle_id, "One mug")

    history = store.fetch_cycle_label_history(cycle_id)
    assert [row["label"] for row in history] == ["0.5 L", "One mug"]
    assert [row["is_active"] for row in history] == [0, 1]


def test_archived_cycle_label_is_hidden_but_preserves_records_and_can_return(
    tmp_path: Path,
) -> None:
    store, cycle_id = store_with_completed_cycle(tmp_path)
    store.add_cycle_label("One mug")
    store.save_cycle_label(cycle_id, "One mug")

    store.archive_cycle_label("one MUG")

    assert store.list_cycle_labels() == []
    assert store.fetch_cycle_label(cycle_id) == "One mug"
    assert store.fetch_cycle_label_history(cycle_id)[0]["label"] == "One mug"
    with pytest.raises(ValueError, match="cycle label does not exist"):
        store.save_cycle_label(cycle_id, "One mug")

    assert store.add_cycle_label("ONE MUG") == "One mug"
    assert store.list_cycle_labels() == ["One mug"]


@pytest.mark.parametrize("label", ["", "   ", "x" * 41])
def test_cycle_label_validates_user_input(tmp_path: Path, label: str) -> None:
    store = SQLiteTelemetryStore(tmp_path / "cycles.db")

    with pytest.raises(ValueError, match="between 1 and 40 characters"):
        store.add_cycle_label(label)
