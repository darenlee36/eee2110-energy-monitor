"""Offline publication and prospective evaluation of an experimental warning cutoff."""
from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from energy_monitor.dataset import make_split, prepare_dataset, read_collection_csv
from energy_monitor.experimental_if import (
    RELEASE_PATH,
    _fit_current_model,
    _score_trace,
    display_score,
    load_release,
    scope_issue,
)
from energy_monitor.pilot import PILOT_FEATURE_VERSION
from energy_monitor.pilot_workflow import (
    _seal,
    predict_experiment,
    restore_experiment,
    save_immutable,
)
from energy_monitor.storage import SQLiteTelemetryStore

POLICY_DIRECTORY = RELEASE_PATH.parent / "adaptive-warnings"
MIN_CALIBRATION_CYCLES = 40
MIN_CALIBRATION_SESSIONS = 4
PERCENTILE = 95
NATIVE_BOUNDARY = 50.0
METHOD = "split-conformal-upper-rank-v1"


def warning_cutoff(scores: list[float]) -> float:
    if len(scores) < MIN_CALIBRATION_CYCLES or any(not 0 <= score <= 100 for score in scores):
        raise ValueError("At least 40 eligible held-out normal scores are required.")
    rank = math.ceil((len(scores) + 1) * PERCENTILE / 100)
    # Requiring a strict exceedance of the order statistic handles tied scores.
    cutoff = math.nextafter(max(NATIVE_BOUNDARY, sorted(scores)[rank - 1]), math.inf)
    if cutoff > 100:
        raise ValueError("The calibration scores do not leave room for a warning cutoff.")
    return cutoff


def calibrate_warning(store, collection, *, baseline_group: str, train_sessions: int,
                      validation_sessions: int, effective_at: datetime) -> dict:
    dataset = prepare_dataset(store, collection)
    if dataset.kind != "sensor_collection":
        raise ValueError("Genuine sensor collection required.")
    plan = make_split(dataset, baseline_group=baseline_group, train_sessions=train_sessions,
                      validation_sessions=validation_sessions)
    calibrated_at = datetime.now(UTC)
    if effective_at.tzinfo is None or effective_at <= calibrated_at:
        raise ValueError("Choose a future activation time with an explicit timezone.")
    effective_at = effective_at.astimezone(UTC)
    buckets = {"train": [], "validation": []}
    for cycle in dataset.cycles:
        record = cycle.collection
        identity = str(record.cycle_id)
        partition = plan.assignments.get(identity)
        if (partition not in buckets or not cycle.eligible() or cycle.verified_label() != "normal"
                or not record.label_evidence.strip() or record.label_verified_at is None
                or cycle.ended_at is None or cycle.started_at is None
                or not cycle.started_at < cycle.ended_at <= record.label_verified_at
                or record.label_verified_at > calibrated_at):
            continue
        buckets[partition].append(cycle)
    sessions = len({cycle.collection.session_id for cycle in buckets["validation"]})
    if (len(buckets["train"]) < 6 or len(buckets["validation"]) < MIN_CALIBRATION_CYCLES
            or sessions < MIN_CALIBRATION_SESSIONS):
        raise ValueError(
            f"Calibration pending. Reviewed normal cycles: {len(buckets['train'])} training, "
            f"{len(buckets['validation'])} held out across {sessions} sessions. "
            "Required: 6 training and 40 held out across at least 4 sessions."
        )

    seed_forest, release = load_release()
    seed_model = release["model"]
    reference = seed_model["training_reference"]
    audited = {"train": [], "validation": []}
    for partition, candidates in buckets.items():
        for candidate in candidates:
            cycle = store.fetch_cycle(str(candidate.collection.cycle_id))
            if cycle is None or scope_issue(cycle, seed_model):
                raise ValueError("Reviewed cycle falls outside the current model scope.")
            row = _score_trace(cycle, store, seed_forest, seed_model)
            if row["collection_trace_sha256"] != candidate.quality_indicators["raw_trace_hash"]:
                raise ValueError("Reviewed telemetry changed during calibration.")
            audited[partition].append({"cycle_id": row["cycle_id"], "features": row["features"]})
    training = sorted(audited["train"], key=lambda cycle: cycle["cycle_id"])
    model = _fit_current_model(json.dumps(training, sort_keys=True, allow_nan=False),
                               json.dumps(reference, sort_keys=True))
    forest, _ = restore_experiment(model)
    held_out = []
    for row in sorted(audited["validation"], key=lambda cycle: cycle["cycle_id"]):
        prediction = predict_experiment(forest, model, {
            **row, "profile_id": reference["profile_id"],
            "device_scope_sha256": reference["device_scope_sha256"],
            "feature_version": PILOT_FEATURE_VERSION,
        })
        if prediction["reference_member"]:
            raise ValueError("Training and calibration overlap.")
        held_out.append({
            "cycle_id": row["cycle_id"], "score": display_score(prediction["raw_score"]),
        })
    return _seal({
        "method": METHOD, "percentile": PERCENTILE, "calibration_count": len(held_out),
        "calibration_session_count": sessions,
        "threshold": warning_cutoff([row["score"] for row in held_out]),
        "calibrated_at": calibrated_at.isoformat(), "effective_at": effective_at.isoformat(),
        "dataset_sha256": dataset.digest(),
        "split_sha256": plan.digest(), "detection_version": plan.scope["detection_version"],
        "calibration": held_out, "model": model,
    })


@lru_cache(maxsize=16)
def load_warning_policy(path: Path) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    members = {row["cycle_id"] for row in policy["calibration"]}
    calibrated = datetime.fromisoformat(policy["calibrated_at"])
    effective = datetime.fromisoformat(policy["effective_at"])
    if (policy != _seal(policy) or path.stem != policy["artifact_sha256"]
            or policy["method"] != METHOD or policy["percentile"] != PERCENTILE
            or policy["calibration_count"] != len(members)
            or policy["calibration_session_count"] < MIN_CALIBRATION_SESSIONS
            or len(members) != len(policy["calibration"])
            or members.intersection(policy["model"]["training_cycle_ids"])
            or calibrated.tzinfo is None or effective.tzinfo is None or effective <= calibrated
            or policy["threshold"] != warning_cutoff([r["score"] for r in policy["calibration"]])):
        raise ValueError("Adaptive warning artifact failed validation.")
    return policy


@lru_cache(maxsize=4)
def _warning_forest(path: Path):
    return restore_experiment(load_warning_policy(path)["model"])[0]


def assess_warning(cycle: dict, audited: dict, directory: Path = POLICY_DIRECTORY) -> dict | None:
    """Replay the frozen policy effective when the audited cycle started."""
    start = datetime.fromisoformat(audited["started_at"])
    available = []
    for path in sorted(directory.glob("*.json")):
        policy = load_warning_policy(path)
        if (scope_issue(cycle, policy["model"]) is None
                and cycle.get("detection_version", "sim-cycle-v1") == policy["detection_version"]
                and datetime.fromisoformat(policy["effective_at"]) <= start):
            available.append((policy["effective_at"], path, policy))
    if not available:
        return None
    _, path, policy = max(available)
    model = policy["model"]
    reference = model["training_reference"]
    if any(r["cycle_id"] == audited["cycle_id"] for r in policy["calibration"]):
        return None
    prediction = predict_experiment(_warning_forest(path), model, {
        **audited, "profile_id": cycle["profile_id"],
        "device_scope_sha256": reference["device_scope_sha256"],
        "feature_version": PILOT_FEATURE_VERSION,
    })
    if prediction["reference_member"]:
        return None
    return {**prediction, "score": display_score(prediction["raw_score"]), "policy": policy}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--baseline-group", required=True)
    parser.add_argument("--train-sessions", type=int, required=True)
    parser.add_argument("--validation-sessions", type=int, required=True)
    parser.add_argument("--effective-at", type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("Existing telemetry cache required.")
    try:
        policy = calibrate_warning(
            SQLiteTelemetryStore(args.database), read_collection_csv(args.collection),
            baseline_group=args.baseline_group, train_sessions=args.train_sessions,
            validation_sessions=args.validation_sessions, effective_at=args.effective_at,
        )
        path = POLICY_DIRECTORY / (policy["artifact_sha256"] + ".json")
        save_immutable(path, policy)
    except ValueError as exc:
        parser.error(str(exc))
    print(path)


if __name__ == "__main__":
    main()
