from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ApplianceState(StrEnum):
    OFF = "off"
    HEATING = "heating"
    UNKNOWN = "unknown"


class ConnectionState(StrEnum):
    ONLINE = "online"
    STALE = "stale"
    OFFLINE = "offline"


class AnomalyStatus(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    NORMAL = "normal"
    ANOMALY = "anomaly"


class QualityStatus(StrEnum):
    VALID = "valid"
    READ_ERROR = "read_error"
    COMMUNICATION_GAP = "communication_gap"
    POWER_INTERRUPTION = "power_interruption"


class CycleStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"


class CycleAssessment(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    NORMAL = "normal"
    UNUSUAL = "unusual"
    INSUFFICIENT_DATA = "insufficient_data"


class VolumeClass(StrEnum):
    HALF_LITRE = "0.5_l"
    ONE_LITRE = "1.0_l"
    ONE_AND_HALF_LITRES = "1.5_l"
    UNKNOWN = "unknown"


class TariffEstimateStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class CycleDetectionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1, max_length=64)
    start_power_w: float = Field(gt=0)
    start_confirm_samples: int = Field(ge=1)
    stop_power_w: float = Field(ge=0)
    stop_confirm_samples: int = Field(ge=1)
    warning_gap_seconds: int = Field(gt=0)
    terminating_gap_seconds: int = Field(gt=0)
    minimum_duration_seconds: int = Field(gt=0)
    maximum_duration_seconds: int = Field(gt=0)
    energy_difference_tolerance: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def thresholds_must_be_ordered(self) -> CycleDetectionSettings:
        if self.terminating_gap_seconds <= self.warning_gap_seconds:
            raise ValueError("terminating gap must be greater than warning gap")
        if self.maximum_duration_seconds <= self.minimum_duration_seconds:
            raise ValueError("maximum duration must be greater than minimum duration")
        return self

    @classmethod
    def simulation_defaults(cls) -> CycleDetectionSettings:
        return cls(
            version="sim-cycle-v1",
            start_power_w=1000.0,
            start_confirm_samples=2,
            stop_power_w=100.0,
            stop_confirm_samples=3,
            warning_gap_seconds=15,
            terminating_gap_seconds=60,
            minimum_duration_seconds=30,
            maximum_duration_seconds=600,
            energy_difference_tolerance=0.20,
        )


class TelemetryReading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    profile_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    timestamp: datetime
    sample_sequence: int = Field(ge=0)
    device_uptime_ms: int = Field(ge=0)
    voltage_v: float = Field(ge=0, le=300)
    current_a: float = Field(ge=0, le=100)
    active_power_w: float = Field(ge=0, le=25_000)
    cumulative_energy_kwh: float = Field(ge=0)
    frequency_hz: float = Field(ge=40, le=70)
    power_factor: float = Field(ge=0, le=1)
    appliance_state: ApplianceState
    battery_voltage_v: float = Field(ge=2.5, le=4.5)
    connection_state: ConnectionState
    anomaly_status: AnomalyStatus
    quality_status: QualityStatus
    firmware_version: str = Field(min_length=1, max_length=32)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)


class TelemetryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    readings: list[TelemetryReading] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def readings_must_share_device(self) -> TelemetryBatch:
        if len({reading.device_id for reading in self.readings}) != 1:
            raise ValueError("all readings in a batch must belong to one device")
        return self


class BatchResult(BaseModel):
    batch_id: UUID
    accepted: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    replayed: bool


class CycleSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cycle_id: UUID
    device_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    profile_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    started_at: datetime
    ended_at: datetime | None
    start_sequence: int = Field(ge=0)
    end_sequence: int | None = Field(default=None, ge=0)
    status: CycleStatus
    assessment: CycleAssessment
    duration_seconds: int = Field(ge=0)
    meter_energy_kwh: float = Field(ge=0)
    integrated_energy_kwh: float = Field(ge=0)
    energy_difference_ratio: float = Field(ge=0)
    average_power_w: float = Field(ge=0)
    peak_power_w: float = Field(ge=0)
    average_voltage_v: float = Field(ge=0)
    minimum_voltage_v: float = Field(ge=0)
    maximum_voltage_v: float = Field(ge=0)
    average_current_a: float = Field(ge=0)
    average_power_factor: float = Field(ge=0, le=1)
    sample_count: int = Field(ge=0)
    valid_sample_count: int = Field(ge=0)
    invalid_sample_count: int = Field(ge=0)
    missing_sample_count: int = Field(ge=0)
    gap_count: int = Field(ge=0)
    largest_gap_seconds: int = Field(ge=0)
    quality_note: str
    detection_version: str = Field(min_length=1, max_length=64)
    predicted_volume: VolumeClass | None = None
    prediction_confidence: float | None = Field(default=None, ge=0, le=1)
    volume_model_version: str | None = None
    effective_volume: VolumeClass = VolumeClass.UNKNOWN

    @field_validator("started_at", "ended_at")
    @classmethod
    def timestamps_must_include_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("cycle timestamps must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def cycle_boundaries_must_be_consistent(self) -> CycleSummary:
        if self.status != CycleStatus.ACTIVE and self.ended_at is None:
            raise ValueError("non-active cycles require ended_at")
        if self.end_sequence is not None and self.end_sequence < self.start_sequence:
            raise ValueError("end_sequence must not precede start_sequence")
        if self.valid_sample_count + self.invalid_sample_count != self.sample_count:
            raise ValueError("valid and invalid sample counts must equal sample_count")
        return self


class TariffEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: TariffEstimateStatus
    provider: str
    scheme: str
    tariff_version: str | None
    tariff_effective_from: str | None
    tariff_effective_to: str | None
    afa_version: str | None
    afa_period: str | None
    occurred_at: datetime
    energy_kwh: Decimal = Field(ge=0)
    monthly_household_kwh: Decimal | None = Field(default=None, ge=0)
    energy_rate_sen_per_kwh: Decimal | None = Field(default=None, ge=0)
    capacity_rate_sen_per_kwh: Decimal | None = Field(default=None, ge=0)
    network_rate_sen_per_kwh: Decimal | None = Field(default=None, ge=0)
    afa_rate_sen_per_kwh: Decimal | None = None
    gross_variable_rate_sen_per_kwh: Decimal | None = Field(default=None, ge=0)
    amount_rm: Decimal | None = Field(default=None, ge=0)
    amount_range_rm: tuple[Decimal, Decimal] | None = None
    included_components: tuple[str, ...]
    excluded_components: tuple[str, ...]
    unresolved_components: tuple[str, ...]
    source_urls: tuple[str, ...]
    last_checked_date: str | None

    @field_validator("occurred_at")
    @classmethod
    def tariff_timestamp_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(UTC)

