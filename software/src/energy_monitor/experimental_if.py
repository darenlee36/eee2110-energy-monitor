"""Versioned two-feature Isolation Forest for audited genuine cycle observations."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from functools import lru_cache
from pathlib import Path

from energy_monitor.pilot import (
    PILOT_FEATURE_VERSION,
    PUBLIC_REFERENCE_PATH,
    WEIGHTS,
    load_pilot_reference,
    pilot_reference,
    pilot_score,
)
from energy_monitor.pilot_workflow import (
    _digest,
    _seal,
    fit_experiment,
    predict_experiment,
    restore_experiment,
)

RELEASE_PATH = PUBLIC_REFERENCE_PATH.with_name("kettle-if-v1.json")
SCORE_MAPPING = "negative-score-samples-times-100-v1"
CADENCES = {"pzem-fw-0.2.5": 5, **{f"pzem-fw-0.2.{n}": 3 for n in range(6, 10)}}


def display_score(raw_score: float) -> float:
    if not math.isfinite(raw_score):
        raise ValueError("Invalid Isolation Forest score.")
    return max(0.0, min(100.0, -100.0 * raw_score))


def scope_issue(cycle: dict, model: dict) -> str | None:
    if cycle.get("status") != "completed":
        return "This cycle is incomplete."
    if cycle.get("data_origin") != "sensor" or cycle.get("record_source") != "measured":
        return "A completed genuine sensor trace is required."
    reference = model["training_reference"]
    device = hashlib.sha256(cycle.get("device_id", "").encode()).hexdigest()
    if cycle.get("profile_id") != reference["profile_id"] or device != (
        reference["device_scope_sha256"]
    ):
        return "No experimental model is available for this appliance and monitor."
    return None


def audit_trace(item: dict) -> dict:
    row = pilot_reference(item)
    readings = item["readings"]
    versions = row["quality"]["firmware_versions"]
    cadence = CADENCES.get(versions[0]) if len(versions) == 1 else None
    if cadence is None or any(
        b["device_uptime_ms"] - a["device_uptime_ms"] != cadence * 1000
        for a, b in zip(readings, readings[1:], strict=False)
    ):
        raise ValueError("Unknown or discontinuous firmware sampling session.")
    row["trace_sha256"] = _digest({"readings": readings, "predecessor": item.get("predecessor")})
    return row


def assess_audited(forest, model: dict, row: dict) -> dict:
    features = {k: row["features"][k] for k in WEIGHTS}
    reference = model["training_reference"]
    prediction = predict_experiment(forest, model, {
        "cycle_id": row["cycle_id"], "profile_id": row["profile_id"],
        "device_scope_sha256": hashlib.sha256(row["device_id"].encode()).hexdigest(),
        "feature_version": PILOT_FEATURE_VERSION, "features": features,
    })
    deviation = pilot_score(features, reference["parameters"])
    if prediction["reference_member"]:
        disposition = "frozen_reference"
    elif prediction["raw_decision"] < 0:
        disposition = "quarantined_unusual"
    elif any(v["scaled_deviation"] >= 3 for v in deviation["contributions"].values()):
        disposition = "quarantined_reference_shift"
    else:
        disposition = "pending_review"
    return {
        **prediction, "features": features, "started_at": row["started_at"],
        "ended_at": row["ended_at"], "energy_kwh": row["features"]["energy_kwh"],
        "sample_count": row["quality"]["sample_count"],
        "trace_sha256": row["trace_sha256"], "quality": row["quality"],
        "score": display_score(prediction["raw_score"]),
        "frozen_reference_deviation": deviation["score"], "learning_disposition": disposition,
    }


def build_release(snapshot: dict) -> dict:
    reference = load_pilot_reference()
    model = fit_experiment(reference)
    forest, _ = restore_experiment(model)
    assessments = {}
    for item in snapshot["cycles"]:
        if issue := scope_issue(item["cycle"], model):
            raise ValueError(issue)
        row = assess_audited(forest, model, audit_trace(item))
        if row["cycle_id"] in assessments:
            raise ValueError("Duplicate cycle in the assessment release.")
        assessments[row["cycle_id"]] = row
    if not set(model["training_cycle_ids"]) <= assessments.keys():
        raise ValueError("Frozen training reference is missing from the trace audit.")
    for member in reference["cycles"]:
        row = assessments[member["cycle_id"]]
        if any(not math.isclose(row["features"][k], member["features"][k], abs_tol=1e-9)
               for k in WEIGHTS):
            raise ValueError("Audited features differ from the frozen training reference.")
    statistics_by_feature = {}
    for feature in [*WEIGHTS, "energy_kwh"]:
        values = [c.get(feature) if feature == "energy_kwh" else c["features"][feature]
                  for c in reference["cycles"]]
        statistics_by_feature[feature] = {
            "mean": statistics.fmean(values), "median": statistics.median(values),
            "stddev": statistics.pstdev(values),
        }
    return _seal({
        "schema_version": 1, "score_mapping": SCORE_MAPPING, "model": model,
        "assessments": assessments, "statistics": statistics_by_feature,
    })


@lru_cache(maxsize=4)
def load_release(path: Path = RELEASE_PATH):
    release = json.loads(path.read_text(encoding="utf-8"))
    if (release != _seal(release) or release.get("schema_version") != 1
            or release.get("score_mapping") != SCORE_MAPPING):
        raise ValueError("Experimental model release failed its integrity check.")
    forest, _ = restore_experiment(release["model"])
    return forest, release


def assess_selected_cycle(cycle: dict, store) -> tuple[dict, dict]:
    forest, release = load_release()
    model = release["model"]
    if issue := scope_issue(cycle, model):
        raise ValueError(issue)
    if recorded := release["assessments"].get(cycle["cycle_id"]):
        if any(not math.isclose(float(cycle[k]), recorded["features"][k], abs_tol=.01)
               for k in WEIGHTS):
            raise ValueError("The current cycle differs from its frozen scored evidence.")
        return recorded, release
    label = str(cycle.get("custom_label") or "").strip().casefold()
    if label == "incomplete":
        raise ValueError("This run was labelled Incomplete.")
    if label != "complete":
        raise ValueError("Label the finished cycle Complete to run its quality check and score.")
    if store is None:
        raise ValueError("The original sensor trace is unavailable for verification.")
    readings = store.fetch_cycle_readings(cycle["cycle_id"], include_predecessor=True)
    before = None
    if readings and readings[0]["sample_sequence"] == cycle["start_sequence"] - 1:
        before = readings.pop(0)
    row = audit_trace({
        "cycle": cycle, "readings": readings, "predecessor": before,
        "annotation": {"label": "Complete"},
    })
    return assess_audited(forest, model, row), release
