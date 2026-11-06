"""Explicit local pilot versioning and two-feature IF experiments; no activation or AI."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from energy_monitor.dataset import PreparedDataset, SplitPlan, validate_split
from energy_monitor.pilot import (
    PILOT_FEATURE_VERSION,
    PILOT_VERSION,
    PUBLIC_REFERENCE_PATH,
    WEIGHTS,
    pilot_parameters,
    pilot_score,
)
from energy_monitor.report_worker import worker_lock


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _seal(value: dict) -> dict:
    unsigned = {k: v for k, v in value.items() if k != "artifact_sha256"}
    return {**unsigned, "artifact_sha256": _digest(unsigned)}


def validate_reference(reference: dict) -> None:
    cycles = reference["cycles"]
    revision = reference.get("reference_revision", 1)
    if (reference != _seal(reference) or reference["schema_version"] != 1
            or reference["algorithm"] != PILOT_VERSION
            or reference["feature_version"] != PILOT_FEATURE_VERSION
            or reference["ml_assessment"] != "not_evaluated"
            or reference["independently_reviewed"] is not False
            or revision not in (1, 2) or len(cycles) < 6
            or (revision == 1 and len(cycles) != 6)
            or len({c["cycle_id"] for c in cycles}) != len(cycles)
            or reference["parameters"] != pilot_parameters([c["features"] for c in cycles])
            or not reference["baseline_id"].startswith(f"pilot-deviation-v{revision}-")):
        raise ValueError("invalid experimental reference artifact")
    if revision == 2 and not reference.get("parent_baseline_id"):
        raise ValueError("expanded reference requires parent baseline")


def assess_collection(reference: dict, dataset: PreparedDataset, plan: SplitPlan,
                      *, testing: bool = False) -> list[dict]:
    """Freeze original v1 comparisons; reviewed unknown labels stay unknown."""
    validate_reference(reference)
    if reference.get("reference_revision", 1) != 1:
        raise ValueError("incoming cycles must be scored against frozen v1 first")
    if testing != (dataset.kind == "synthetic_fixture"):
        raise ValueError("synthetic fixtures require explicit testing mode")
    validate_split(dataset, plan)
    reference_ids = {c["cycle_id"] for c in reference["cycles"]}
    latest_end = max(datetime.fromisoformat(c["started_at"]) + timedelta(
        seconds=c["features"]["duration_seconds"]) for c in reference["cycles"])
    records = []
    for cycle in dataset.cycles:
        identity = str(cycle.collection.cycle_id)
        partition = plan.assignments.get(identity)
        if partition is None:
            continue
        c, f, q = cycle.collection, cycle.features, cycle.quality_indicators
        if f is not None:
            f.vector()  # Reject missing/nonfinite features before arithmetic below.
        firmware = set(q.get("firmware_versions", []))
        cadence = (3 if firmware and firmware <= {"pzem-fw-0.2.6", "pzem-fw-0.2.7"}
                   else 5 if firmware == {"pzem-fw-0.2.5"} else None)
        if (not cycle.eligible() or not c.reviewed or not c.operator.strip()
                or f is None or f.data_origin != "sensor"
                or str(f.cycle_id) != identity or f.device_id != cycle.device_id
                or f.profile_id != cycle.profile_id
                or cycle.profile_id != reference["profile_id"]
                or hashlib.sha256(cycle.device_id.encode()).hexdigest()
                != reference["device_scope_sha256"]
                or identity in reference_ids
                or cycle.started_at is None or cycle.ended_at is None
                or cycle.started_at <= latest_end
                or not math.isclose((cycle.ended_at - cycle.started_at).total_seconds(),
                                    f.values["duration_seconds"], abs_tol=0.001)
                or q.get("status") != "completed" or q.get("sample_count", 0) < 6
                or cadence is None
                or q.get("largest_gap_seconds", math.inf) > cadence * 1.5
                or abs(q["sample_count"] - (f.values["duration_seconds"] / cadence + 1)) > 1
                or any(q.get(key) != 0 for key in (
                    "invalid_sample_count", "missing_sample_count", "gap_count"))
                or not re.fullmatch(r"[a-f0-9]{64}", str(q.get("raw_trace_hash", "")))):
            raise ValueError(f"ineligible incoming cycle: {identity}")
        features = {key: f.values[key] for key in WEIGHTS}
        records.append({
            "schema_version": 1, "baseline_id": reference["baseline_id"],
            "reference_sha256": reference["artifact_sha256"],
            "feature_version": PILOT_FEATURE_VERSION, "cycle_id": identity,
            "profile_id": cycle.profile_id,
            "device_scope_sha256": reference["device_scope_sha256"],
            "started_at": cycle.started_at.isoformat(), "ended_at": cycle.ended_at.isoformat(),
            "features": features, "energy_kwh": f.values["energy_kwh"],
            "sample_count": q["sample_count"], "raw_trace_hash": q["raw_trace_hash"],
            "pilot": pilot_score(features, reference["parameters"]),
            "reference_parameters": copy.deepcopy(reference["parameters"]),
            "dataset_hash": dataset.digest(), "split_hash": plan.digest(),
            "partition": partition, "session_id": c.session_id,
            "session_started_at": c.session_started_at.isoformat(),
            "collection_scope": cycle.scope(), "label": cycle.verified_label(),
            "reviewed": True, "reference_member": False, "testing": testing,
            "ml_assessment": "not_evaluated",
        })
    if not records:
        raise ValueError("no eligible scoped incoming cycles")
    return records


def save_immutable(path: Path, value: dict) -> dict:
    """Share the existing OS lock; atomic checkpoint, no overwrites or automatic retries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with worker_lock(path.with_suffix(path.suffix + ".lock")) as acquired:
        if not acquired:
            raise ValueError("artifact busy; retry explicitly")
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != value:
                raise ValueError("immutable artifact differs; preserve original and review")
            return existing
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(value, handle, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return value


def expand_reference(reference: dict, dataset: PreparedDataset, plan: SplitPlan,
                     cycle_ids: list[str], ledger: Path, *, testing: bool = False) -> dict:
    records = assess_collection(reference, dataset, plan, testing=testing)
    if not cycle_ids or len(cycle_ids) != len(set(cycle_ids)):
        raise ValueError("explicit unique reviewed training cycle IDs required")
    by_id = {r["cycle_id"]: r for r in records}
    rows = {str(c.collection.cycle_id): c for c in dataset.cycles}
    selected = []
    for identity in cycle_ids:
        UUID(identity)
        record = by_id.get(identity)
        if record is None or record["partition"] != "train":
            raise ValueError("only training sessions may expand the reference")
        row = rows[identity]
        if (record["label"] != "normal" or row.collection.label_verified_at is None
                or row.collection.label_verified_at < row.ended_at):
            raise ValueError("independently reviewed normal observation required")
        path = ledger / (identity + ".json")
        if not path.exists() or json.loads(path.read_text(encoding="utf-8")) != record:
            raise ValueError("saved v1 assessment required before expansion")
        selected.append(record)
    expanded = copy.deepcopy(reference)
    expanded.update(
        reference_revision=2, parent_baseline_id=reference["baseline_id"],
        parent_artifact_sha256=reference["artifact_sha256"],
        expansion_dataset_sha256=dataset.digest(), expansion_split_sha256=plan.digest(),
        selected_reviewed_training_ids=sorted(cycle_ids), testing=testing)
    expanded["cycles"] += [{key: r[key] for key in (
        "cycle_id", "started_at", "features", "energy_kwh", "sample_count")} for r in selected]
    expanded["cycles"].sort(key=lambda c: c["cycle_id"])
    expanded["parameters"] = pilot_parameters([c["features"] for c in expanded["cycles"]])
    expanded["baseline_id"] = "pilot-deviation-v2-" + _digest({
        "parent": reference["artifact_sha256"], "dataset": dataset.digest(),
        "selected": sorted(cycle_ids)})[:12]
    expanded = _seal(expanded)
    validate_reference(expanded)
    return expanded


def fit_experiment(reference: dict) -> dict:
    """Two features, one vector per cycle. Artifact reconstructs deterministically, no pickle."""
    import sklearn
    from sklearn.ensemble import IsolationForest

    validate_reference(reference)
    matrix = [[c["features"][k] for k in WEIGHTS] for c in reference["cycles"]]
    settings = {"n_estimators": 200, "max_samples": len(matrix),
                "contamination": "auto", "random_state": 42, "n_jobs": 1}
    forest = IsolationForest(**settings).fit(matrix)
    result = {
        "schema_version": 1, "model_version": "pilot-if-two-features-v1",
        "status": "experimental_in_sample_only", "validated": False,
        "production_activation": False, "feature_version": PILOT_FEATURE_VERSION,
        "feature_names": list(WEIGHTS), "sklearn_version": sklearn.__version__,
        "settings": settings, "training_reference": reference,
        "training_cycle_ids": [c["cycle_id"] for c in reference["cycles"]],
        "in_sample_decisions": [float(v) for v in forest.decision_function(matrix)],
        "in_sample_raw_scores": [float(v) for v in forest.score_samples(matrix)],
        "independent_validation_cycles": 0, "failure_probability": None,
    }
    return _seal(result)


def load_experiment(path: Path):
    return restore_experiment(json.loads(path.read_text(encoding="utf-8")))


def restore_experiment(artifact: dict):
    import sklearn
    from sklearn.ensemble import IsolationForest

    expected = fit_experiment(artifact["training_reference"])
    scores = {"in_sample_decisions", "in_sample_raw_scores"}
    metadata = scores | {"artifact_sha256"}
    if (artifact != _seal(artifact)
            or artifact["sklearn_version"] != sklearn.__version__
            or {k: v for k, v in artifact.items() if k not in metadata}
            != {k: v for k, v in expected.items() if k not in metadata}
            or any(len(artifact[k]) != len(expected[k]) or any(
                not math.isclose(a, b, abs_tol=1e-12)
                for a, b in zip(artifact[k], expected[k], strict=True)
            ) for k in scores)):
        raise ValueError("invalid or incompatible experimental model artifact")
    reference = artifact["training_reference"]
    matrix = [[c["features"][k] for k in WEIGHTS] for c in reference["cycles"]]
    return IsolationForest(**artifact["settings"]).fit(matrix), artifact


def predict_experiment(forest, artifact: dict, assessment: dict) -> dict:
    reference = artifact["training_reference"]
    if (assessment["profile_id"] != reference["profile_id"]
            or assessment["device_scope_sha256"] != reference["device_scope_sha256"]
            or assessment["feature_version"] != PILOT_FEATURE_VERSION
            or assessment.get("testing", False) != reference.get("testing", False)):
        raise ValueError("experimental prediction scope differs")
    features = assessment["features"]
    pilot_score(features, reference["parameters"])  # Reject missing/nonfinite inputs.
    matrix = [[features[k] for k in WEIGHTS]]
    member = assessment["cycle_id"] in artifact["training_cycle_ids"]
    return {
        "cycle_id": assessment["cycle_id"], "model_artifact_sha256": artifact["artifact_sha256"],
        "raw_decision": float(forest.decision_function(matrix)[0]),
        "raw_score": float(forest.score_samples(matrix)[0]),
        "reference_member": member, "classification": "not_evaluated",
        "evaluation_role": "in_sample" if member else "unvalidated_new_cycle",
        "failure_probability": None,
    }


def score_experiment(forest, artifact: dict, reference: dict) -> list[dict]:
    validate_reference(reference)
    return [predict_experiment(forest, artifact, {
        **c, "profile_id": reference["profile_id"],
        "device_scope_sha256": reference["device_scope_sha256"],
        "feature_version": reference["feature_version"],
        "testing": reference.get("testing", False),
    }) for c in reference["cycles"]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fit", "assess", "expand"))
    parser.add_argument("--reference", type=Path, default=PUBLIC_REFERENCE_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--split", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--cycle-id", action="append", default=[])
    parser.add_argument("--testing", action="store_true")
    args = parser.parse_args()
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    if args.command == "fit":
        save_immutable(args.output, fit_experiment(reference))
    else:
        if not all((args.dataset, args.split, args.ledger)):
            parser.error("collection commands require --dataset, --split and --ledger")
        dataset = PreparedDataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
        plan = SplitPlan.model_validate_json(args.split.read_text(encoding="utf-8"))
        if args.command == "assess":
            records = assess_collection(reference, dataset, plan, testing=args.testing)
            for record in records:
                save_immutable(args.ledger / (record["cycle_id"] + ".json"), record)
            save_immutable(args.output, {"assessments": records})
        else:
            save_immutable(args.output, expand_reference(
                reference, dataset, plan, args.cycle_id, args.ledger, testing=args.testing))


if __name__ == "__main__":
    main()
