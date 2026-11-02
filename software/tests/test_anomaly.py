"""A1 uses test-only fixtures; no production model or live AI is created."""
from uuid import UUID

import pytest


def case(index=1, origin="simulated", **updates):
    from energy_monitor.anomaly import CycleFeatures, TrainingCase
    values = {
        "duration_seconds": 120.0 + index % 7,
        "energy_kwh": .07 + (index % 5) * .001,
        "average_power_w": 2000.0 + index % 11,
        "peak_power_w": 2100.0 + index % 13,
        "average_current_a": 8.5 + index % 3 * .01,
        "voltage_span_v": 1.0 + index % 4 * .1,
        "average_power_factor": .99,
        "frequency_span_hz": .1,
        "sampling_gap_count": 0.0,
        "invalid_fraction": 0.0,
    }
    values.update(updates.pop("values", {}))
    f = CycleFeatures(cycle_id=str(UUID(int=index)), profile_id="kettle", device_id="meter",
                      data_origin=origin, values=values, issues=[])
    return TrainingCase(features=f, label="normal", split="train", reviewed=True, **updates)


def test_untrained_and_insufficient_never_return_a_score():
    from energy_monitor.anomaly import CycleAnomalyModel, train_model
    model = CycleAnomalyModel()
    assert model.predict(case().features).anomaly_score is None
    small = train_model([case()], test_only=True)
    assert small.status == "insufficient_data"
    assert small.predict(case().features).classification == "unavailable"


def test_production_training_rejects_synthetic_manual_unreviewed_and_holdout():
    from energy_monitor.anomaly import train_model
    assert train_model([case(i) for i in range(1, 35)]).status == "insufficient_data"
    candidates = [case(i, origin="sensor") for i in range(1, 35)]
    candidates = [c.model_copy(update={"split": "held_out"}) for c in candidates]
    assert train_model(candidates).status == "insufficient_data"
    candidates = [case(i, origin="sensor").model_copy(update={"reviewed": False})
                  for i in range(1, 35)]
    assert train_model(candidates).status == "insufficient_data"


def test_test_only_fit_prediction_normalization_and_scope():
    from energy_monitor.anomaly import train_model
    model = train_model([case(i) for i in range(1, 41)], test_only=True)
    assert model.status == "test_only"
    assert model.predict(case().features).anomaly_score is None
    result = model.predict(case().features, testing=True)
    assert result.status == "test_only" and 0 <= result.anomaly_score <= 100
    assert result.raw_decision is not None and result.model_version
    assert result.normalization == "baseline_upper_tail_percentile_v1"
    assert result.comparisons and result.is_failure_probability is False
    other = case().features.model_copy(update={"profile_id": "another"})
    assert model.predict(other, testing=True).status == "scope_mismatch"


def test_percentile_direction_ties_and_nonfinite():
    from energy_monitor.anomaly import normalized_score
    assert normalized_score(-2, [-1, 0, 1]) == 100
    assert normalized_score(2, [-1, 0, 1]) == 0
    assert normalized_score(0, [0, 0]) == 50
    with pytest.raises(ValueError):
        normalized_score(float("nan"), [-1, 0])


def test_versioned_round_trip_corruption_and_explicit_trust(tmp_path):
    from energy_monitor.anomaly import CycleAnomalyModel, train_model
    model = train_model([case(i) for i in range(1, 41)], test_only=True)
    path = tmp_path / "test-model.zip"
    model.save(path)
    with pytest.raises(ValueError, match="trusted"):
        CycleAnomalyModel.load(path)
    loaded = CycleAnomalyModel.load(path, trusted_local=True)
    assert loaded.predict(case().features, testing=True) == model.predict(
        case().features, testing=True)
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        CycleAnomalyModel.load(path, trusted_local=True)


def test_extract_features_from_detector_and_reject_missing_nonfinite_or_bad_quality(tmp_path):
    from test_reporting import seeded_cycles

    from energy_monitor.anomaly import extract_cycle_features
    store, ids = seeded_cycles(tmp_path)
    row = store.fetch_cycle(ids[0])
    # seeded_cycles changes the cycle UUID, but retains first-cycle sequences.
    raw = store.fetch_cycle_readings(ids[0])
    features = extract_cycle_features(row, raw)
    assert not features.issues
    assert features.values["average_power_w"] == row["average_power_w"]
    assert features.values["frequency_span_hz"] == 0
    assert extract_cycle_features(row, []).issues
    bad = [dict(r) for r in raw]
    bad[0]["frequency_hz"] = float("nan")
    assert extract_cycle_features(row, bad).issues
    bad = [dict(r) for r in raw]
    bad[0]["quality_status"] = "read_error"
    assert extract_cycle_features(row, bad).issues


def test_training_duplicates_scope_quality_and_constant_features_are_excluded():
    from energy_monitor.anomaly import train_model
    duplicates = [case()] * 40
    assert train_model(duplicates, test_only=True).status == "insufficient_data"
    mixed = [case(i) for i in range(1, 41)]
    mixed[-1] = case(41).model_copy(update={"features": case(41).features.model_copy(
        update={"profile_id": "other"})})
    with pytest.raises(ValueError, match="scope"):
        train_model(mixed, test_only=True)
    bad = [c.model_copy(update={"features": c.features.model_copy(
        update={"issues": ["gap"]})}) for c in [case(i) for i in range(1, 41)]]
    assert train_model(bad, test_only=True).status == "insufficient_data"


def test_out_of_range_raw_input_is_excluded(tmp_path):
    from test_reporting import seeded_cycles

    from energy_monitor.anomaly import extract_cycle_features
    store, ids = seeded_cycles(tmp_path)
    row, raw = store.fetch_cycle(ids[0]), store.fetch_cycle_readings(ids[0])
    raw[0]["frequency_hz"] = 999
    assert extract_cycle_features(row, raw).issues


def test_test_artifact_cannot_be_promoted_by_report_evidence(tmp_path):
    from energy_monitor.anomaly import AnomalyPrediction
    with pytest.raises(ValueError):
        AnomalyPrediction(status="not_trained", data_origin="sensor", anomaly_score=80)
    with pytest.raises(ValueError):
        AnomalyPrediction(status="scored", data_origin="sensor", anomaly_score=80,
                          raw_decision=float("nan"), classification="anomalous",
                          model_version="test")


def test_constant_only_training_and_non_normal_labels_are_insufficient():
    from energy_monitor.anomaly import train_model
    first = case().features.values
    constant = [case(i, values=first) for i in range(1, 41)]
    assert train_model(constant, test_only=True).status == "insufficient_data"
    unusual = [case(i).model_copy(update={"label": "anomalous"}) for i in range(1, 41)]
    assert train_model(unusual, test_only=True).status == "insufficient_data"
