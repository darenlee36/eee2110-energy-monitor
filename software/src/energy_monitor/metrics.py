from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from energy_monitor.models import TelemetryReading


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
