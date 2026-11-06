from pathlib import Path

import pytest

from energy_monitor.experimental_if import (
    display_score,
    load_release,
    scope_issue,
)


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
