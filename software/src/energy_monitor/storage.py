from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from energy_monitor.models import BatchResult, TelemetryBatch


class IdempotencyConflict(ValueError):
    """Raised when a batch ID is reused with a different request body."""


class SQLiteTelemetryStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ingestion_batches (
                    batch_id TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    accepted INTEGER NOT NULL,
                    duplicates INTEGER NOT NULL,
                    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    sample_sequence INTEGER NOT NULL,
                    device_uptime_ms INTEGER NOT NULL,
                    voltage_v REAL NOT NULL,
                    current_a REAL NOT NULL,
                    active_power_w REAL NOT NULL,
                    cumulative_energy_kwh REAL NOT NULL,
                    frequency_hz REAL NOT NULL,
                    power_factor REAL NOT NULL,
                    appliance_state TEXT NOT NULL,
                    battery_voltage_v REAL NOT NULL,
                    connection_state TEXT NOT NULL,
                    anomaly_status TEXT NOT NULL,
                    quality_status TEXT NOT NULL,
                    firmware_version TEXT NOT NULL,
                    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(device_id, sample_sequence)
                );

                CREATE INDEX IF NOT EXISTS telemetry_timestamp_idx
                    ON telemetry(timestamp DESC);
                """
            )

    @staticmethod
    def _request_hash(batch: TelemetryBatch) -> str:
        canonical = json.dumps(
            batch.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def insert_batch(self, batch: TelemetryBatch) -> BatchResult:
        batch_id = str(batch.batch_id)
        request_hash = self._request_hash(batch)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                """
                SELECT request_hash, accepted, duplicates
                FROM ingestion_batches
                WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
            if previous is not None:
                if previous["request_hash"] != request_hash:
                    raise IdempotencyConflict(
                        "batch_id was already used for a different request body"
                    )
                connection.commit()
                return BatchResult(
                    batch_id=batch.batch_id,
                    accepted=previous["accepted"],
                    duplicates=previous["duplicates"],
                    replayed=True,
                )

            accepted = 0
            for reading in batch.readings:
                values = reading.model_dump(mode="json")
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO telemetry (
                        device_id, profile_id, timestamp, sample_sequence,
                        device_uptime_ms, voltage_v, current_a, active_power_w,
                        cumulative_energy_kwh, frequency_hz, power_factor,
                        appliance_state, battery_voltage_v, connection_state,
                        anomaly_status, quality_status, firmware_version
                    ) VALUES (
                        :device_id, :profile_id, :timestamp, :sample_sequence,
                        :device_uptime_ms, :voltage_v, :current_a, :active_power_w,
                        :cumulative_energy_kwh, :frequency_hz, :power_factor,
                        :appliance_state, :battery_voltage_v, :connection_state,
                        :anomaly_status, :quality_status, :firmware_version
                    )
                    """,
                    values,
                )
                accepted += cursor.rowcount

            duplicates = len(batch.readings) - accepted
            connection.execute(
                """
                INSERT INTO ingestion_batches (batch_id, request_hash, accepted, duplicates)
                VALUES (?, ?, ?, ?)
                """,
                (batch_id, request_hash, accepted, duplicates),
            )
            connection.commit()
            return BatchResult(
                batch_id=batch.batch_id,
                accepted=accepted,
                duplicates=duplicates,
                replayed=False,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def count_readings(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS total FROM telemetry").fetchone()
            return int(row["total"])

    def fetch_recent_readings(self, limit: int = 500) -> list[dict[str, object]]:
        if limit < 1 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT *
                    FROM telemetry
                    ORDER BY timestamp DESC, id DESC
                    LIMIT ?
                )
                ORDER BY timestamp ASC, id ASC
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

