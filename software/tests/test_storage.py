from pathlib import Path

import pytest
from test_models import valid_reading

from energy_monitor.models import TelemetryBatch
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
