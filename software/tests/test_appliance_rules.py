from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from energy_monitor.cycles import detect_cycles
from energy_monitor.models import CycleDetectionSettings, TelemetryBatch
from energy_monitor.simulator import generate_cycle_scenario
from energy_monitor.storage import SQLiteTelemetryStore


def test_rules_persist_and_apply_to_future_scoped_cycles(tmp_path):
    path = tmp_path / "cycles.sqlite"
    store = SQLiteTelemetryStore(path)
    initial = datetime(2026, 11, 5, tzinfo=UTC)
    kettle = generate_cycle_scenario("normal", initial)
    fan_start = initial + timedelta(hours=1)
    fan = [sample.model_copy(update={
        "sample_sequence": sample.sample_sequence + 1000,
        "active_power_w": sample.active_power_w / 30,
    }) for sample in generate_cycle_scenario("normal", fan_start)]

    def insert(samples):
        for start in range(0, len(samples), 6):
            store.insert_batch(TelemetryBatch(batch_id=uuid4(), readings=samples[start:start + 6]))

    settings = CycleDetectionSettings.simulation_defaults()
    insert(kettle)
    old, = [c for c in store.process_cycles(settings) if c.status == "completed"]
    fan_rule = settings.model_copy(update={"start_power_w": 30, "stop_power_w": 10})
    with pytest.raises(ValueError, match="stop threshold"):
        store.save_appliance_rule(kettle[0].device_id, kettle[0].profile_id, "Fan",
                                  fan_rule.model_copy(update={"stop_power_w": 40}),
                                  effective_at=fan_start)
    version = store.save_appliance_rule(kettle[0].device_id, kettle[0].profile_id, "Fan",
                                        fan_rule, effective_at=fan_start)
    store = SQLiteTelemetryStore(path)
    insert(fan)
    completed = [c for c in store.process_cycles(settings) if c.status == "completed"]
    assert [c.detection_version for c in completed] == [settings.version, version]
    assert completed[0] == old
    assert store.fetch_cycle(old.cycle_id)["detection_version"] == settings.version
    assert store.cycle_rule(version)[0] == "Fan"
    assert store.cycle_rule(version)[1].start_power_w == 30


def test_threshold_change_cannot_finalize_a_cycle_already_in_progress():
    readings = generate_cycle_scenario("normal", datetime(2026, 11, 5, tzinfo=UTC))
    base = CycleDetectionSettings.simulation_defaults()
    old_cycle, = detect_cycles(readings, base)
    revised = base.model_copy(update={
        "version": "later-rules", "start_power_w": 2500, "stop_power_w": 2200,
    })
    mid_cycle = old_cycle.started_at + timedelta(seconds=60)
    assert detect_cycles(readings, base, revisions=[(mid_cycle, revised)]) == [old_cycle]
