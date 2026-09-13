from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC
from uuid import NAMESPACE_URL, uuid5

import httpx

from energy_monitor.models import CycleDetectionSettings, TelemetryBatch, TelemetryReading
from energy_monitor.storage import SQLiteTelemetryStore

TELEMETRY_FIELDS = (
    "device_id,profile_id,recorded_at,sample_sequence,device_uptime_ms,voltage_v,"
    "current_a,active_power_w,cumulative_energy_kwh,frequency_hz,power_factor,"
    "appliance_state,battery_voltage_v,connection_state,anomaly_status,quality_status,"
    "firmware_version"
)


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    service_role_key: str

    @classmethod
    def from_environment(cls) -> SupabaseConfig | None:
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = (
            os.environ.get("SUPABASE_SECRET_KEY", "").strip()
            or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        )
        return cls(url, key) if url and key else None


class SupabaseSyncError(RuntimeError):
    pass


def sync_supabase_telemetry(
    store: SQLiteTelemetryStore,
    config: SupabaseConfig,
    *,
    limit: int = 500,
    client: httpx.Client | None = None,
) -> int:
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")

    latest = store.latest_telemetry_timestamp()
    params = {
        "select": TELEMETRY_FIELDS,
        "order": "recorded_at.asc" if latest else "recorded_at.desc",
        "limit": str(limit),
    }
    if latest is not None:
        params["recorded_at"] = f"gt.{latest.astimezone(UTC).isoformat()}"
    headers = {
        "apikey": config.service_role_key,
        "Authorization": f"Bearer {config.service_role_key}",
    }

    owns_client = client is None
    http = client or httpx.Client(timeout=10)
    try:
        response = http.get(
            f"{config.url}/rest/v1/telemetry",
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        rows = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SupabaseSyncError("Supabase telemetry sync failed") from exc
    finally:
        if owns_client:
            http.close()

    if not isinstance(rows, list):
        raise SupabaseSyncError("Supabase telemetry response must be a list")
    if latest is None:
        rows.reverse()

    readings: list[TelemetryReading] = []
    try:
        for row in rows:
            payload = dict(row)
            payload["timestamp"] = payload.pop("recorded_at")
            readings.append(TelemetryReading.model_validate(payload))
    except (TypeError, KeyError, ValueError) as exc:
        raise SupabaseSyncError("Supabase returned invalid telemetry") from exc

    accepted = 0
    for start in range(0, len(readings), 6):
        chunk = readings[start : start + 6]
        identity = "|".join(
            f"{item.device_id}:{item.sample_sequence}" for item in chunk
        )
        result = store.insert_batch(
            TelemetryBatch(batch_id=uuid5(NAMESPACE_URL, identity), readings=chunk)
        )
        accepted += result.accepted
    if accepted:
        store.process_cycles(CycleDetectionSettings.simulation_defaults())
    return accepted
