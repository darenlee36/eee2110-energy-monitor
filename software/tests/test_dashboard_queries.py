from datetime import UTC, datetime
from pathlib import Path

from energy_monitor.dashboard_queries import (
    comparison_text,
    get_cycle_detail,
    get_live_view,
    list_cycle_items,
)
from energy_monitor.models import CycleDetectionSettings, VolumeClass
from energy_monitor.simulator import batch_readings, generate_cycle_scenario
from energy_monitor.storage import SQLiteTelemetryStore


def populated_store(tmp_path: Path, scenario: str = "normal") -> SQLiteTelemetryStore:
    store = SQLiteTelemetryStore(tmp_path / f"{scenario}.db")
    readings = generate_cycle_scenario(
        scenario,  # type: ignore[arg-type]
        datetime(2026, 9, 12, 6, 0, tzinfo=UTC),
    )
    for batch in batch_readings(readings):
        store.insert_batch(batch)
    store.process_cycles(CycleDetectionSettings.simulation_defaults())
    return store


def test_live_view_distinguishes_instantaneous_values_from_cycle_totals(
    tmp_path: Path,
) -> None:
    store = populated_store(tmp_path, scenario="normal")
    view = get_live_view(store, now=datetime(2026, 9, 12, 6, 5, tzinfo=UTC))

    assert view.state in {"idle", "heating", "cycle_complete"}
    assert view.latest_power_w is not None and view.latest_power_w >= 0
    assert view.last_cycle is not None
    assert view.last_cycle.energy_label == "Measured cycle energy"
    assert view.recent_readings
    assert view.cycle_readings
    assert view.recent_readings[-1].timestamp == view.updated_at


def test_empty_live_view_is_explicit(tmp_path: Path) -> None:
    view = get_live_view(
        SQLiteTelemetryStore(tmp_path / "empty.db"),
        now=datetime(2026, 9, 12, 6, 5, tzinfo=UTC),
    )

    assert view.state == "empty"
    assert view.latest_power_w is None
    assert view.quality_note == "No telemetry has been received"
    assert view.recent_readings == []
    assert view.cycle_readings == []


def test_cycle_detail_contains_only_cycle_readings_and_tariff_disclosure(
    tmp_path: Path,
) -> None:
    store = populated_store(tmp_path)
    cycle_id = str(store.fetch_cycles()[0]["cycle_id"])
    store.save_volume_label(cycle_id, VolumeClass.ONE_LITRE)

    detail = get_cycle_detail(store, cycle_id, tariff_context=None)

    assert detail.effective_volume == VolumeClass.ONE_LITRE
    assert detail.readings[0].sample_sequence == detail.summary.start_sequence
    assert detail.readings[-1].sample_sequence == detail.summary.end_sequence
    assert detail.tariff_estimate.amount_rm is None
    assert detail.comparison_text == "Not enough similar labelled cycles for comparison"


def test_comparison_text_uses_matching_volume_median() -> None:
    assert comparison_text(132, [118, 120, 122], VolumeClass.ONE_LITRE) == (
        "12 s longer than the median of your valid 1.0 L cycles"
    )


def test_cycle_list_filters_incomplete_cycles(tmp_path: Path) -> None:
    store = populated_store(tmp_path, scenario="incomplete_gap")

    items = list_cycle_items(store, status_filter="incomplete")

    assert len(items) == 1
    assert items[0].status.value == "incomplete"


def test_cycle_list_uses_user_managed_label_filter(tmp_path: Path) -> None:
    store = populated_store(tmp_path)
    cycle_id = str(store.fetch_cycles()[0]["cycle_id"])
    store.add_cycle_label("Morning mug")
    store.save_cycle_label(cycle_id, "Morning mug")

    matching = list_cycle_items(store, label_filter="Morning mug")
    unlabelled = list_cycle_items(store, label_filter="__unlabelled__")

    assert len(matching) == 1
    assert matching[0].cycle_label == "Morning mug"
    assert unlabelled == []
