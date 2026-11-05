"""Synthetic incoming cycles exercise local workflows; no production model activation."""
from datetime import timedelta

import pytest
from test_dataset import prepared_fixture

from energy_monitor.dataset import make_split
from energy_monitor.pilot import load_pilot_reference
from energy_monitor.pilot_workflow import (
    assess_collection,
    expand_reference,
    fit_experiment,
    load_experiment,
    predict_experiment,
    save_immutable,
    score_experiment,
)


def collection():
    dataset = prepared_fixture(sessions=3, per_session=2)
    for cycle in dataset.cycles:
        cycle.device_id = "sim-esp32-u3-20261102-a"
        cycle.profile_id = "tefal-kettle"
        cycle.features = cycle.features.model_copy(update={
            "device_id": cycle.device_id, "profile_id": cycle.profile_id,
            "data_origin": "sensor",
        })
        cycle.ended_at = cycle.started_at + timedelta(
            seconds=cycle.features.values["duration_seconds"])
        cycle.collection.label_verified_at = cycle.ended_at + timedelta(minutes=1)
        cycle.quality_indicators.update(
            status="completed", invalid_sample_count=0, missing_sample_count=0,
            gap_count=0, raw_trace_hash="a" * 64, firmware_versions=["pzem-fw-0.2.7"],
            largest_gap_seconds=3,
            sample_count=round(cycle.features.values["duration_seconds"] / 3 + 1))
    return dataset, make_split(dataset, baseline_group="baseline",
                               train_sessions=1, validation_sessions=1)


def test_frozen_v1_assessments_are_reproducible_and_duplicate_safe(tmp_path):
    dataset, plan = collection()
    reference = load_pilot_reference()
    records = assess_collection(reference, dataset, plan, testing=True)
    assert len(records) == 6
    assert {r["partition"] for r in records} == {"train", "validation", "test"}
    for record in records:
        assert record["baseline_id"] == reference["baseline_id"]
        assert record["ml_assessment"] == "not_evaluated"
        assert record["testing"] is True
        path = tmp_path / (record["cycle_id"] + ".json")
        assert save_immutable(path, record) == record
        assert save_immutable(path, record) == record
        with pytest.raises(ValueError, match="immutable"):
            save_immutable(path, {**record, "features": {}})


@pytest.mark.parametrize("change", ["fixture_mode", "review", "quality", "origin",
                                  "scope", "trace", "time", "features", "split",
                                  "short_gap", "count", "firmware"])
def test_incoming_collection_gates(change):
    dataset, plan = collection()
    row = dataset.cycles[0]
    testing = True
    if change == "fixture_mode":
        testing = False
    elif change == "review":
        row.collection.reviewed = False
    elif change == "quality":
        row.quality_flags.append("missing_samples")
    elif change == "origin":
        row.features = row.features.model_copy(update={"data_origin": "simulated"})
    elif change == "scope":
        row.features = row.features.model_copy(update={"device_id": "other"})
    elif change == "trace":
        row.quality_indicators["raw_trace_hash"] = ""
    elif change == "time":
        row.ended_at += timedelta(seconds=1)
    elif change == "features":
        row.features = row.features.model_copy(update={"issues": ["invalid"]})
    elif change == "split":
        plan.assignments[str(row.collection.cycle_id)] = "test"
    elif change == "short_gap":
        row.quality_indicators["largest_gap_seconds"] = 6
    elif change == "count":
        row.quality_indicators["sample_count"] -= 3
    elif change == "firmware":
        row.quality_indicators["firmware_versions"] = ["unverified"]
    if change != "split":
        # Refresh legitimate split metadata; the independent gate must still reject.
        plan.dataset_hash = dataset.digest()
    with pytest.raises(ValueError):
        assess_collection(load_pilot_reference(), dataset, plan, testing=testing)


def test_v2_requires_explicit_reviewed_training_ids_and_saved_v1_scores(tmp_path):
    dataset, plan = collection()
    reference = load_pilot_reference()
    original = reference.copy()
    records = assess_collection(reference, dataset, plan, testing=True)
    ids = [r["cycle_id"] for r in records if r["partition"] == "train"]
    with pytest.raises(ValueError, match="saved v1"):
        expand_reference(reference, dataset, plan, ids, tmp_path, testing=True)
    for record in records:
        save_immutable(tmp_path / (record["cycle_id"] + ".json"), record)
    expanded = expand_reference(reference, dataset, plan, ids, tmp_path, testing=True)
    assert expanded["parent_baseline_id"] == reference["baseline_id"]
    assert expanded["baseline_id"].startswith("pilot-deviation-v2-")
    assert len(expanded["cycles"]) == 8
    assert reference == original
    assert expanded["ml_assessment"] == "not_evaluated"
    held_out = next(r["cycle_id"] for r in records if r["partition"] == "test")
    with pytest.raises(ValueError, match="training"):
        expand_reference(reference, dataset, plan, [held_out], tmp_path, testing=True)
    with pytest.raises(ValueError, match="unique"):
        expand_reference(reference, dataset, plan, ids * 2, tmp_path, testing=True)


def test_public_annotation_or_unknown_label_cannot_expand_reference(tmp_path):
    dataset, _ = collection()
    dataset.cycles[0].collection.label = "unknown"
    plan = make_split(dataset, baseline_group="baseline", train_sessions=1, validation_sessions=1)
    records = assess_collection(load_pilot_reference(), dataset, plan, testing=True)
    for record in records:
        save_immutable(tmp_path / (record["cycle_id"] + ".json"), record)
    with pytest.raises(ValueError, match="reviewed normal"):
        expand_reference(load_pilot_reference(), dataset, plan,
                         [records[0]["cycle_id"]], tmp_path, testing=True)


def test_experimental_forest_artifact_reconstructs_without_pickle_or_activation(tmp_path):
    reference = load_pilot_reference()
    experiment = fit_experiment(reference)
    assert experiment["status"] == "experimental_in_sample_only"
    assert experiment["validated"] is False
    assert experiment["feature_names"] == ["duration_seconds", "average_power_w"]
    assert len(experiment["training_cycle_ids"]) == 6
    path = tmp_path / "experiment.json"
    save_immutable(path, experiment)
    forest, loaded = load_experiment(path)
    assert loaded == experiment
    scores = score_experiment(forest, loaded, reference)
    assert len(scores) == 6
    assert all(s["reference_member"] and s["classification"] == "not_evaluated" for s in scores)
    assert [s["raw_decision"] for s in scores] == pytest.approx(
        experiment["in_sample_decisions"])
    assert fit_experiment(reference) == experiment
    path.write_text(path.read_text().replace('"random_state": 42', '"random_state": 43'))
    with pytest.raises(ValueError, match="artifact"):
        load_experiment(path)


def test_experiment_rejects_scope_leakage_and_tampered_reference():
    reference = load_pilot_reference()
    reference["cycles"][0]["features"]["duration_seconds"] += 1
    with pytest.raises(ValueError):
        fit_experiment(reference)


def test_save_lock_and_interruption_leave_no_partial_final_artifact(tmp_path, monkeypatch):
    import energy_monitor.pilot_workflow as workflow
    from energy_monitor.report_worker import worker_lock

    path = tmp_path / "cycle.json"
    with worker_lock(path.with_suffix(".json.lock")) as acquired:
        assert acquired
        with pytest.raises(ValueError, match="busy"):
            save_immutable(path, {"score": 1})
    original_replace = workflow.os.replace
    def interrupted(*args):
        raise OSError("simulated interrupted save")
    monkeypatch.setattr(workflow.os, "replace", interrupted)
    with pytest.raises(OSError):
        save_immutable(path, {"score": 1})
    assert not path.exists()
    assert not list(tmp_path.glob("*.tmp"))
    monkeypatch.setattr(workflow.os, "replace", original_replace)
    assert save_immutable(path, {"score": 1}) == {"score": 1}


def test_new_cycle_prediction_remains_unvalidated_and_rejects_scope(tmp_path):
    import hashlib
    import json

    reference = load_pilot_reference()
    reference["testing"] = True
    unsigned = {k: v for k, v in reference.items() if k != "artifact_sha256"}
    reference["artifact_sha256"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, allow_nan=False).encode()).hexdigest()
    path = tmp_path / "fixture-experiment.json"
    save_immutable(path, fit_experiment(reference))
    forest, artifact = load_experiment(path)
    dataset, plan = collection()
    candidate = assess_collection(reference, dataset, plan, testing=True)[-1]
    result = predict_experiment(forest, artifact, candidate)
    assert result["reference_member"] is False
    assert result["evaluation_role"] == "unvalidated_new_cycle"
    assert result["classification"] == "not_evaluated"
    assert result["failure_probability"] is None
    with pytest.raises(ValueError, match="scope"):
        predict_experiment(forest, artifact, {**candidate, "profile_id": "another"})


def test_saved_legacy_or_future_observations_are_not_backdated(tmp_path):
    dataset, plan = collection()
    row = dataset.cycles[0]
    row.collection.label_verified_at = row.started_at
    plan.dataset_hash = dataset.digest()
    records = assess_collection(load_pilot_reference(), dataset, plan, testing=True)
    for record in records:
        save_immutable(tmp_path / (record["cycle_id"] + ".json"), record)
    with pytest.raises(ValueError, match="reviewed normal"):
        expand_reference(load_pilot_reference(), dataset, plan,
                         [records[0]["cycle_id"]], tmp_path, testing=True)


def test_cli_rehearsal_assess_expand_fit_preserves_v1(tmp_path, monkeypatch):
    import json
    import sys

    from energy_monitor.pilot_workflow import main

    dataset, plan = collection()
    dataset_path, split_path = tmp_path / "dataset.json", tmp_path / "split.json"
    dataset_path.write_text(dataset.model_dump_json())
    split_path.write_text(plan.model_dump_json())
    ledger = tmp_path / "v1-assessments"
    common = ["--dataset", str(dataset_path), "--split", str(split_path),
              "--ledger", str(ledger), "--testing"]
    monkeypatch.setattr(sys, "argv", ["pilot", "assess", *common,
                                     "--output", str(tmp_path / "assessments.json")])
    main()
    identity = str(dataset.cycles[0].collection.cycle_id)
    original = (ledger / (identity + ".json")).read_bytes()
    expanded_path = tmp_path / "v2.json"
    monkeypatch.setattr(sys, "argv", ["pilot", "expand", *common, "--cycle-id", identity,
                                     "--output", str(expanded_path)])
    main()
    monkeypatch.setattr(sys, "argv", ["pilot", "fit", "--reference", str(expanded_path),
                                     "--output", str(tmp_path / "v2-if.json")])
    main()
    _, artifact = load_experiment(tmp_path / "v2-if.json")
    assert artifact["training_reference"]["testing"] is True
    assert len(artifact["training_cycle_ids"]) == 7
    assert (ledger / (identity + ".json")).read_bytes() == original
    assert json.loads(expanded_path.read_text())["parent_baseline_id"] == (
        load_pilot_reference()["baseline_id"])
