from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from energy_monitor.models import CycleDetectionSettings, TelemetryBatch, TelemetryReading
from energy_monitor.storage import SQLiteTelemetryStore

TELEMETRY_FIELDS = (
    "device_id,profile_id,recorded_at,sample_sequence,device_uptime_ms,voltage_v,"
    "current_a,active_power_w,cumulative_energy_kwh,frequency_hz,power_factor,"
    "appliance_state,battery_voltage_v,connection_state,anomaly_status,quality_status,"
    "firmware_version,id,received_at"
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

    cursor = store.cloud_sync_cursor(config.url)
    params = {
        "select": TELEMETRY_FIELDS,
        "order": "received_at.asc,id.asc",
        "limit": str(limit),
    }
    if cursor is not None:
        received_at, row_id = cursor
        params["or"] = (
            f"(received_at.gt.{received_at},"
            f"and(received_at.eq.{received_at},id.gt.{row_id}))"
        )
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
    readings: dict[str, list[TelemetryReading]] = defaultdict(list)
    next_cursor = cursor
    try:
        for row in rows:
            payload = dict(row)
            row_id = str(UUID(str(payload.pop("id"))))
            received_at = datetime.fromisoformat(payload.pop("received_at"))
            if received_at.tzinfo is None:
                raise ValueError("arrival time requires a timezone")
            row_cursor = (received_at.astimezone(UTC).isoformat(), row_id)
            if next_cursor is not None and row_cursor <= next_cursor:
                raise ValueError("cloud page is not in cursor order")
            payload["timestamp"] = payload.pop("recorded_at")
            reading = TelemetryReading.model_validate(payload)
            readings[reading.device_id].append(reading)
            next_cursor = row_cursor
    except (TypeError, KeyError, ValueError) as exc:
        raise SupabaseSyncError("Supabase returned invalid telemetry") from exc

    accepted = 0
    for device_readings in readings.values():
        for start in range(0, len(device_readings), 6):
            chunk = device_readings[start : start + 6]
            identity = "|".join(f"{item.device_id}:{item.sample_sequence}" for item in chunk)
            result = store.insert_batch(
                TelemetryBatch(batch_id=uuid5(NAMESPACE_URL, identity), readings=chunk)
            )
            accepted += 0 if result.replayed else result.accepted
    if rows:
        # Reprocess before advancing, including recovery after an interrupted import.
        store.process_cycles(CycleDetectionSettings.simulation_defaults())
        assert next_cursor is not None
        store.save_cloud_sync_cursor(config.url, *next_cursor)
    return accepted
