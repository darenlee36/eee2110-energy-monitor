"""Synthetic calibration and dashboard checks. No fixture is published."""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from test_dataset import prepared_fixture

from dashboard.components import cycle_presentation_insights, model_pattern_insight
from energy_monitor import adaptive_warning as warning
from energy_monitor.pilot_workflow import _seal, save_immutable

DEVICE = "sim-esp32-u3-20261102-a"
SCOPE = {
    "device_id": DEVICE, "profile_id": "tefal-kettle", "status": "completed",
    "data_origin": "sensor", "record_source": "measured", "detection_version": "sim-cycle-v1",
}


def test_rank_handles_insufficient_data_native_boundary_and_ties():
    with pytest.raises(ValueError, match="40"):
        warning.warning_cutoff([55.0] * 39)
    with pytest.raises(ValueError):
        warning.warning_cutoff([float("nan")] * 40)
    assert warning.warning_cutoff([45.0] * 40) > 50.0
    scores = [55.0] * 39 + [80.0]
    cutoff = warning.warning_cutoff(scores)
    assert 55.0 < cutoff < 55.001
    assert 55.0 < cutoff <= 80.0


def test_controlled_calibration_replays_frozen_versions_and_excludes_unreviewed(
    monkeypatch, tmp_path,
):
    dataset = prepared_fixture(sessions=7, per_session=10)
    shift = timedelta(days=120)
    for item in dataset.cycles:
        item.started_at -= shift
        item.ended_at -= shift
        item.collection.session_started_at -= shift
        item.collection.label_verified_at = item.ended_at + timedelta(hours=1)
        item.quality_indicators.update(detection_version="sim-cycle-v1", raw_trace_hash="a" * 64)

    options = dict(baseline_group="baseline", train_sessions=1, validation_sessions=5)
    effective = datetime.now(UTC) + timedelta(days=1)
    monkeypatch.setattr(warning, "prepare_dataset", lambda *_: dataset)
    with pytest.raises(ValueError, match="Genuine"):
        warning.calibrate_warning(None, None, **options, effective_at=effective)
    # Stub the trusted telemetry preparation boundary to exercise the real forest offline.
    prepared = dataset.model_copy(update={"kind": "sensor_collection"})
    monkeypatch.setattr(warning, "prepare_dataset", lambda *_: prepared)
    item = prepared.cycles[11]
    item.collection.reviewed = False
    with pytest.raises(ValueError, match="39 held out"):
        warning.calibrate_warning(None, None, **{**options, "validation_sessions": 4},
                                  effective_at=effective)
    prepared.cycles[12].quality_flags.append("missing_sample")
    other = prepared.cycles[13].collection
    other.reviewed = False
    other.label = "unusual"
    other.label_basis = "independent_observation"
    other.reviewed = True

    class TraceStore:
        def fetch_cycle(self, cycle_id):
            return {**SCOPE, "cycle_id": cycle_id}

    def audit(cycle, *_):
        idx = UUID(cycle["cycle_id"]).int
        return {
            "cycle_id": cycle["cycle_id"], "collection_trace_sha256": "a" * 64,
            "features": {
                "duration_seconds": 200 + (idx - 1) % 10 * 5,
                "average_power_w": 1800 + (idx - 1) % 10 * 8,
            },
        }

    monkeypatch.setattr(warning, "_score_trace", audit)
    policy = warning.calibrate_warning(TraceStore(), None, **options, effective_at=effective)
    assert policy["calibration_count"] == 47
    assert policy["model"]["settings"]["n_estimators"] == 200
    assert policy["model"]["settings"]["contamination"] == "auto"
    fitted = set(policy["model"]["training_cycle_ids"])
    held_out = {row["cycle_id"] for row in policy["calibration"]}
    assert not fitted & held_out
    assert not held_out.intersection(str(UUID(int=i)) for i in (12, 13, 14, 61))
    path = tmp_path / f"{policy['artifact_sha256']}.json"
    save_immutable(path, policy)
    assert warning.load_warning_policy(path) == policy
    with pytest.raises(ValueError, match="immutable"):
        save_immutable(path, {**policy, "threshold": 70})

    observed = {
        "cycle_id": str(UUID(int=200)),
        "started_at": (effective + timedelta(hours=1)).isoformat(),
        "features": {"duration_seconds": 320, "average_power_w": 1600},
    }
    result = warning.assess_warning(SCOPE, observed, tmp_path)
    assert result is not None
    assert result["policy"]["threshold"] == policy["threshold"]
    assert result["score"] == warning.display_score(result["raw_score"])
    assert warning.assess_warning(SCOPE, {**observed, "cycle_id": next(iter(fitted))},
                                  tmp_path) is None
    assert warning.assess_warning(SCOPE, {**observed, "cycle_id": next(iter(held_out))},
                                  tmp_path) is None
    assert warning.assess_warning({**SCOPE, "profile_id": "another"}, observed, tmp_path) is None
    assert warning.assess_warning({**SCOPE, "data_origin": "simulated"},
                                  observed, tmp_path) is None
    assert warning.assess_warning(SCOPE, {**observed, "started_at": datetime.now(UTC).isoformat()},
                                  tmp_path) is None

    newer_effective = effective + timedelta(days=1)
    newer = warning.calibrate_warning(TraceStore(), None, **options,
                                      effective_at=newer_effective)
    save_immutable(tmp_path / f"{newer['artifact_sha256']}.json", newer)
    assert warning.assess_warning(SCOPE, observed, tmp_path) == result
    new_row = {**observed, "started_at": newer_effective.isoformat()}
    assert warning.assess_warning(SCOPE, new_row, tmp_path)["policy"] == newer
    warning._warning_forest.cache_clear()
    warning.assess_warning(SCOPE, new_row, tmp_path)
    warning.assess_warning(SCOPE, new_row, tmp_path)
    assert warning._warning_forest.cache_info().misses == 1


def test_policy_integrity_rejects_training_calibration_overlap(tmp_path: Path):
    members = [{"cycle_id": str(UUID(int=i)), "score": 52.0} for i in range(40)]
    policy = _seal({
        "method": warning.METHOD, "percentile": warning.PERCENTILE,
        "calibration_count": len(members), "calibration": members,
        "calibration_session_count": 4,
        "calibrated_at": "2026-11-06T10:00:00+08:00",
        "effective_at": "2026-11-06T11:00:00+08:00",
        "model": {"training_cycle_ids": [members[0]["cycle_id"]]},
        "threshold": warning.warning_cutoff([52.0] * 40),
    })
    path = tmp_path / f"{policy['artifact_sha256']}.json"
    save_immutable(path, policy)
    with pytest.raises(ValueError, match="artifact"):
        warning.load_warning_policy(path)


def test_insights_follow_native_result_dominant_measurement_and_quality():
    model = {"training_reference": {"cycles": [
        {"features": {"duration_seconds": 200, "average_power_w": 1800}},
    ]}}
    row = {"features": {"duration_seconds": 240, "average_power_w": 1854}}
    cycle = {
        **SCOPE, **row["features"], "meter_energy_kwh": 0.12,
        "peak_power_w": 1900, "assessment": "normal",
    }
    normal = model_pattern_insight(row, {"score": 49, "raw_decision": .01}, model)
    unusual = model_pattern_insight(row, {"score": 51, "raw_decision": -.01}, model)
    policy = {"threshold": 62}
    flagged = model_pattern_insight(row, {"score": 62, "raw_decision": -.12}, model, policy)
    assert [i["pattern"] for i in (normal, unusual, flagged)] == ["normal", "unusual", "warning"]
    assert "+40.0 s (+20.0%)" in unusual["comparison"]
    assert "+54.0 W (+3.0%)" in unusual["comparison"]
    assert "within" in cycle_presentation_insights(cycle, normal)["possibilities"][0]
    assert "heating times" in cycle_presentation_insights(cycle, unusual)["actions"][0]
    assert "fault" in cycle_presentation_insights(cycle, flagged)["actions"][-1]
    power_row = {"features": {"duration_seconds": 202, "average_power_w": 2000}}
    power = model_pattern_insight(power_row, {"score": 58, "raw_decision": -.08}, model)
    assert "voltage" in cycle_presentation_insights(cycle, power)["actions"][0]
    assert "connectivity" in cycle_presentation_insights(
        {**cycle, "missing_sample_count": 1}, unusual,
    )["actions"][0]
