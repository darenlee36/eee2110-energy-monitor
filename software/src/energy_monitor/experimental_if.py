"""Versioned two-feature Isolation Forest for audited genuine cycle observations."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from energy_monitor.dataset import reading_fingerprint
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
ROLLING_POWER_VERSION = "rolling-power-v1"
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
    if cycle.get("detection_version", "sim-cycle-v1") != "sim-cycle-v1":
        return "This cycle uses different appliance rules from the frozen kettle model."
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
    if hasattr(store, "fetch_first_assessment"):
        saved = store.fetch_first_assessment(cycle["cycle_id"])
        if saved is not None:
            validate_first_assessment(saved, cycle, release)
            return saved, release
    row = _score_trace(cycle, store, forest, model)
    if hasattr(store, "append_first_assessment"):
        # A completed label may have been saved before an interrupted score write.
        try:
            row = _save_first_assessment(cycle, row, release, store)
        except ValueError:
            # The original trace still supports the score while the baseline is pending.
            pass
    return row, release


def _score_trace(cycle: dict, store, forest, model: dict) -> dict:
    readings = store.fetch_cycle_readings(cycle["cycle_id"], include_predecessor=True)
    before = None
    if readings and readings[0]["sample_sequence"] == cycle["start_sequence"] - 1:
        before = readings.pop(0)
    row = audit_trace({
        "cycle": cycle, "readings": readings, "predecessor": before,
        "annotation": {"label": "Complete"},
    })
    return {**assess_audited(forest, model, row), "collection_trace_sha256": (
        reading_fingerprint(readings)
    )}


def validate_first_assessment(row: dict, cycle: dict, release: dict) -> None:
    if (row != _seal(row)
            or row.get("cycle_id") != cycle["cycle_id"]
            or row.get("model_artifact_sha256") != release["model"]["artifact_sha256"]
            or row.get("reference_member") is not False
            or row.get("started_at") != cycle["started_at"]
            or row.get("ended_at") != cycle["ended_at"]
            or not math.isclose(row["score"], display_score(row["raw_score"]), abs_tol=1e-9)
            or any(not math.isclose(float(cycle[k]), row["features"][k], abs_tol=.01)
                   for k in WEIGHTS)):
        raise ValueError("The saved first score does not match the cycle or model.")
    _validate_rolling_reference(row.get("rolling_power"))


def preserve_first_assessment(cycle: dict, store) -> bool:
    """Persist only a verified, labelled trace outside the bundled frozen history."""
    forest, release = load_release()
    model = release["model"]
    if (scope_issue(cycle, model) or cycle["cycle_id"] in release["assessments"]
            or str(cycle.get("custom_label") or "").strip().casefold() != "complete"):
        return False
    saved = store.fetch_first_assessment(cycle["cycle_id"])
    if saved is not None:
        validate_first_assessment(saved, cycle, release)
        return False
    row = _score_trace(cycle, store, forest, model)
    _save_first_assessment(cycle, row, release, store)
    return True


def _save_first_assessment(cycle: dict, row: dict, release: dict, store) -> dict:
    prior = store.fetch_rolling_power(row["cycle_id"], list(release["assessments"]))
    reference = {**prior, "version": ROLLING_POWER_VERSION}
    _validate_rolling_reference(reference)
    row = _seal({**row, "rolling_power": reference})
    confirmed = store.append_first_assessment(row)
    validate_first_assessment(confirmed, cycle, release)
    if _seal({**row, "rolling_power": confirmed["rolling_power"]}) != confirmed:
        raise ValueError("Concurrent first score differs from this cycle evidence.")
    return confirmed


def rolling_power_comparison(row: dict, release: dict, store) -> dict | None:
    frozen = release["assessments"]
    if row["cycle_id"] in frozen or not hasattr(store, "fetch_rolling_power"):
        selected = frozen.get(row["cycle_id"], row)
        started_at = datetime.fromisoformat(selected["started_at"])
        powers = [prior["features"]["average_power_w"] for prior in frozen.values()
                  if prior["cycle_id"] != row["cycle_id"]
                  and datetime.fromisoformat(prior["ended_at"]) < started_at]
        reference = {
            "version": ROLLING_POWER_VERSION, "cycle_count": len(powers),
            "mean_power_w": statistics.fmean(powers) if powers else None,
        }
    else:
        reference = row.get("rolling_power")
    _validate_rolling_reference(reference)
    if reference["cycle_count"] == 0:
        return None
    return {**reference, "power_change_w": (
        row["features"]["average_power_w"] - reference["mean_power_w"]
    )}


def current_rolling_power_comparison(row: dict, release: dict, store) -> dict | None:
    frozen = release["assessments"]
    if hasattr(store, "fetch_current_rolling_power"):
        result = store.fetch_current_rolling_power(row["cycle_id"], list(frozen))
    else:
        powers = [prior["features"]["average_power_w"] for prior in frozen.values()]
        result = {"cycle_count": len(powers), "mean_power_w": (
            statistics.fmean(powers) if powers else None
        )}
    reference = {**result, "version": "current-rolling-power-v1"}
    _validate_rolling_reference(reference, version="current-rolling-power-v1")
    if reference["cycle_count"] == 0:
        return None
    return {**reference, "power_change_w": (
        row["features"]["average_power_w"] - reference["mean_power_w"]
    )}


def _validate_rolling_reference(
    reference: dict | None, *, version: str = ROLLING_POWER_VERSION,
) -> None:
    if not isinstance(reference, dict) or reference.get("version") != version:
        raise ValueError("A saved rolling comparison is unavailable.")
    count, mean = reference.get("cycle_count"), reference.get("mean_power_w")
    if type(count) is not int or count < 0:
        raise ValueError("Invalid prior cycle count.")
    if (count == 0 and mean is not None) or (
        count > 0 and (type(mean) not in (float, int) or not 0 < mean <= 25_000)
    ):
        raise ValueError("Invalid prior cycle mean power.")
