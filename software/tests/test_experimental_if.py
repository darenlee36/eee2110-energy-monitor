from datetime import datetime, timedelta
from pathlib import Path
from statistics import fmean
from uuid import uuid4

import pytest

from energy_monitor import experimental_if
from energy_monitor.cloud_edits import SupabaseCycleStore
from energy_monitor.experimental_if import (
    assess_current_model,
    assess_selected_cycle,
    current_rolling_power_comparison,
    display_score,
    load_release,
    preserve_first_assessment,
    rolling_power_comparison,
    scope_issue,
)
from energy_monitor.supabase_sync import SupabaseConfig


def test_score_mapping_increases_as_samples_become_more_unusual():
    assert [display_score(value) for value in (-0.35, -0.50, -0.71)] == [35, 50, 71]
    with pytest.raises(ValueError):
        display_score(float("nan"))


def test_frozen_release_is_reproducible_and_does_not_train_on_new_cycles(tmp_path: Path):
    forest, release = load_release()
    model = release["model"]
    assert len(model["training_cycle_ids"]) == 6
    assert model["settings"]["n_estimators"] == 200
    assert len(release["assessments"]) == 10
    dispositions = [r["learning_disposition"] for r in release["assessments"].values()]
    assert dispositions[6:] == [
        "quarantined_unusual", "pending_review", "quarantined_reference_shift",
        "quarantined_reference_shift",
    ]
    for row in release["assessments"].values():
        vector = [[row["features"][name] for name in model["feature_names"]]]
        assert row["raw_score"] == pytest.approx(forest.score_samples(vector)[0])
        assert row["raw_decision"] == pytest.approx(forest.decision_function(vector)[0])
        assert row["reference_member"] == (row["cycle_id"] in model["training_cycle_ids"])
    broken = tmp_path / "release.json"
    broken.write_text('{"schema_version": 1}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_release(broken)


def test_scope_rejects_simulated_cross_profile_or_incomplete_cycles():
    _, release = load_release()
    model = release["model"]
    cycle = {
        "profile_id": "tefal-kettle", "device_id": "sim-esp32-u3-20261102-a",
        "data_origin": "sensor", "status": "completed", "record_source": "measured",
    }
    assert scope_issue(cycle, model) is None
    assert scope_issue({**cycle, "data_origin": "simulated"}, model)
    assert scope_issue({**cycle, "profile_id": "fan"}, model)
    assert scope_issue({**cycle, "status": "incomplete"}, model)


def test_first_score_survives_fresh_cache_and_complete_is_required(tmp_path, monkeypatch):
    _, release = load_release()
    row = {**list(release["assessments"].values())[-1], "cycle_id": str(uuid4())}
    cycle = {
        **row["features"], "cycle_id": row["cycle_id"], "data_origin": "sensor",
        "profile_id": "tefal-kettle", "device_id": "sim-esp32-u3-20261102-a",
        "status": "completed", "record_source": "measured", "custom_label": "Incomplete",
        "started_at": row["started_at"], "ended_at": row["ended_at"],
    }
    values = {}
    prior = {"cycle_count": 10, "mean_power_w": 1862.4}
    cloud_ready = False

    def rpc(self, name, payload):
        if name == "read_intelligence_first_score":
            return values.get(payload["p_cycle_id"])
        if name == "read_intelligence_rolling_power":
            assert set(payload["p_frozen_ids"]) == set(release["assessments"])
            return prior.copy()
        assert name == "append_intelligence_first_score"
        assert cycle["custom_label"] == "Complete"
        if not cloud_ready:
            raise ValueError("First score save interrupted")
        return values.setdefault(cycle["cycle_id"], payload["p_evidence"])

    monkeypatch.setattr(SupabaseCycleStore, "_rpc", rpc)
    monkeypatch.setattr(experimental_if, "_score_trace", lambda *args: row)
    config = SupabaseConfig("https://project.supabase.co", "test-key")
    store = SupabaseCycleStore(tmp_path / "first.db", config)
    assert not preserve_first_assessment(cycle, store)
    assert not values
    cycle["custom_label"] = "Complete"
    with pytest.raises(ValueError, match="interrupted"):
        preserve_first_assessment(cycle, store)
    pending, _ = assess_selected_cycle(cycle, store)
    assert pending["score"] == row["score"]
    assert "rolling_power" not in pending and not values
    cloud_ready = True
    recovered, _ = assess_selected_cycle(cycle, store)
    assert recovered["rolling_power"]["cycle_count"] == 10
    prior.update(cycle_count=9, mean_power_w=1830.6)
    fresh = SupabaseCycleStore(tmp_path / "fresh.db", config)
    monkeypatch.setattr(experimental_if, "_score_trace",
                        lambda *args: pytest.fail("First score was recalculated"))
    assert not preserve_first_assessment(cycle, fresh)
    saved, saved_release = assess_selected_cycle(cycle, fresh)
    assert saved["score"] == row["score"]
    comparison = rolling_power_comparison(saved, saved_release, fresh)
    assert comparison["cycle_count"] == 10
    assert comparison["mean_power_w"] == 1862.4
    assert comparison["version"] == "rolling-power-v1"
    cycle["custom_label"] = "Incomplete"
    with pytest.raises(ValueError, match="Incomplete"):
        assess_selected_cycle(cycle, fresh)


def test_rolling_power_compares_first_then_includes_one_mean_per_cycle():
    _, release = load_release()
    earlier = list(release["assessments"].values())
    end = max(datetime.fromisoformat(row["ended_at"]) for row in earlier)
    powers = [row["features"]["average_power_w"] for row in earlier]
    cycle11 = {"cycle_id": "cycle-11", "features": {"average_power_w": 2100},
               "started_at": (end + timedelta(minutes=1)).isoformat(),
               "ended_at": (end + timedelta(minutes=4)).isoformat()}
    cycle12 = {"cycle_id": "cycle-12", "features": {"average_power_w": 2200},
               "started_at": (end + timedelta(minutes=5)).isoformat()}

    first = rolling_power_comparison(cycle11, release, None)
    assert first["cycle_count"] == 10
    assert first["mean_power_w"] == pytest.approx(fmean(powers))

    with_cycle11 = {**release, "assessments": {**release["assessments"], "cycle-11": cycle11}}
    assert rolling_power_comparison(cycle11, with_cycle11, None) == first
    second = rolling_power_comparison(cycle12, with_cycle11, None)
    assert second["cycle_count"] == 11
    assert second["mean_power_w"] == pytest.approx(fmean([*powers, 2100]))


def test_current_forest_refits_and_rescores_an_earlier_cycle_when_membership_changes():
    _, release = load_release()
    row = list(release["assessments"].values())[-1]
    initial = current_rolling_power_comparison(row, release, None)
    score, model = assess_current_model(row, initial, release)
    new_cycle = {"cycle_id": "00000000-0000-4000-8000-000000000001",
                 "duration_seconds": 242, "average_power_w": 2040}
    updated = {**initial, "cycle_count": 11,
               "training_rows": [*initial["training_rows"], new_cycle]}
    rescored, refit = assess_current_model(row, updated, release)
    assert len(model["training_cycle_ids"]) == 10
    assert len(refit["training_cycle_ids"]) == 11
    assert new_cycle["cycle_id"] in refit["training_cycle_ids"]
    assert model["artifact_sha256"] != refit["artifact_sha256"]
    assert score["score"] != rescored["score"]
    assert assess_current_model(row, updated, release)[1] is refit
    assert round(row["score"], 2) == 46.12
