from __future__ import annotations

from datetime import UTC, datetime
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

