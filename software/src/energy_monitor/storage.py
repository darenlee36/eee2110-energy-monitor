from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from energy_monitor.cycles import detect_cycles
from energy_monitor.models import (
    BatchResult,
    CycleDetectionSettings,
    CycleSummary,
    TelemetryBatch,
    TelemetryReading,
    VolumeClass,
)


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

                CREATE TABLE IF NOT EXISTS cycle_detection_versions (
                    version TEXT PRIMARY KEY,
                    settings_json TEXT NOT NULL,
                    calibration_status TEXT NOT NULL
                        CHECK (calibration_status IN ('simulation_default', 'hardware_validated')),
                    effective_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS appliance_cycles (
                    cycle_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    start_sequence INTEGER NOT NULL,
                    end_sequence INTEGER,
                    status TEXT NOT NULL
                        CHECK (status IN ('active', 'completed', 'incomplete')),
                    assessment TEXT NOT NULL CHECK (
                        assessment IN (
                            'not_evaluated', 'normal', 'unusual', 'insufficient_data'
                        )
                    ),
                    summary_json TEXT NOT NULL,
                    detection_version TEXT NOT NULL
                        REFERENCES cycle_detection_versions(version),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(device_id, profile_id, start_sequence)
                );

                CREATE INDEX IF NOT EXISTS appliance_cycles_started_at_idx
                    ON appliance_cycles(started_at DESC);

                CREATE TABLE IF NOT EXISTS cycle_volume_labels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id TEXT NOT NULL REFERENCES appliance_cycles(cycle_id),
                    volume_class TEXT NOT NULL
                        CHECK (volume_class IN ('0.5_l', '1.0_l', '1.5_l', 'unknown')),
                    source TEXT NOT NULL CHECK (source = 'dashboard'),
                    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE UNIQUE INDEX IF NOT EXISTS one_active_volume_label_per_cycle_idx
                    ON cycle_volume_labels(cycle_id) WHERE is_active = 1;
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

    @staticmethod
    def _reading_from_row(row: sqlite3.Row) -> TelemetryReading:
        fields = TelemetryReading.model_fields
        return TelemetryReading.model_validate({name: row[name] for name in fields})

    def process_cycles(self, settings: CycleDetectionSettings) -> list[CycleSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM telemetry ORDER BY timestamp ASC, id ASC"
            ).fetchall()
            grouped: dict[tuple[str, str], list[TelemetryReading]] = defaultdict(list)
            for row in rows:
                item = self._reading_from_row(row)
                grouped[(item.device_id, item.profile_id)].append(item)

            cycles = [
                cycle
                for readings in grouped.values()
                for cycle in detect_cycles(readings, settings)
            ]
            connection.execute(
                """
                INSERT INTO cycle_detection_versions (
                    version, settings_json, calibration_status, effective_at
                ) VALUES (?, ?, 'simulation_default', ?)
                ON CONFLICT(version) DO NOTHING
                """,
                (
                    settings.version,
                    settings.model_dump_json(),
                    datetime.now(UTC).isoformat(),
                ),
            )
            for cycle in cycles:
                values = cycle.model_dump(mode="json")
                connection.execute(
                    """
                    INSERT INTO appliance_cycles (
                        cycle_id, device_id, profile_id, started_at, ended_at,
                        start_sequence, end_sequence, status, assessment,
                        summary_json, detection_version
                    ) VALUES (
                        :cycle_id, :device_id, :profile_id, :started_at, :ended_at,
                        :start_sequence, :end_sequence, :status, :assessment,
                        :summary_json, :detection_version
                    )
                    ON CONFLICT(cycle_id) DO UPDATE SET
                        ended_at = excluded.ended_at,
                        end_sequence = excluded.end_sequence,
                        status = excluded.status,
                        assessment = excluded.assessment,
                        summary_json = excluded.summary_json,
                        detection_version = excluded.detection_version,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    {
                        **values,
                        "summary_json": cycle.model_dump_json(),
                    },
                )
        return cycles

    @staticmethod
    def _cycle_dict(row: sqlite3.Row) -> dict[str, object]:
        result: dict[str, object] = json.loads(row["summary_json"])
        manual_volume = row["manual_volume"] if "manual_volume" in row.keys() else None
        result["manual_volume"] = manual_volume
        result["effective_volume"] = manual_volume or result.get("effective_volume", "unknown")
        if "created_at" in row.keys():
            result["created_at"] = row["created_at"]
            result["updated_at"] = row["updated_at"]
        return result

    def fetch_cycles(
        self,
        status: str | None = None,
        volume: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        if limit < 1 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")
        parameters: list[object] = []
        where = ""
        if status is not None:
            where = "WHERE cycles.status = ?"
            parameters.append(status)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT cycles.*,
                       labels.volume_class AS manual_volume
                FROM appliance_cycles AS cycles
                LEFT JOIN cycle_volume_labels AS labels
                  ON labels.cycle_id = cycles.cycle_id AND labels.is_active = 1
                {where}
                ORDER BY cycles.started_at DESC
                """,  # noqa: S608 -- where is a fixed internal fragment
                parameters,
            ).fetchall()
        cycles = [self._cycle_dict(row) for row in rows]
        if volume is not None:
            cycles = [cycle for cycle in cycles if cycle["effective_volume"] == volume]
        return cycles[:limit]

    def fetch_cycle(self, cycle_id: UUID | str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT cycles.*, labels.volume_class AS manual_volume
                FROM appliance_cycles AS cycles
                LEFT JOIN cycle_volume_labels AS labels
                  ON labels.cycle_id = cycles.cycle_id AND labels.is_active = 1
                WHERE cycles.cycle_id = ?
                """,
                (str(cycle_id),),
            ).fetchone()
        return None if row is None else self._cycle_dict(row)

    def fetch_cycle_readings(self, cycle_id: UUID | str) -> list[dict[str, object]]:
        with self._connect() as connection:
            cycle = connection.execute(
                """
                SELECT device_id, profile_id, start_sequence, end_sequence
                FROM appliance_cycles WHERE cycle_id = ?
                """,
                (str(cycle_id),),
            ).fetchone()
            if cycle is None:
                return []
            if cycle["end_sequence"] is None:
                sequence_clause = "sample_sequence >= ?"
                parameters = (
                    cycle["device_id"],
                    cycle["profile_id"],
                    cycle["start_sequence"],
                )
            else:
                sequence_clause = "sample_sequence BETWEEN ? AND ?"
                parameters = (
                    cycle["device_id"],
                    cycle["profile_id"],
                    cycle["start_sequence"],
                    cycle["end_sequence"],
                )
            rows = connection.execute(
                f"""
                SELECT * FROM telemetry
                WHERE device_id = ? AND profile_id = ? AND {sequence_clause}
                ORDER BY timestamp ASC, id ASC
                """,  # noqa: S608 -- sequence_clause is a fixed internal fragment
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def save_volume_label(self, cycle_id: UUID | str, volume: VolumeClass) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM appliance_cycles WHERE cycle_id = ?",
                (str(cycle_id),),
            ).fetchone()
            if exists is None:
                raise ValueError("cycle does not exist")
            connection.execute(
                "UPDATE cycle_volume_labels SET is_active = 0 WHERE cycle_id = ?",
                (str(cycle_id),),
            )
            connection.execute(
                """
                INSERT INTO cycle_volume_labels (
                    cycle_id, volume_class, source, is_active
                ) VALUES (?, ?, 'dashboard', 1)
                """,
                (str(cycle_id), volume.value),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fetch_effective_volume(self, cycle_id: UUID | str) -> VolumeClass:
        cycle = self.fetch_cycle(cycle_id)
        if cycle is None:
            raise ValueError("cycle does not exist")
        return VolumeClass(str(cycle["effective_volume"]))

    def fetch_volume_label_history(
        self,
        cycle_id: UUID | str,
    ) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, cycle_id, volume_class, source, is_active, created_at
                FROM cycle_volume_labels
                WHERE cycle_id = ?
                ORDER BY id ASC
                """,
                (str(cycle_id),),
            ).fetchall()
        return [dict(row) for row in rows]

