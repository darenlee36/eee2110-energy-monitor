"""A2 collection manifests and session-held-out evaluation; no activation or AI.

All measurements/features come from the existing store and A1 extractor. CSV
contains operator annotations only. A split is frozen against the full manifest.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from energy_monitor.anomaly import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    CycleAnomalyModel,
    CycleFeatures,
    TrainingCase,
    extract_cycle_features,
    train_model,
)
from energy_monitor.storage import SQLiteTelemetryStore

DATASET_VERSION = "kettle-collection-v1"


TRACE_FIELDS = (
    "timestamp",
    "sample_sequence",
    "device_uptime_ms",
    "voltage_v",
    "current_a",
    "active_power_w",
    "cumulative_energy_kwh",
    "frequency_hz",
    "power_factor",
    "quality_status",
    "firmware_version",
)

QUALITY_KEYS = (
    "status",
    "sample_count",
    "valid_sample_count",
    "invalid_sample_count",
    "missing_sample_count",
    "gap_count",
    "largest_gap_seconds",
    "detection_version",
)


def reading_fingerprint(readings: list[dict]) -> str:
    return _digest({"readings": [{k: r.get(k) for k in TRACE_FIELDS} for r in readings]})


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class CollectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)
    cycle_id: UUID
    session_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    session_started_at: AwareDatetime
    water_volume_ml: float = Field(gt=0, le=5000)
    start_temperature_band: Literal["room", "cool", "warm", "unknown"] = "unknown"
    operating_condition: Literal["standard_boil", "normal_reboil", "other", "unknown"] = "unknown"
    baseline_group: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    reviewed: bool = False
    label: Literal["normal", "unusual", "unknown"] = "unknown"
    label_basis: Literal[
        "normal_observation", "independent_observation", "model_only", "unknown"
    ] = "unknown"
    label_evidence: str = Field(default="", max_length=1000)
    operator: str = Field(default="", max_length=100)
    notes: str = Field(default="", max_length=1000)
    water_start_temperature_c: float | None = Field(default=None, ge=0, le=100)
    ambient_temperature_c: float | None = Field(default=None, ge=0, le=60)
    label_verified_at: AwareDatetime | None = None

    @field_validator("session_started_at", "label_verified_at")
    @classmethod
    def utc_time(cls, value):
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def independent_labels(self):
        if self.reviewed and self.label != "unknown":
            required = "normal_observation" if self.label == "normal" else "independent_observation"
            if self.label_basis != required or not self.operator.strip():
                raise ValueError("reviewed labels need independent operator evidence")
            if self.label == "unusual" and not self.label_evidence.strip():
                raise ValueError("unusual labels need an independent observation")
        return self


CSV_COLUMNS = tuple(CollectionRecord.model_fields)


class DatasetCycle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection: CollectionRecord
    device_id: str | None = None
    profile_id: str | None = None
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    features: CycleFeatures | None = None
    quality_flags: list[str]
    quality_indicators: dict = Field(default_factory=dict)

    def scope(self) -> dict:
        c = self.collection
        return {
            "device_id": self.device_id,
            "profile_id": self.profile_id,
            "baseline_group": c.baseline_group,
            "water_volume_ml": c.water_volume_ml,
            "start_temperature_band": c.start_temperature_band,
            "operating_condition": c.operating_condition,
            "detection_version": self.quality_indicators.get("detection_version", "unknown"),
            "firmware_versions": self.quality_indicators.get("firmware_versions", []),
        }

    def eligible(self) -> bool:
        return (
            self.features is not None
            and not self.quality_flags
            and not self.features.issues
            and self.collection.start_temperature_band != "unknown"
            and self.collection.operating_condition in ("standard_boil", "normal_reboil")
        )

    def verified_label(self) -> str:
        c = self.collection
        return c.label if c.reviewed and c.label_basis != "model_only" else "unknown"


class PreparedDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["kettle-collection-v1"] = DATASET_VERSION
    feature_version: Literal["cycle-features-v1"] = FEATURE_VERSION
    kind: Literal["sensor_collection", "synthetic_fixture"] = "sensor_collection"
    cycles: list[DatasetCycle]

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [c.collection.cycle_id for c in self.cycles]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate cycle IDs in collection")
        return self

    def digest(self) -> str:
        value = self.model_dump(mode="json")
        value["cycles"].sort(key=lambda c: c["collection"]["cycle_id"])
        return _digest(value)


class ReportCollectionBinding(BaseModel):
    """Public report subset: never persist operator names, notes or label evidence."""

    model_config = ConfigDict(extra="forbid")
    version: Literal["cycle-context-v1"] = "cycle-context-v1"
    status: Literal["bound", "missing", "mismatch", "unreviewed", "quality_excluded", "unavailable"]
    cycle_id: UUID
    dataset_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    dataset_kind: Literal["sensor_collection", "synthetic_fixture"] | None = None
    session_id: str | None = None
    session_started_at: AwareDatetime | None = None
    scope: dict | None = None
    reviewed: bool = False
    raw_trace_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    feature_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    quality_indicators: dict = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_binding(self):
        if self.status in ("bound", "unreviewed", "quality_excluded"):
            if not all(
                (
                    self.dataset_hash,
                    self.dataset_kind,
                    self.session_id,
                    self.session_started_at,
                    self.scope,
                    self.raw_trace_hash,
                    self.feature_hash,
                )
            ):
                raise ValueError("incomplete collection binding")
            # Reuse the annotation's primitive condition/time validation only;
            # private reviewer/label fields are deliberately absent from reports.
            CollectionRecord(
                cycle_id=self.cycle_id,
                session_id=self.session_id,
                session_started_at=self.session_started_at,
                **{
                    k: self.scope[k]
                    for k in (
                        "water_volume_ml",
                        "start_temperature_band",
                        "operating_condition",
                        "baseline_group",
                    )
                },
            )
        if self.status == "bound" and (not self.reviewed or self.issues):
            raise ValueError("unreviewed or excluded context cannot be bound for scoring")
        return self


def bind_report_context(dataset: PreparedDataset, cycle: dict) -> dict:
    source = next(
        (c for c in dataset.cycles if str(c.collection.cycle_id) == cycle["cycle_id"]), None
    )
    result = ReportCollectionBinding(
        status="missing",
        cycle_id=cycle["cycle_id"],
        dataset_hash=dataset.digest(),
        dataset_kind=dataset.kind,
        issues=["collection_record_missing"],
    )
    if source is None:
        return result.model_dump(mode="json")
    peers = [
        c
        for c in dataset.cycles
        if (c.device_id, c.profile_id, c.collection.session_id)
        == (source.device_id, source.profile_id, source.collection.session_id)
    ]
    if (
        source.started_at is None
        or source.collection.session_started_at > source.started_at
        or any(
            c.collection.session_started_at != source.collection.session_started_at for c in peers
        )
    ):
        return result.model_copy(
            update={"status": "mismatch", "issues": ["collection_session_mismatch"]}
        ).model_dump(mode="json")
    features = extract_cycle_features(cycle, cycle.get("readings", []))
    raw_hash = reading_fingerprint(cycle.get("readings", []))
    actual_scope = {
        **source.scope(),
        "device_id": cycle["device_id"],
        "profile_id": cycle["profile_id"],
        "detection_version": cycle["detection_version"],
        "firmware_versions": sorted({r["firmware_version"] for r in cycle.get("readings", [])}),
    }
    if (
        source.features is None
        or source.features.model_dump() != features.model_dump()
        or source.started_at != datetime.fromisoformat(cycle["started_at"])
        or source.ended_at != datetime.fromisoformat(cycle["ended_at"])
        or source.scope() != actual_scope
        or source.quality_indicators.get("raw_trace_hash") != raw_hash
        or any(source.quality_indicators.get(k) != cycle[k] for k in QUALITY_KEYS)
    ):
        return result.model_copy(
            update={"status": "mismatch", "issues": ["collection_measurements_mismatch"]}
        ).model_dump(mode="json")
    flags = sorted(set(source.quality_flags + features.issues))
    status = (
        "quality_excluded" if flags else "bound" if source.collection.reviewed else "unreviewed"
    )
    return ReportCollectionBinding(
        status=status,
        cycle_id=cycle["cycle_id"],
        dataset_hash=dataset.digest(),
        dataset_kind=dataset.kind,
        session_id=source.collection.session_id,
        session_started_at=source.collection.session_started_at,
        scope=actual_scope,
        reviewed=source.collection.reviewed,
        raw_trace_hash=raw_hash,
        feature_hash=_digest(features.model_dump(mode="json")),
        quality_indicators={k: cycle[k] for k in QUALITY_KEYS},
        issues=flags or ([] if source.collection.reviewed else ["collection_review_pending"]),
    ).model_dump(mode="json")


def validate_report_binding(cycle: dict) -> ReportCollectionBinding | None:
    if "collection_context" not in cycle:
        return None
    binding = ReportCollectionBinding.model_validate(cycle["collection_context"])
    if str(binding.cycle_id) != cycle["cycle_id"]:
        raise ValueError("collection context belongs to a different cycle")
    if binding.status in ("bound", "unreviewed", "quality_excluded"):
        features = extract_cycle_features(cycle, cycle.get("readings", []))
        if (
            binding.scope["device_id"] != cycle["device_id"]
            or binding.scope["profile_id"] != cycle["profile_id"]
            or binding.scope["detection_version"] != cycle["detection_version"]
            or binding.scope["firmware_versions"]
            != sorted({r["firmware_version"] for r in cycle.get("readings", [])})
            or binding.session_started_at > datetime.fromisoformat(cycle["started_at"])
            or binding.raw_trace_hash != reading_fingerprint(cycle.get("readings", []))
            or binding.feature_hash != _digest(features.model_dump(mode="json"))
            or binding.quality_indicators != {k: cycle[k] for k in QUALITY_KEYS}
        ):
            raise ValueError("collection evidence differs from saved measurements")
    return binding


class SplitPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["session-chronological-v1"] = "session-chronological-v1"
    dataset_hash: str
    scope: dict
    session_order: list[str]
    train_sessions: int = Field(gt=0)
    validation_sessions: int = Field(gt=0)
    assignments: dict[str, Literal["train", "validation", "test"]]

    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


def write_collection_csv(path: Path, records: list[CollectionRecord] = ()) -> None:
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for record in records:
            writer.writerow(record.model_dump(mode="json"))


def read_collection_csv(path: Path) -> list[CollectionRecord]:
    records = []
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(CSV_COLUMNS):
            raise ValueError("collection CSV header differs from the template")
        for line, row in enumerate(reader, 2):
            if not any(row.values()):
                continue
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"malformed CSV row {line}")
            values = {k: v.strip() for k, v in row.items() if v.strip()}
            if "reviewed" in values:
                if values["reviewed"].lower() not in ("true", "false"):
                    raise ValueError(f"reviewed must be TRUE/FALSE on row {line}")
                values["reviewed"] = values["reviewed"].lower() == "true"
            records.append(CollectionRecord.model_validate(values))
    return records


def prepare_dataset(
    store: SQLiteTelemetryStore,
    records: list[CollectionRecord],
    *,
    kind: Literal["sensor_collection", "synthetic_fixture"] = "sensor_collection",
) -> PreparedDataset:
    cycles = []
    for record in records:
        record = CollectionRecord.model_validate(record.model_dump())
        row = store.fetch_cycle(str(record.cycle_id))
        if row is None:
            cycles.append(DatasetCycle(collection=record, quality_flags=["cycle_not_found"]))
            continue
        flags = []
        raw = store.fetch_cycle_readings(str(record.cycle_id))
        try:
            features = extract_cycle_features(row, raw)
            flags.extend(features.issues)
        except (TypeError, ValueError, KeyError):
            features = None
            flags.append("invalid_cycle_features")
        if kind == "sensor_collection" and (features is None or features.data_origin != "sensor"):
            flags.append("non_sensor_origin")
        if datetime.fromisoformat(row["started_at"]) < record.session_started_at:
            flags.append("cycle_precedes_recording_session")
        if record.start_temperature_band == "unknown" or record.operating_condition in (
            "unknown",
            "other",
        ):
            flags.append("operating_conditions_unverified")
        try:
            trace_hash = reading_fingerprint(raw)
        except (TypeError, ValueError):
            trace_hash = None
        cycles.append(
            DatasetCycle(
                collection=record,
                device_id=row["device_id"],
                profile_id=row["profile_id"],
                started_at=row["started_at"],
                ended_at=row["ended_at"],
                features=features,
                quality_flags=sorted(set(flags)),
                quality_indicators={
                    k: row[k]
                    for k in (
                        "status",
                        "sample_count",
                        "valid_sample_count",
                        "invalid_sample_count",
                        "missing_sample_count",
                        "gap_count",
                        "largest_gap_seconds",
                        "quality_note",
                    )
                },
            )
        )
        cycles[-1].quality_indicators.update(
            detection_version=row["detection_version"],
            firmware_versions=sorted({str(r.get("firmware_version", "unknown")) for r in raw}),
            raw_trace_hash=trace_hash,
        )
    return PreparedDataset(kind=kind, cycles=cycles)


def make_split(
    dataset: PreparedDataset,
    *,
    baseline_group: str,
    train_sessions: int = 3,
    validation_sessions: int = 1,
) -> SplitPlan:
    dataset = PreparedDataset.model_validate(dataset.model_dump())
    rows = [
        c
        for c in dataset.cycles
        if c.collection.baseline_group == baseline_group and c.device_id is not None
    ]
    if not rows:
        raise ValueError("no scoped collection cycles")
    scope = rows[0].scope()
    if any(c.scope() != scope for c in rows):
        raise ValueError("one device/profile/volume/condition scope per baseline group required")
    if scope["start_temperature_band"] == "unknown" or scope["operating_condition"] in (
        "unknown",
        "other",
    ):
        raise ValueError("known comparable operating conditions required")
    sessions = {}
    for cycle in rows:
        sid = cycle.collection.session_id
        start = cycle.collection.session_started_at
        if sid in sessions and sessions[sid][0] != start:
            raise ValueError("inconsistent recording session start")
        if cycle.started_at is None or cycle.ended_at is None:
            continue
        if cycle.started_at < start:
            raise ValueError("cycle precedes session")
        sessions[sid] = (start, max(cycle.ended_at, sessions.get(sid, (start, start))[1]))
    order = sorted(sessions, key=lambda sid: (sessions[sid][0], sid))
    if (
        train_sessions < 1
        or validation_sessions < 1
        or len(order) <= train_sessions + validation_sessions
    ):
        raise ValueError("separate training, validation and later test sessions required")
    if any(sessions[a][1] >= sessions[b][0] for a, b in zip(order, order[1:], strict=False)):
        raise ValueError("recording sessions overlap; chronological independence unconfirmed")
    partitions = {
        sid: "train"
        if i < train_sessions
        else "validation"
        if i < train_sessions + validation_sessions
        else "test"
        for i, sid in enumerate(order)
    }
    return SplitPlan(
        dataset_hash=dataset.digest(),
        scope=scope,
        session_order=order,
        train_sessions=train_sessions,
        validation_sessions=validation_sessions,
        assignments={
            str(c.collection.cycle_id): partitions[c.collection.session_id]
            for c in rows
            if c.collection.session_id in partitions
        },
    )


def validate_split(dataset: PreparedDataset, plan: SplitPlan) -> None:
    if dataset.digest() != plan.dataset_hash:
        raise ValueError("frozen dataset hash differs; explicitly review/refreeze collection")
    expected = make_split(
        dataset,
        baseline_group=plan.scope["baseline_group"],
        train_sessions=plan.train_sessions,
        validation_sessions=plan.validation_sessions,
    )
    if plan != expected:
        raise ValueError("split differs from frozen chronological session plan")


def training_cases(dataset: PreparedDataset, plan: SplitPlan) -> list[TrainingCase]:
    validate_split(dataset, plan)
    cases = []
    for cycle in dataset.cycles:
        partition = plan.assignments.get(str(cycle.collection.cycle_id))
        if partition is None or not cycle.eligible():
            continue
        label = cycle.verified_label()
        cases.append(
            TrainingCase(
                features=cycle.features,
                label="anomalous" if label == "unusual" else label,
                split="train" if partition == "train" else "held_out",
                reviewed=cycle.collection.reviewed,
            )
        )
    return cases


def fit_prepared(
    dataset: PreparedDataset, plan: SplitPlan, *, test_only: bool
) -> CycleAnomalyModel:
    if test_only != (dataset.kind == "synthetic_fixture"):
        raise ValueError("fixture data cannot train a production model; explicit mode required")
    model = train_model(training_cases(dataset, plan), test_only=test_only)
    model.metadata.update(
        dataset_hash=dataset.digest(),
        split_hash=plan.digest(),
        collection_scope=plan.scope,
        training_sessions=plan.session_order[: plan.train_sessions],
    )
    return model


def _rate(successes: int, denominator: int) -> dict:
    # Descriptive Wilson 95% interval; repeated cycles are not guaranteed iid.
    if denominator == 0:
        return {"numerator": successes, "denominator": 0, "rate": None, "wilson_95": None}
    p, z = successes / denominator, 1.959963984540054
    divisor = 1 + z * z / denominator
    center = (p + z * z / (2 * denominator)) / divisor
    half = z * math.sqrt(p * (1 - p) / denominator + z * z / (4 * denominator**2)) / divisor
    return {
        "numerator": successes,
        "denominator": denominator,
        "rate": p,
        "wilson_95": [max(0, center - half), min(1, center + half)],
    }


def _distribution(values: list[float]) -> dict:
    values = sorted(values)
    if not values:
        return {
            "count": 0,
            "minimum": None,
            "median": None,
            "p95": None,
            "maximum": None,
            "histogram_10_bins": [0] * 10,
        }
    bins = [0] * 10
    for value in values:
        bins[min(9, int(value / 10))] += 1
    return {
        "count": len(values),
        "minimum": values[0],
        "median": statistics.median(values),
        "p95": values[math.ceil(0.95 * len(values)) - 1],
        "maximum": values[-1],
        "histogram_10_bins": bins,
    }


def evaluate_partition(
    model: CycleAnomalyModel,
    dataset: PreparedDataset,
    plan: SplitPlan,
    *,
    partition: Literal["validation", "test"],
    decision_threshold: float = 0,
    testing: bool = False,
) -> dict:
    validate_split(dataset, plan)
    if partition not in ("validation", "test") or not math.isfinite(decision_threshold):
        raise ValueError("held-out partition and finite raw-decision threshold required")
    result = {
        "status": "model_unavailable",
        "partition": partition,
        "dataset_hash": dataset.digest(),
        "split_hash": plan.digest(),
        "model_version": model.metadata.get("model_version"),
        "decision_threshold": decision_threshold,
        "model_activated": False,
        "normal_false_alerts": _rate(0, 0),
        "unusual_detection": _rate(0, 0),
        "score_distributions": {},
        "cycles": [],
    }
    if model.estimator is None:
        return result
    if (dataset.kind == "synthetic_fixture") != testing or (model.status == "test_only") != testing:
        raise ValueError("fixture and model evaluation modes differ")
    if (
        model.metadata.get("dataset_hash") != dataset.digest()
        or model.metadata.get("split_hash") != plan.digest()
        or model.metadata.get("collection_scope") != plan.scope
    ):
        raise ValueError("model was not bound to this frozen dataset and split")
    expected = {
        str(c.features.cycle_id)
        for c in training_cases(dataset, plan)
        if c.split == "train" and c.label == "normal" and c.reviewed
    }
    trained = model.metadata["training_cycle_ids"]
    if set(trained) != expected or len(trained) != len(expected):
        raise ValueError("training identities differ or held-out leakage detected")
    counts = {"normal": [], "unusual": [], "unknown": []}
    flagged = {"normal": 0, "unusual": 0}
    excluded = 0
    for cycle in dataset.cycles:
        cid = str(cycle.collection.cycle_id)
        if plan.assignments.get(cid) != partition:
            continue
        if not cycle.eligible():
            excluded += 1
            continue
        prediction = model.predict(cycle.features, testing=testing, collection_scope=plan.scope)
        if prediction.anomaly_score is None:
            excluded += 1
            continue
        label = cycle.verified_label()
        is_flagged = prediction.raw_decision < decision_threshold
        counts[label].append(prediction.anomaly_score)
        if label in flagged:
            flagged[label] += int(is_flagged)
        result["cycles"].append(
            {
                "cycle_id": cid,
                "session_id": cycle.collection.session_id,
                "verified_label": label,
                "anomaly_score": prediction.anomaly_score,
                "raw_decision": prediction.raw_decision,
                "flagged_at_review_threshold": is_flagged,
                "model_classification_at_zero": prediction.classification,
            }
        )
    result.update(
        status="synthetic_fixture_only" if testing else "evaluated",
        normal_false_alerts=_rate(flagged["normal"], len(counts["normal"])),
        unusual_detection=_rate(flagged["unusual"], len(counts["unusual"])),
        score_distributions={label: _distribution(values) for label, values in counts.items()},
        excluded_cycles=excluded,
        scored_cycles=sum(map(len, counts.values())),
        limitations=[
            "Scores are not equipment-failure probabilities.",
            "Small correlated session samples limit generalization; "
            "Wilson intervals assume binomial trials.",
            "Threshold review does not change the deployed model classification.",
        ],
    )
    return result


def review_thresholds(
    model: CycleAnomalyModel,
    dataset: PreparedDataset,
    plan: SplitPlan,
    *,
    thresholds: list[float],
    testing: bool = False,
) -> list[dict]:
    return [
        evaluate_partition(
            model, dataset, plan, partition="validation", decision_threshold=t, testing=testing
        )
        for t in thresholds
    ]


def write_bundle(dataset: PreparedDataset, plan: SplitPlan | None, output: Path) -> None:
    """New output folder only; never overwrite the operator's collection or old freeze."""
    if plan is not None:
        validate_split(dataset, plan)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "dataset-manifest.json").write_text(
        dataset.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    if plan is not None:
        (output / "split-plan.json").write_text(
            plan.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
    columns = (
        "cycle_id",
        "device_id",
        "profile_id",
        "session_id",
        "session_started_at",
        "started_at",
        "ended_at",
        "baseline_group",
        "water_volume_ml",
        "start_temperature_band",
        "operating_condition",
        "data_origin",
        "verified_label",
        "reviewed",
        "analysis_eligible",
        "partition",
        "quality_flags",
        "sample_count",
        "invalid_sample_count",
        "missing_sample_count",
        "gap_count",
        "largest_gap_seconds",
        *FEATURE_NAMES,
    )
    # FEATURE_NAMES includes sampling_gap_count; gap_count is the original detector evidence.
    with (output / "cycle-features.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for cycle in dataset.cycles:
            c = cycle.collection
            row = {
                k: getattr(c, k)
                for k in (
                    "cycle_id",
                    "session_id",
                    "session_started_at",
                    "baseline_group",
                    "water_volume_ml",
                    "start_temperature_band",
                    "operating_condition",
                )
            }
            row.update(
                device_id=cycle.device_id,
                profile_id=cycle.profile_id,
                started_at=cycle.started_at,
                ended_at=cycle.ended_at,
                data_origin=cycle.features.data_origin if cycle.features else "unavailable",
                verified_label=cycle.verified_label(),
                reviewed=c.reviewed,
                analysis_eligible=cycle.eligible(),
                partition=plan.assignments.get(str(c.cycle_id), "out_of_scope")
                if plan
                else "unassigned",
                quality_flags=";".join(cycle.quality_flags),
            )
            row.update(
                {
                    k: cycle.quality_indicators.get(k)
                    for k in (
                        "sample_count",
                        "invalid_sample_count",
                        "missing_sample_count",
                        "gap_count",
                        "largest_gap_seconds",
                    )
                }
            )
            row.update(cycle.features.values if cycle.features else {})
            writer.writerow(row)


def _write_evaluation(result: dict, output: Path) -> None:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "evaluation.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    columns = (
        "cycle_id",
        "session_id",
        "verified_label",
        "anomaly_score",
        "raw_decision",
        "flagged_at_review_threshold",
        "model_classification_at_zero",
    )
    with (output / "evaluation-cycles.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(result["cycles"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A2 offline collection and held-out evaluation")
    commands = parser.add_subparsers(dest="command", required=True)
    template = commands.add_parser("template", help="Write a blank collection CSV; no measurements")
    template.add_argument("--output", required=True, type=Path)
    prepare = commands.add_parser(
        "prepare", help="Join collection notes to an existing SQLite cache"
    )
    prepare.add_argument("--database", required=True, type=Path)
    prepare.add_argument("--collection", required=True, type=Path)
    prepare.add_argument("--baseline-group", required=True)
    prepare.add_argument("--train-sessions", type=int, default=3)
    prepare.add_argument("--validation-sessions", type=int, default=1)
    prepare.add_argument("--fixture", action="store_true")
    prepare.add_argument("--output", required=True, type=Path)
    for name in ("evaluate", "review"):
        command = commands.add_parser(name, help="Existing model only; never train or activate")
        command.add_argument("--bundle", required=True, type=Path)
        command.add_argument("--model", required=True, type=Path)
        command.add_argument("--trust-local-model", required=True, action="store_true")
        command.add_argument("--fixture", action="store_true")
        command.add_argument("--output", required=True, type=Path)
        if name == "evaluate":
            command.add_argument("--partition", choices=("validation", "test"), required=True)
            command.add_argument("--raw-threshold", type=float, default=0)
        else:
            command.add_argument("--thresholds", type=float, nargs="+", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            if args.output.exists():
                raise ValueError("output already exists")
            write_collection_csv(args.output, [])
            status = {"status": "blank_template_created"}
        elif args.command == "prepare":
            if not args.database.is_file():
                raise ValueError("existing SQLite cache required")
            dataset = prepare_dataset(
                SQLiteTelemetryStore(args.database),
                read_collection_csv(args.collection),
                kind="synthetic_fixture" if args.fixture else "sensor_collection",
            )
            plan = None
            split_status = "ready"
            try:
                plan = make_split(
                    dataset,
                    baseline_group=args.baseline_group,
                    train_sessions=args.train_sessions,
                    validation_sessions=args.validation_sessions,
                )
            except ValueError as exc:
                split_status = str(exc)
            write_bundle(dataset, plan, args.output)
            status = {
                "status": "prepared",
                "kind": dataset.kind,
                "cycles": len(dataset.cycles),
                "eligible_cycles": sum(c.eligible() for c in dataset.cycles),
                "dataset_hash": dataset.digest(),
                "split_status": split_status,
                "production_model_trained": False,
            }
            (args.output / "preparation-status.json").write_text(
                json.dumps(status, indent=2) + "\n", encoding="utf-8"
            )
        else:
            dataset = PreparedDataset.model_validate_json(
                (args.bundle / "dataset-manifest.json").read_text(encoding="utf-8")
            )
            plan = SplitPlan.model_validate_json(
                (args.bundle / "split-plan.json").read_text(encoding="utf-8")
            )
            model = CycleAnomalyModel.load(args.model, trusted_local=args.trust_local_model)
            if args.command == "evaluate":
                result = evaluate_partition(
                    model,
                    dataset,
                    plan,
                    partition=args.partition,
                    decision_threshold=args.raw_threshold,
                    testing=args.fixture,
                )
                _write_evaluation(result, args.output)
                status = {"status": result["status"], "partition": args.partition}
            else:
                result = review_thresholds(
                    model, dataset, plan, thresholds=args.thresholds, testing=args.fixture
                )
                args.output.mkdir(parents=True, exist_ok=False)
                (args.output / "validation-threshold-review.json").write_text(
                    json.dumps(
                        {
                            "partition": "validation",
                            "candidate_results": result,
                            "model_activated": False,
                        },
                        indent=2,
                        allow_nan=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                status = {"status": "validation_review_only", "candidates": len(result)}
        print(json.dumps(status, allow_nan=False))
        return 0
    except (OSError, ValueError, KeyError):
        print(
            json.dumps(
                {
                    "status": "not_completed",
                    "message": "Check collection, frozen scope/split, trusted model "
                    "and new output path.",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
