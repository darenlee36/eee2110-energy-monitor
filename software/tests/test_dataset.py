"""Synthetic-only collection/evaluation fixtures; no physical or production training."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from test_anomaly import case


def prepared_fixture(sessions=6, per_session=10):
    from energy_monitor.dataset import CollectionRecord, DatasetCycle, PreparedDataset

    records = []
    for session in range(sessions):
        start = datetime(2026, 11, 9, 8, tzinfo=UTC) + timedelta(days=session)
        for index in range(per_session):
            number = session * per_session + index + 1
            features = case(number).features
            records.append(
                DatasetCycle(
                    collection=CollectionRecord(
                        cycle_id=features.cycle_id,
                        session_id=f"S{session + 1:02}",
                        session_started_at=start,
                        water_volume_ml=1000,
                        start_temperature_band="room",
                        operating_condition="standard_boil",
                        baseline_group="baseline",
                        reviewed=True,
                        label="normal",
                        label_basis="normal_observation",
                        label_evidence="Fixture observation only",
                        operator="fixture",
                    ),
                    device_id=features.device_id,
                    profile_id=features.profile_id,
                    started_at=start + timedelta(minutes=index * 5),
                    ended_at=start + timedelta(minutes=index * 5 + 2),
                    features=features,
                    quality_flags=[],
                    quality_indicators={"sample_count": 25},
                )
            )
    return PreparedDataset(kind="synthetic_fixture", cycles=records)


def test_reproducible_whole_session_split_and_frozen_hash():
    from energy_monitor.dataset import make_split, validate_split

    dataset = prepared_fixture()
    plan = make_split(dataset, baseline_group="baseline")
    assert len([p for p in plan.assignments.values() if p == "train"]) == 30
    assert len([p for p in plan.assignments.values() if p == "validation"]) == 10
    assert len([p for p in plan.assignments.values() if p == "test"]) == 20
    reversed_dataset = dataset.model_copy(update={"cycles": list(reversed(dataset.cycles))})
    assert make_split(reversed_dataset, baseline_group="baseline") == plan
    validate_split(dataset, plan)
    changed = dataset.model_copy(deep=True)
    changed.cycles[0].collection.notes = "new review"
    with pytest.raises(ValueError, match="frozen|hash"):
        validate_split(changed, plan)


def test_sessions_cannot_cross_partitions_or_overlap_in_time():
    from energy_monitor.dataset import make_split, validate_split

    dataset = prepared_fixture()
    plan = make_split(dataset, baseline_group="baseline")
    bad = plan.model_copy(deep=True)
    bad.assignments[str(dataset.cycles[0].collection.cycle_id)] = "test"
    with pytest.raises(ValueError):
        validate_split(dataset, bad)
    dataset.cycles[10].collection.session_started_at = dataset.cycles[
        0
    ].collection.session_started_at
    with pytest.raises(ValueError):
        make_split(dataset, baseline_group="baseline")


def test_labels_need_independent_evidence_and_no_volume_mixing():
    from energy_monitor.dataset import CollectionRecord, make_split

    with pytest.raises(ValueError):
        CollectionRecord(
            cycle_id=UUID(int=1),
            session_id="S1",
            session_started_at=datetime(2026, 11, 9, tzinfo=UTC),
            water_volume_ml=1000,
            start_temperature_band="room",
            operating_condition="standard_boil",
            baseline_group="baseline",
            reviewed=True,
            label="unusual",
            label_basis="model_only",
            label_evidence="model said so",
            operator="fixture",
        )
    dataset = prepared_fixture()
    dataset.cycles[-1].collection.water_volume_ml = 500
    with pytest.raises(ValueError, match="scope|volume|condition"):
        make_split(dataset, baseline_group="baseline")


def test_fixture_fit_is_test_only_and_production_entry_is_blocked():
    from energy_monitor.dataset import fit_prepared, make_split

    dataset = prepared_fixture()
    plan = make_split(dataset, baseline_group="baseline")
    with pytest.raises(ValueError, match="fixture|production"):
        fit_prepared(dataset, plan, test_only=False)
    model = fit_prepared(dataset, plan, test_only=True)
    assert model.status == "test_only"
    assert model.metadata["dataset_hash"] == dataset.digest()
    assert set(model.metadata["training_cycle_ids"]) == {
        key for key, value in plan.assignments.items() if value == "train"
    }
    assert model.predict(dataset.cycles[-1].features, testing=True).status == "scope_mismatch"


def test_evaluation_denominators_distributions_and_no_invented_anomalies():
    from energy_monitor.dataset import evaluate_partition, fit_prepared, make_split

    dataset = prepared_fixture()
    plan = make_split(dataset, baseline_group="baseline")
    model = fit_prepared(dataset, plan, test_only=True)
    result = evaluate_partition(model, dataset, plan, partition="test", testing=True)
    assert result["status"] == "synthetic_fixture_only"
    assert result["normal_false_alerts"]["denominator"] == 20
    assert result["unusual_detection"]["denominator"] == 0
    assert result["unusual_detection"]["rate"] is None
    assert result["score_distributions"]["normal"]["count"] == 20
    assert result["model_activated"] is False


def test_threshold_review_uses_validation_only_and_leakage_is_rejected():
    from energy_monitor.dataset import (
        evaluate_partition,
        fit_prepared,
        make_split,
        review_thresholds,
    )

    dataset = prepared_fixture()
    plan = make_split(dataset, baseline_group="baseline")
    model = fit_prepared(dataset, plan, test_only=True)
    results = review_thresholds(model, dataset, plan, thresholds=[-0.05, 0, 0.05], testing=True)
    assert len(results) == 3 and all(r["partition"] == "validation" for r in results)
    with pytest.raises(ValueError):
        evaluate_partition(model, dataset, plan, partition="train", testing=True)
    model.metadata["training_cycle_ids"].append(str(dataset.cycles[-1].collection.cycle_id))
    with pytest.raises(ValueError, match="training|leak"):
        evaluate_partition(model, dataset, plan, partition="test", testing=True)


def test_csv_template_is_round_trippable_and_unknown_labels_stay_unknown(tmp_path):
    from energy_monitor.dataset import CSV_COLUMNS, read_collection_csv, write_collection_csv

    path = tmp_path / "collection.csv"
    records = [prepared_fixture().cycles[0].collection]
    write_collection_csv(path, records)
    assert path.read_text(encoding="utf-8-sig").splitlines()[0].split(",") == list(CSV_COLUMNS)
    assert read_collection_csv(path) == records
    path.write_text(",".join(CSV_COLUMNS) + "\n", encoding="utf-8")
    assert read_collection_csv(path) == []


def test_validation_reuses_a1_and_keeps_unsuitable_records(tmp_path):
    from test_reporting import seeded_cycles

    from energy_monitor.dataset import CollectionRecord, prepare_dataset

    store, ids = seeded_cycles(tmp_path)
    metadata = CollectionRecord(
        cycle_id=ids[0],
        session_id="pilot",
        session_started_at=datetime(2026, 10, 1, tzinfo=UTC),
        water_volume_ml=1000,
        start_temperature_band="room",
        operating_condition="standard_boil",
        baseline_group="pilot",
    )
    genuine = prepare_dataset(store, [metadata])
    assert "non_sensor_origin" in genuine.cycles[0].quality_flags
    fixture = prepare_dataset(store, [metadata], kind="synthetic_fixture")
    assert not fixture.cycles[0].quality_flags
    assert fixture.cycles[0].collection.label == "unknown"
    missing = metadata.model_copy(update={"cycle_id": UUID(int=999)})
    assert "cycle_not_found" in prepare_dataset(store, [missing]).cycles[0].quality_flags


def test_unknown_and_unverified_unusual_do_not_enter_truth_denominators():
    from energy_monitor.dataset import evaluate_partition, fit_prepared, make_split

    dataset = prepared_fixture()
    for cycle in dataset.cycles[-2:]:
        cycle.collection.reviewed = False
        cycle.collection.label = "unusual"
        cycle.collection.label_basis = "model_only"
    plan = make_split(dataset, baseline_group="baseline")
    model = fit_prepared(dataset, plan, test_only=True)
    report = evaluate_partition(model, dataset, plan, partition="test", testing=True)
    assert report["normal_false_alerts"]["denominator"] == 18
    assert report["unusual_detection"]["denominator"] == 0
    assert report["score_distributions"]["unknown"]["count"] == 2


def test_independently_verified_unusual_cases_are_counted_separately():
    from energy_monitor.dataset import evaluate_partition, fit_prepared, make_split

    dataset = prepared_fixture()
    c = dataset.cycles[-1].collection
    c.reviewed = False
    c.label = "unusual"
    c.label_basis = "independent_observation"
    c.label_evidence = "Synthetic fixture observation, not a physical anomaly"
    c.reviewed = True
    plan = make_split(dataset, baseline_group="baseline")
    model = fit_prepared(dataset, plan, test_only=True)
    report = evaluate_partition(model, dataset, plan, partition="test", testing=True)
    assert report["normal_false_alerts"]["denominator"] == 19
    assert report["unusual_detection"]["denominator"] == 1
    assert report["status"] == "synthetic_fixture_only"


def test_collection_cli_template_bundle_and_evaluation(tmp_path):
    import json

    from energy_monitor.dataset import fit_prepared, main, make_split, write_bundle

    dataset = prepared_fixture()
    plan = make_split(dataset, baseline_group="baseline")
    bundle = tmp_path / "bundle"
    write_bundle(dataset, plan, bundle)
    assert (bundle / "cycle-features.csv").is_file()
    assert (bundle / "split-plan.json").is_file()
    model = fit_prepared(dataset, plan, test_only=True)
    model.save(tmp_path / "test-only.zip")
    assert (
        main(
            [
                "evaluate",
                "--bundle",
                str(bundle),
                "--model",
                str(tmp_path / "test-only.zip"),
                "--trust-local-model",
                "--partition",
                "test",
                "--fixture",
                "--output",
                str(tmp_path / "eval"),
            ]
        )
        == 0
    )
    result = json.loads((tmp_path / "eval/evaluation.json").read_text())
    assert result["status"] == "synthetic_fixture_only"
    assert (tmp_path / "eval/evaluation-cycles.csv").is_file()
    template = tmp_path / "blank.csv"
    assert main(["template", "--output", str(template)]) == 0
    before = template.read_bytes()
    assert main(["template", "--output", str(template)]) != 0
    assert template.read_bytes() == before


def test_quality_exclusions_and_missing_labels_do_not_become_training_cases():
    from energy_monitor.dataset import fit_prepared, make_split, training_cases

    dataset = prepared_fixture()
    dataset.cycles[0].quality_flags = ["communication_gap"]
    plan = make_split(dataset, baseline_group="baseline")
    assert len([c for c in training_cases(dataset, plan) if c.split == "train"]) == 29
    assert fit_prepared(dataset, plan, test_only=True).status == "insufficient_data"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"status": "incomplete"}, "cycle_not_completed"),
        ({"missing_sample_count": 1}, "summary_quality_exclusion"),
        ({"gap_count": 1}, "summary_quality_exclusion"),
    ],
)
def test_incomplete_missing_and_gap_records_remain_visible(tmp_path, changes, reason):
    from test_reporting import change_cycle, seeded_cycles

    from energy_monitor.dataset import CollectionRecord, prepare_dataset

    store, ids = seeded_cycles(tmp_path)
    change_cycle(store, ids[0], **changes)
    record = CollectionRecord(
        cycle_id=ids[0],
        session_id="pilot",
        session_started_at=datetime(2026, 10, 1, tzinfo=UTC),
        water_volume_ml=1000,
        start_temperature_band="room",
        operating_condition="standard_boil",
        baseline_group="pilot",
    )
    result = prepare_dataset(store, [record], kind="synthetic_fixture")
    assert reason in result.cycles[0].quality_flags
    assert result.cycles[0].eligible() is False


def test_pilot_prepare_cli_saves_quality_review_without_enough_sessions(tmp_path):
    import json

    from test_reporting import seeded_cycles

    from energy_monitor.dataset import CollectionRecord, main, write_collection_csv

    store, ids = seeded_cycles(tmp_path)
    record = CollectionRecord(
        cycle_id=ids[0],
        session_id="pilot",
        session_started_at=datetime(2026, 10, 1, tzinfo=UTC),
        water_volume_ml=1000,
        start_temperature_band="room",
        operating_condition="standard_boil",
        baseline_group="pilot",
    )
    notes = tmp_path / "notes.csv"
    write_collection_csv(notes, [record])
    assert (
        main(
            [
                "prepare",
                "--database",
                str(tmp_path / "reports.db"),
                "--collection",
                str(notes),
                "--baseline-group",
                "pilot",
                "--fixture",
                "--output",
                str(tmp_path / "pilot"),
            ]
        )
        == 0
    )
    status = json.loads((tmp_path / "pilot/preparation-status.json").read_text())
    assert status["cycles"] == 1 and status["split_status"] != "ready"
    assert status["production_model_trained"] is False
    assert (tmp_path / "pilot/cycle-features.csv").is_file()
    assert not (tmp_path / "pilot/split-plan.json").exists()


def test_dataset_duplicates_bad_csv_and_unavailable_model(tmp_path):
    from energy_monitor.anomaly import CycleAnomalyModel
    from energy_monitor.dataset import (
        PreparedDataset,
        evaluate_partition,
        make_split,
        read_collection_csv,
        write_collection_csv,
    )

    dataset = prepared_fixture()
    with pytest.raises(ValueError, match="duplicate"):
        PreparedDataset(kind="synthetic_fixture", cycles=[dataset.cycles[0], dataset.cycles[0]])
    path = tmp_path / "bad.csv"
    write_collection_csv(path, [dataset.cycles[0].collection])
    path.write_text(
        path.read_text(encoding="utf-8-sig").replace(",True,", ",maybe,"), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        read_collection_csv(path)
    result = evaluate_partition(
        CycleAnomalyModel(),
        dataset,
        make_split(dataset, baseline_group="baseline"),
        partition="test",
        testing=True,
    )
    assert result["status"] == "model_unavailable"
    assert result["normal_false_alerts"]["rate"] is None


def test_wilson_interval_has_denominators_and_no_zero_failure_guarantee():
    from energy_monitor.dataset import _rate

    assert _rate(0, 0)["rate"] is None
    result = _rate(0, 20)
    assert result["rate"] == 0
    assert result["wilson_95"][1] == pytest.approx(0.161125, abs=0.00001)


def test_detector_version_mixing_is_not_a_comparable_baseline():
    from energy_monitor.dataset import make_split

    dataset = prepared_fixture()
    dataset.cycles[-1].quality_indicators["detection_version"] = "changed-calibration"
    with pytest.raises(ValueError, match="scope"):
        make_split(dataset, baseline_group="baseline")
