"""Opt-in descriptive pilot comparison. No production training or fault verdicts."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from datetime import datetime
from pathlib import Path

from energy_monitor.anomaly import extract_cycle_features
from energy_monitor.cycles import _integrated_energy_kwh, _meter_energy_kwh
from energy_monitor.models import TelemetryReading

PILOT_VERSION = "pilot-deviation-v1"
WEIGHTS = {"duration_seconds": 0.7, "average_power_w": 0.3}
FLOORS = {"duration_seconds": 20.0, "average_power_w": 50.0}


def pilot_reference(item: dict) -> dict:
    """Validate a Complete-labelled cloud cycle against its stored sensor trace."""
    cycle, readings = item["cycle"], item["readings"]
    if (item["annotation"].get("label", "").strip().casefold() != "complete"
            or cycle["status"] != "completed" or cycle["record_source"] != "measured"
            or cycle["data_origin"] != "sensor"):
        raise ValueError("Requires completed sensor status and the Complete annotation")
    features = extract_cycle_features(cycle, readings)
    if features.issues:
        raise ValueError("A1 quality gate: " + ", ".join(features.issues))
    times = [datetime.fromisoformat(r["timestamp"]) for r in readings]
    sequences = [r["sample_sequence"] for r in readings]
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)]
    if (any((r["device_id"], r["profile_id"]) !=
            (cycle["device_id"], cycle["profile_id"]) for r in readings)
            or len(set(sequences)) != len(sequences)
            or any(b != a + 1 for a, b in zip(sequences, sequences[1:], strict=False))
            or sequences[0] != cycle["start_sequence"]
            or sequences[-1] != cycle["end_sequence"]
            or times[0] != datetime.fromisoformat(cycle["started_at"])
            or times[-1] != datetime.fromisoformat(cycle["ended_at"])):
        raise ValueError("Sensor scope, sequence or boundary mismatch")
    cadence = statistics.median(gaps)
    if cadence not in (3, 5) or any(g > cadence * 1.5 for g in gaps):
        raise ValueError("Missing sampling interval")
    powers = [float(r["active_power_w"]) for r in readings]
    duration = (times[-1] - times[0]).total_seconds()
    parsed = [TelemetryReading.model_validate(
        {k: v for k, v in r.items() if k in TelemetryReading.model_fields}) for r in readings]
    before = item.get("predecessor")
    predecessor = None if before is None else TelemetryReading.model_validate(
        {k: v for k, v in before.items() if k in TelemetryReading.model_fields})
    if predecessor is not None and (
        (predecessor.device_id, predecessor.profile_id) !=
        (cycle["device_id"], cycle["profile_id"])
        or predecessor.sample_sequence + 1 != sequences[0]
        or not 0 < (times[0] - predecessor.timestamp).total_seconds() <= cadence * 1.5
        or predecessor.cumulative_energy_kwh > readings[0]["cumulative_energy_kwh"]
    ):
        raise ValueError("Invalid predecessor for meter-energy calculation")
    energy = _meter_energy_kwh(parsed, predecessor)
    integrated = _integrated_energy_kwh(parsed)

    raw_mean = statistics.fmean(powers)
    for actual, stored, tolerance in (
        (duration, cycle["duration_seconds"], 0.001),
        (energy, cycle["meter_energy_kwh"], 0.000001),
        (integrated, cycle["integrated_energy_kwh"], 0.000001),
        (raw_mean, cycle["average_power_w"], 0.01),
    ):
        if not math.isclose(actual, stored, abs_tol=tolerance, rel_tol=0):
            raise ValueError("Stored cycle summary differs from sensor trace")
    arrival_lags = [(datetime.fromisoformat(r["received_at"]) - t).total_seconds()
                    for r, t in zip(readings, times, strict=True)]
    return {
        "cycle_id": str(features.cycle_id),
        "device_id": features.device_id, "profile_id": features.profile_id,
        "started_at": cycle["started_at"], "ended_at": cycle["ended_at"],
        "annotation_id": item["annotation"].get("id"),
        "independently_reviewed": False,
        "features": dict(features.values),
        "additional_features": {
            "energy_derived_power_w": energy * 3_600_000 / duration,
            "power_population_std_w": statistics.pstdev(powers),
            "power_cv_percent": statistics.pstdev(powers) / raw_mean * 100,
        },
        "quality": {
            "sample_count": len(readings), "cadence_seconds": cadence,
            "largest_gap_seconds": max(gaps), "sequence_gaps": 0,
            "energy_difference_ratio": cycle["energy_difference_ratio"],
            "maximum_arrival_lag_seconds": max(arrival_lags),
            "firmware_versions": sorted({r["firmware_version"] for r in readings}),
        },
    }


def pilot_parameters(features: list[dict]) -> dict:
    result = {}
    for field in WEIGHTS:
        values = [float(f[field]) for f in features]
        if not values or any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("Finite, non-negative pilot features required")
        median = statistics.median(values)
        mad = statistics.median(abs(v - median) for v in values)
        result[field] = {"median": median, "mad": mad,
                         "scale": max(1.4826 * mad, FLOORS[field]), "weight": WEIGHTS[field]}
    return result


def pilot_score(features: dict, parameters: dict) -> dict:
    contributions = {}
    points = 0.0
    for field in WEIGHTS:
        value = float(features[field])
        if not math.isfinite(value) or value < 0:
            raise ValueError("Finite, non-negative pilot features required")
        parameter = parameters[field]
        deviation = abs(value - parameter["median"]) / parameter["scale"]
        contribution = 100 * parameter["weight"] * deviation / 3
        points += contribution
        contributions[field] = {
            "value": value, "reference_median": parameter["median"],
            "scale": parameter["scale"], "scaled_deviation": deviation,
            "weighted_points": contribution,
        }
    return {"score": min(100.0, points), "uncapped_points": points,
            "contributions": contributions}


def build_pilot_baseline(snapshot: dict) -> dict:
    records, excluded = [], list(snapshot.get("excluded") or [])
    for item in snapshot["cycles"]:
        try:
            records.append(pilot_reference(item))
        except (ValueError, KeyError, TypeError, IndexError, ZeroDivisionError) as exc:
            excluded.append({"cycle_id": item.get("cycle", {}).get("cycle_id"),
                             "reason": str(exc)})
    if len(records) < 6:
        raise ValueError(f"Six eligible pilot cycles required; found {len(records)}. {excluded}")
    scopes = {(r["device_id"], r["profile_id"]) for r in records}
    ids = [r["cycle_id"] for r in records]
    if len(scopes) != 1 or len(set(ids)) != len(ids):
        raise ValueError("Reference cycles require one scope and unique cycle IDs")
    reference_json = json.dumps(records, sort_keys=True, allow_nan=False)
    digest = hashlib.sha256(reference_json.encode()).hexdigest()
    parameters = pilot_parameters([r["features"] for r in records])
    for record in records:
        record["pilot"] = pilot_score(record["features"], parameters)
        record["reference_member"] = True
    return {
        "version": PILOT_VERSION, "baseline_id": PILOT_VERSION + "-" + digest[:12],
        "reference_sha256": digest, "source": snapshot["source"],
        "parameters": parameters, "cycles": records, "excluded": excluded,
        "ml_assessment": "not_evaluated", "production_model_status": "not_trained",
        "power_definition": "Arithmetic mean of stored PZEM active_power_w samples",
        "descriptive_statistics": {
            key: {"median": statistics.median(r["features"][key] for r in records),
                  "minimum": min(r["features"][key] for r in records),
                  "maximum": max(r["features"][key] for r in records)}
            for key in ("duration_seconds", "energy_kwh", "average_power_w")
        },
        "limitations": [
            "Six same-day cycles; independent collection-session labels are unavailable.",
            "Complete is a public collection annotation; independent normal review is pending.",
            "Estimated 700–800 mL; exact volume and initial temperature are unknown.",
            "Weights, MAD multiplier, scale floors and score mapping are provisional.",
            "Higher scores describe deviation; equipment-failure probability is uncalibrated.",
            "Raw Modbus provenance and independent calibration remain unverified.",
        ],
    }


def analyze_pilot(snapshot: dict, *, explore_isolation_forest: bool = False) -> dict:
    baseline = build_pilot_baseline(snapshot)
    features = [r["features"] for r in baseline["cycles"]]
    correlations = {}
    for left, right in (("duration_seconds", "energy_kwh"),
                        ("duration_seconds", "average_power_w"),
                        ("energy_kwh", "average_power_w")):
        correlations[left + " vs " + right] = statistics.correlation(
            [f[left] for f in features], [f[right] for f in features])
    baseline["pearson_correlations"] = correlations
    for index, record in enumerate(baseline["cycles"]):
        alternatives = []
        for duration_weight in (0.5, 0.7, 0.9):
            for duration_floor in (10, 20, 30):
                for power_floor in (25, 50, 100):
                    params = {k: dict(v) for k, v in baseline["parameters"].items()}
                    params["duration_seconds"]["weight"] = duration_weight
                    params["average_power_w"]["weight"] = 1 - duration_weight
                    for field, floor in (("duration_seconds", duration_floor),
                                         ("average_power_w", power_floor)):
                        params[field]["scale"] = max(1.4826 * params[field]["mad"], floor)
                    alternatives.append(pilot_score(record["features"], params)["score"])
        other_features = [f for i, f in enumerate(features) if i != index]
        record["sensitivity"] = {
            "27_parameter_variants_min": min(alternatives),
            "27_parameter_variants_max": max(alternatives),
            "leave_one_out_score": pilot_score(
                record["features"], pilot_parameters(other_features))["score"],
            "independent_evaluation": False,
        }
    if explore_isolation_forest:
        import sklearn
        from sklearn.ensemble import IsolationForest

        matrix = [[f[k] for k in WEIGHTS] for f in features]
        forest = IsolationForest(n_estimators=200, max_samples=len(matrix),
                                 contamination="auto", random_state=42)
        forest.fit(matrix)
        baseline["exploratory_isolation_forest"] = {
            "status": "in_sample_exploration_only", "sklearn_version": sklearn.__version__,
            "random_state": 42, "n_estimators": 200, "max_samples": len(matrix),
            "features": list(WEIGHTS), "validated": False, "artifact_saved": False,
            "decision_values": [float(v) for v in forest.decision_function(matrix)],
            "raw_scores": [float(v) for v in forest.score_samples(matrix)],
        }
    return baseline



PUBLIC_REFERENCE_PATH = (
    Path(__file__).resolve().parents[2] / "config/pilot/kettle-six-cycles-v1.json"
)
PILOT_FEATURE_VERSION = "duration-measured-power-v1"


def public_pilot_reference(snapshot: dict) -> dict:
    """Publish approved cycle summaries while keeping raw traces and notes local."""
    baseline = build_pilot_baseline(snapshot)
    cycles = [{
        "cycle_id": c["cycle_id"], "started_at": c["started_at"],
        "features": {k: c["features"][k] for k in WEIGHTS},
        "energy_kwh": c["features"]["energy_kwh"],
        "sample_count": c["quality"]["sample_count"],
    } for c in baseline["cycles"]]
    reference = {
        "schema_version": 1, "algorithm": PILOT_VERSION,
        "feature_version": PILOT_FEATURE_VERSION, "baseline_id": baseline["baseline_id"],
        "profile_id": baseline["cycles"][0]["profile_id"],
        "device_scope_sha256": hashlib.sha256(
            baseline["cycles"][0]["device_id"].encode()).hexdigest(),
        "reference_date": "2026-11-05", "source": "Supabase sensor cycle summaries",
        "source_reference_sha256": baseline["reference_sha256"],
        "annotation_eligibility": "Complete", "independently_reviewed": False,
        "parameters": baseline["parameters"], "cycles": cycles,
        "ml_assessment": "not_evaluated",
    }
    reference["artifact_sha256"] = hashlib.sha256(
        json.dumps(reference, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return reference


def load_pilot_reference(path: Path = PUBLIC_REFERENCE_PATH) -> dict:
    reference = json.loads(path.read_text(encoding="utf-8"))
    unsigned = {k: v for k, v in reference.items() if k != "artifact_sha256"}
    digest = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, allow_nan=False).encode()).hexdigest()
    cycles = reference["cycles"]
    if (reference["schema_version"] != 1 or reference["algorithm"] != PILOT_VERSION
            or reference["feature_version"] != PILOT_FEATURE_VERSION
            or reference["ml_assessment"] != "not_evaluated"
            or reference["independently_reviewed"] is not False
            or len(cycles) != 6 or len({c["cycle_id"] for c in cycles}) != 6
            or reference["artifact_sha256"] != digest
            or reference["parameters"] != pilot_parameters([c["features"] for c in cycles])):
        raise ValueError("Invalid frozen pilot reference")
    return reference


def pilot_reference_member(reference: dict, cycle: dict) -> dict | None:
    scope = hashlib.sha256(cycle["device_id"].encode()).hexdigest()
    if (cycle["profile_id"] != reference["profile_id"]
            or scope != reference["device_scope_sha256"]
            or cycle.get("data_origin") != "sensor"
            or cycle.get("record_source") != "measured"
            or cycle.get("status") != "completed"):
        return None
    for member in reference["cycles"]:
        if member["cycle_id"] == cycle["cycle_id"]:
            for key in WEIGHTS:
                if not math.isclose(member["features"][key], cycle[key], abs_tol=0.01):
                    raise ValueError("Saved report differs from frozen reference")
            return {**member, "pilot": pilot_score(member["features"], reference["parameters"])}
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--explore-isolation-forest", action="store_true")
    args = parser.parse_args()
    result = analyze_pilot(json.loads(args.snapshot.read_text(encoding="utf-8")),
                           explore_isolation_forest=args.explore_isolation_forest)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

