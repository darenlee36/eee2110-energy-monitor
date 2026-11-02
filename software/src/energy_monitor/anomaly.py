"""Profile/device-scoped Isolation Forest. No automatic training or AI calls.

0--100 is an empirical upper-tail percentile of normal-baseline decision values,
with half-weight ties. Lower raw decision values produce higher unusualness.
Classification uses decision_function < 0, not a 50-point score cutoff. This is
not a calibrated equipment-failure probability or a causal explanation.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import pickle
import statistics
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from energy_monitor.models import CycleSummary

FEATURE_VERSION = "cycle-features-v1"
FEATURE_NAMES = (
    "duration_seconds", "energy_kwh", "average_power_w", "peak_power_w",
    "average_current_a", "voltage_span_v", "average_power_factor",
    "frequency_span_hz", "sampling_gap_count", "invalid_fraction",
)
MIN_TRAINING_CYCLES = 30  # Engineering gate, not proof of statistical sufficiency.
NORMALIZATION = "baseline_upper_tail_percentile_v1"


class CycleFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    cycle_id: UUID
    profile_id: str
    device_id: str
    data_origin: Literal["sensor", "simulated", "manual", "mixed", "unknown"]
    values: dict[str, float | None]
    issues: list[str]

    @model_validator(mode="after")
    def valid_vector(self):
        if set(self.values) != set(FEATURE_NAMES):
            raise ValueError("feature schema differs")
        if any(v is not None and (not math.isfinite(v) or v < 0)
               for v in self.values.values()):
            raise ValueError("non-finite or negative feature")
        for key in ("average_power_factor", "invalid_fraction"):
            if self.values[key] is not None and self.values[key] > 1:
                raise ValueError("invalid fraction")
        if (self.values["sampling_gap_count"] or self.values["invalid_fraction"] or
            self.values["duration_seconds"] == 0 or self.values["energy_kwh"] == 0):
            if "quality_feature_exclusion" not in self.issues:
                self.issues.append("quality_feature_exclusion")
        return self

    def vector(self) -> list[float]:
        if self.issues or any(v is None for v in self.values.values()):
            raise ValueError("unsuitable cycle features")
        return [self.values[key] for key in FEATURE_NAMES]


class TrainingCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    features: CycleFeatures
    label: Literal["normal", "anomalous", "unknown"]
    split: Literal["train", "held_out"]
    reviewed: bool  # Explicit human review of measurements and comparable conditions.


class AnomalyPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    status: Literal["not_trained", "insufficient_data", "quality_excluded", "scope_mismatch",
                    "test_only_model", "training_cycle", "scored", "test_only"]
    classification: Literal["normal", "anomalous", "unavailable"] = "unavailable"
    anomaly_score: float | None = Field(default=None, ge=0, le=100)
    raw_decision: float | None = None
    model_version: str | None = None
    training_status: str = "not_trained"
    data_origin: str
    quality_warnings: list[str] = Field(default_factory=list)
    normalization: Literal["baseline_upper_tail_percentile_v1"] = NORMALIZATION
    is_failure_probability: Literal[False] = False
    comparisons: dict[str, dict[str, float | None]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent(self):
        scored = self.status in ("scored", "test_only")
        if scored != (self.anomaly_score is not None and self.raw_decision is not None):
            raise ValueError("score and training status disagree")
        if scored and (not self.model_version or self.classification == "unavailable"):
            raise ValueError("prediction provenance unavailable")
        if scored and self.classification != ("anomalous" if self.raw_decision < 0 else "normal"):
            raise ValueError("classification differs from decision")
        if not scored and (self.classification != "unavailable" or self.raw_decision is not None
                           or self.anomaly_score is not None):
            raise ValueError("unavailable prediction contains a verdict")
        return self


def extract_cycle_features(cycle: dict, readings: list[dict]) -> CycleFeatures:
    summary = CycleSummary.model_validate({k: v for k, v in cycle.items()
                                          if k in CycleSummary.model_fields})
    issues = []
    if summary.status != "completed" or summary.duration_seconds <= 0:
        issues.append("cycle_not_completed")
    if (summary.invalid_sample_count or summary.missing_sample_count or summary.gap_count):
        issues.append("summary_quality_exclusion")
    if summary.energy_difference_ratio > .20:
        issues.append("energy_crosscheck_warning")
    if len(readings) != summary.sample_count or len(readings) < 2:
        issues.append("raw_sample_count_mismatch")
    frequency_span = None
    try:
        times = [datetime.fromisoformat(str(r["timestamp"])) for r in readings]
        gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)]
        if any(t.tzinfo is None for t in times) or any(g <= 0 or g > 7.5 for g in gaps):
            issues.append("irregular_sampling")
        for r in readings:
            if r["quality_status"] != "valid":
                issues.append("invalid_reading")
            bounds = {"frequency_hz": (40, 70), "voltage_v": (0, 300),
                      "current_a": (0, 100), "active_power_w": (0, 25000),
                      "cumulative_energy_kwh": (0, float("inf")), "power_factor": (0, 1)}
            for key, (low, high) in bounds.items():
                value = float(r[key])
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError("out-of-range reading")
            if summary.data_origin == "sensor" and not str(
                    r.get("firmware_version", "")).startswith("pzem-"):
                issues.append("origin_trace_mismatch")
        energy = [float(r["cumulative_energy_kwh"]) for r in readings]
        if any(b < a for a, b in zip(energy, energy[1:], strict=False)):
            issues.append("meter_counter_reset")
        if readings:
            frequency = [float(r["frequency_hz"]) for r in readings]
            frequency_span = max(frequency) - min(frequency)
    except (KeyError, TypeError, ValueError, OverflowError):
        issues.append("invalid_raw_measurements")
    source = "manual" if cycle.get("record_source") == "manual" else summary.data_origin
    if source == "manual":
        issues.append("manual_record_without_sensor_trace")
    return CycleFeatures(
        cycle_id=summary.cycle_id, profile_id=summary.profile_id, device_id=summary.device_id,
        data_origin=source, issues=sorted(set(issues)), values={
            "duration_seconds": summary.duration_seconds, "energy_kwh": summary.meter_energy_kwh,
            "average_power_w": summary.average_power_w, "peak_power_w": summary.peak_power_w,
            "average_current_a": summary.average_current_a,
            "voltage_span_v": summary.maximum_voltage_v - summary.minimum_voltage_v,
            "average_power_factor": summary.average_power_factor,
            "frequency_span_hz": frequency_span, "sampling_gap_count": summary.gap_count,
            "invalid_fraction": summary.invalid_sample_count / max(1, summary.sample_count),
        })


def normalized_score(decision: float, reference: list[float]) -> float:
    if not reference or not all(math.isfinite(x) for x in [decision, *reference]):
        raise ValueError("finite baseline decisions required")
    return 100 * (sum(x > decision for x in reference) +
                  .5 * sum(x == decision for x in reference)) / len(reference)


@dataclass
class CycleAnomalyModel:
    estimator: object | None = None
    metadata: dict = field(default_factory=lambda: {"status": "not_trained"})

    @property
    def status(self) -> str:
        return self.metadata["status"]

    def predict(self, features: CycleFeatures, *, testing: bool = False) -> AnomalyPrediction:
        features = CycleFeatures.model_validate(features.model_dump())
        reason = self.status
        if self.estimator is not None:
            if self.status == "test_only" and not testing:
                reason = "test_only_model"
            elif (features.profile_id, features.device_id) != (
                    self.metadata["profile_id"], self.metadata["device_id"]):
                reason = "scope_mismatch"
            elif (features.issues or any(v is None for v in features.values.values()) or
                  (features.data_origin != "sensor" and not testing)):
                reason = "quality_excluded"
            elif str(features.cycle_id) in self.metadata["training_cycle_ids"] and not testing:
                reason = "training_cycle"
            else:
                raw = float(self.estimator.decision_function([features.vector()])[0])
                comparisons = {}
                for key in FEATURE_NAMES:
                    median = self.metadata["medians"][key]
                    delta = features.values[key] - median
                    comparisons[key] = {"value": features.values[key], "baseline_median": median,
                                        "delta": delta, "percent_delta":
                                        100 * delta / median if median else None}
                return AnomalyPrediction(
                    status="test_only" if self.status == "test_only" else "scored",
                    classification="anomalous" if raw < 0 else "normal",
                    anomaly_score=normalized_score(raw, self.metadata["reference_decisions"]),
                    raw_decision=raw, model_version=self.metadata["model_version"],
                    training_status=self.status, data_origin=features.data_origin,
                    comparisons=comparisons)
        return AnomalyPrediction(status=reason, data_origin=features.data_origin,
                                 training_status=self.status,
                                 model_version=self.metadata.get("model_version"),
                                 quality_warnings=features.issues)

    def save(self, path: Path) -> None:
        if self.estimator is None:
            raise ValueError("no fitted model to save")
        payload = pickle.dumps(self.estimator, protocol=5)
        metadata = {**self.metadata, "payload_sha256": hashlib.sha256(payload).hexdigest()}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("metadata.json", json.dumps(metadata, allow_nan=False))
            archive.writestr("estimator.pkl", payload)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                name = handle.name
                handle.write(buffer.getvalue())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)

    @classmethod
    def load(cls, path: Path, *, trusted_local: bool = False) -> CycleAnomalyModel:
        # A hash detects corruption, NOT malicious pickle content. Never accept uploads.
        if not trusted_local:
            raise ValueError("model loading requires an explicitly trusted local artifact")
        import sklearn
        from sklearn.ensemble import IsolationForest
        try:
            with zipfile.ZipFile(path) as archive:
                if any(i.file_size > 20_000_000 for i in archive.infolist()):
                    raise ValueError("oversized model")
                metadata = json.loads(archive.read("metadata.json"))
                payload = archive.read("estimator.pkl")
            if (metadata["feature_version"] != FEATURE_VERSION or
                metadata["feature_names"] != list(FEATURE_NAMES) or
                metadata["sklearn_version"] != sklearn.__version__ or
                metadata["python_version"] != list(sys.version_info[:2]) or
                metadata["payload_sha256"] != hashlib.sha256(payload).hexdigest()):
                raise ValueError("incompatible or corrupt model artifact")
            references = metadata["reference_decisions"]
            if (len(references) != metadata["training_count"] or
                metadata["training_count"] < MIN_TRAINING_CYCLES or
                len(set(metadata["training_cycle_ids"])) != metadata["training_count"] or
                set(metadata["medians"]) != set(FEATURE_NAMES) or
                not all(math.isfinite(v) for v in [*references, *metadata["medians"].values()]) or
                (metadata["status"] == "trained" and metadata["training_origins"] != ["sensor"])):
                raise ValueError("invalid training provenance")
            estimator = pickle.loads(payload)  # noqa: S301 -- explicit trusted-local boundary.
            if (type(estimator) is not IsolationForest or
                estimator.n_features_in_ != len(FEATURE_NAMES)):
                raise ValueError("invalid estimator")
            if metadata["status"] not in ("trained", "test_only"):
                raise ValueError("invalid training status")
            model = cls(estimator, metadata)
            return model
        except (OSError, KeyError, TypeError, zipfile.BadZipFile, EOFError,
                pickle.UnpicklingError) as exc:
            raise ValueError("invalid model artifact") from exc


def train_model(cases: list[TrainingCase], *, test_only: bool = False) -> CycleAnomalyModel:
    import sklearn
    from sklearn.ensemble import IsolationForest

    cases = [TrainingCase.model_validate(c.model_dump()) for c in cases]
    ids = [str(c.features.cycle_id) for c in cases]
    if len(set(ids)) != len(ids):
        return CycleAnomalyModel(metadata={"status": "insufficient_data"})
    selected = [c.features for c in cases if c.split == "train" and c.label == "normal"
                and c.reviewed and not c.features.issues
                and all(v is not None for v in c.features.values.values())
                and (test_only or c.features.data_origin == "sensor")]
    if len({(f.profile_id, f.device_id) for f in selected}) > 1:
        raise ValueError("one profile/device scope required")
    if len(selected) < MIN_TRAINING_CYCLES:
        return CycleAnomalyModel(metadata={"status": "insufficient_data"})
    selected.sort(key=lambda f: str(f.cycle_id))
    matrix = [f.vector() for f in selected]
    if all(row == matrix[0] for row in matrix):
        return CycleAnomalyModel(metadata={"status": "insufficient_data"})
    estimator = IsolationForest(n_estimators=100, contamination=.05, random_state=42, n_jobs=1)
    estimator.fit(matrix)
    identity = json.dumps([FEATURE_VERSION, test_only, sklearn.__version__,
                           [f.model_dump(mode="json") for f in selected]], sort_keys=True)
    version = "isoforest-v1-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
    return CycleAnomalyModel(estimator, {
        "status": "test_only" if test_only else "trained", "model_version": version,
        "feature_version": FEATURE_VERSION, "feature_names": list(FEATURE_NAMES),
        "sklearn_version": sklearn.__version__, "python_version": list(sys.version_info[:2]),
        "profile_id": selected[0].profile_id, "device_id": selected[0].device_id,
        "training_count": len(selected), "training_cycle_ids": [str(f.cycle_id) for f in selected],
        "review_status": "human_reviewed_normal_comparable_conditions",
        "training_origins": sorted({f.data_origin for f in selected}),
        "held_out_cycle_ids": [str(c.features.cycle_id) for c in cases if c.split == "held_out"],
        "dataset_manifest_hash": hashlib.sha256(json.dumps(
            [c.model_dump(mode="json") for c in cases], sort_keys=True).encode()).hexdigest(),
        "contamination": .05, "random_state": 42,
        "normalization": NORMALIZATION,
        "reference_decisions": estimator.decision_function(matrix).tolist(),
        "medians": {key: statistics.median(row[i] for row in matrix)
                    for i, key in enumerate(FEATURE_NAMES)},
    })


def model_evidence(model: CycleAnomalyModel, cycles: list[dict]) -> dict:
    predictions = []
    for cycle in cycles:
        features = extract_cycle_features(cycle, cycle.get("readings", []))
        predictions.append({"cycle_id": cycle["cycle_id"],
                            **model.predict(features).model_dump(mode="json")})
    return {"status": model.status, "model_version": model.metadata.get("model_version"),
            "feature_version": FEATURE_VERSION, "normalization": NORMALIZATION,
            "training_count": model.metadata.get("training_count", 0),
            "anomaly_evaluation": "evaluated" if any(
                p["status"] == "scored" for p in predictions) else "not_evaluated",
            "predictions": predictions}


def validate_model_evidence(evidence: dict, cycles: list[dict]) -> None:
    if evidence.get("feature_version") != FEATURE_VERSION:
        raise ValueError("unsupported model evidence schema")
    predictions = evidence["predictions"]
    if len(predictions) != len(cycles):
        raise ValueError("model prediction coverage differs")
    for cycle, result in zip(cycles, predictions, strict=True):
        fields = dict(result)
        if fields.pop("cycle_id") != cycle["cycle_id"]:
            raise ValueError("model prediction cycle differs")
        prediction = AnomalyPrediction.model_validate(fields)
        if prediction.anomaly_score is not None:
            if (prediction.status != "scored" or evidence["status"] != "trained" or
                prediction.model_version != evidence["model_version"] or
                prediction.training_status != "trained" or cycle["data_origin"] != "sensor" or
                prediction.data_origin != "sensor" or not cycle["eligible_for_totals"] or
                extract_cycle_features(cycle, cycle.get("readings", [])).issues):
                raise ValueError("unsuitable data/model cannot display a genuine score")
            if prediction.classification != (
                    "anomalous" if prediction.raw_decision < 0 else "normal"):
                raise ValueError("classification differs from decision value")


def configured_model() -> CycleAnomalyModel | None:
    """Opt-in operator-owned local file only; never train or accept browser uploads."""
    path = os.environ.get("ENERGY_MONITOR_ANOMALY_MODEL")
    if not path:
        return None
    try:
        return CycleAnomalyModel.load(Path(path), trusted_local=True)
    except ValueError:
        return CycleAnomalyModel(metadata={"status": "not_trained"})
