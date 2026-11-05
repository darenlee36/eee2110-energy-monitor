from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from energy_monitor.models import TelemetryReading


def sample_interval_seconds(firmware: str) -> int | None:
    """Verified firmware schedules, not network upload/arrival intervals."""
    if firmware == "sim-0.1.0":
        return 5
    version = re.fullmatch(r"(?:pzem|sim)-fw-0\.2\.(\d+)", firmware)
    if version:
        patch = int(version[1])
        return 5 if patch <= 5 else 3 if patch in (6, 7) else None
    return None


def sampling_gap_counts(readings, *, legacy_seconds: int | None = None) -> list[int]:
    """Estimated missing slots per record (first=0); no cloud-receipt dependence.

    Unknown firmware needs >=3 consistent consecutive-sequence observations at
    either 3 or 5 seconds. Sequence holes remain evidence even without a cadence.
    Device/profile/origin transitions do not create sampling-loss counts.
    """
    rows = [r.model_dump() if isinstance(r, TelemetryReading) else r for r in readings]
    def stamp(row):
        value = row["timestamp"]
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    def origin(row):
        firmware = row.get("firmware_version", "")
        return "sensor" if firmware.startswith("pzem-") else (
            "simulated" if firmware.startswith("sim-") else "unknown")
    def scope(row):
        return row.get("device_id"), row.get("profile_id"), origin(row)
    observed = defaultdict(list)
    for previous, current in zip(rows, rows[1:], strict=False):
        gap = (stamp(current) - stamp(previous)).total_seconds()
        sequence_step = (current["sample_sequence"] - previous["sample_sequence"]
                         if isinstance(current.get("sample_sequence"), int)
                         and isinstance(previous.get("sample_sequence"), int) else None)
        if (scope(previous) == scope(current)
                and previous.get("firmware_version") == current.get("firmware_version")
                and sequence_step in (None, 1)):
            observed[(*scope(current), current.get("firmware_version"))].append(gap)
    inferred = {}
    for key, gaps in observed.items():
        # Reject mixed/ambiguous schedules; long gaps are not an observed cadence.
        candidates = [v for v in (3, 5) if sum(abs(g - v) <= .5 for g in gaps) >= 3]
        if len(candidates) == 1:
            inferred[key] = candidates[0]
    result = [0] if rows else []
    for previous, current in zip(rows, rows[1:], strict=False):
        if scope(previous) != scope(current):
            result.append(0)
            continue
        schedules = [sample_interval_seconds(r.get("firmware_version", ""))
                     or inferred.get((*scope(r), r.get("firmware_version")))
                     for r in (previous, current)]
        cadence = max(schedules) if all(schedules) else legacy_seconds
        gap = (stamp(current) - stamp(previous)).total_seconds()
        missing = (max(1, round(gap / cadence) - 1)
                   if cadence and gap > cadence * 1.5 else 0)
        if (isinstance(current.get("sample_sequence"), int)
                and isinstance(previous.get("sample_sequence"), int)
                and current.get("device_uptime_ms", 0) >= previous.get("device_uptime_ms", 0)):
            missing = max(missing, current["sample_sequence"] - previous["sample_sequence"] - 1)
        result.append(max(0, missing))
    return result


class AlertLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DashboardAlert(BaseModel):
    code: str
    level: AlertLevel
    title: str
    message: str


def derive_connection_state(
    last_timestamp: datetime,
    now: datetime,
    stale_after_seconds: int = 45,
    offline_after_seconds: int = 120,
) -> str:
    if last_timestamp.tzinfo is None or now.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    age_seconds = max(0.0, (now - last_timestamp).total_seconds())
    if age_seconds <= stale_after_seconds:
        return "online"
    if age_seconds <= offline_after_seconds:
        return "stale"
    return "offline"


def calculate_energy_used_kwh(cumulative_values: list[float]) -> float:
    if len(cumulative_values) < 2:
        return 0.0
    total = sum(
        max(0.0, later - earlier)
        for earlier, later in zip(cumulative_values, cumulative_values[1:], strict=False)
    )
    return round(total, 6)


def calculate_cost_rm(energy_kwh: float, rate_rm_per_kwh: float) -> float:
    if energy_kwh < 0 or rate_rm_per_kwh < 0:
        raise ValueError("energy and tariff rate must be non-negative")
    return round(energy_kwh * rate_rm_per_kwh, 4)


def build_alerts(reading: TelemetryReading, connection_state: str) -> list[DashboardAlert]:
    alerts: list[DashboardAlert] = []
    if connection_state == "offline":
        alerts.append(
            DashboardAlert(
                code="DEVICE_OFFLINE",
                level="error",
                title="Monitor offline",
                message=(
                    "No recent telemetry has arrived. Check the low-voltage controller "
                    "and Wi-Fi."
                ),
            )
        )
    elif connection_state == "stale":
        alerts.append(
            DashboardAlert(
                code="DEVICE_STALE",
                level="warning",
                title="Telemetry delayed",
                message="The latest reading is older than the expected upload interval.",
            )
        )

    if reading.quality_status != "valid":
        alerts.append(
            DashboardAlert(
                code="DATA_QUALITY",
                level="error",
                title="Measurement unavailable",
                message=(
                    f"The latest record is marked {reading.quality_status.value}; "
                    "it is not an appliance-off measurement."
                ),
            )
        )

    if reading.anomaly_status == "anomaly":
        synthetic = reading.firmware_version.startswith("sim-")
        alerts.append(
            DashboardAlert(
                code="SYNTHETIC_ANOMALY" if synthetic else "ANOMALY",
                level="warning",
                title="Synthetic unusual cycle" if synthetic else "Unusual cycle",
                message=(
                    "This simulated reading demonstrates the alert path and does not confirm "
                    "a physical fault."
                    if synthetic
                    else (
                        "The cycle differs from its baseline and does not confirm a physical "
                        "fault."
                    )
                ),
            )
        )

    if reading.battery_voltage_v < 3.5:
        alerts.append(
            DashboardAlert(
                code="LOW_BATTERY",
                level="warning",
                title="Controller battery low",
                message="Plan a monitored shutdown and recharge with the monitor switched off.",
            )
        )
    return alerts
