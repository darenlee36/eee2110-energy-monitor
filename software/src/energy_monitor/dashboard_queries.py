from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from energy_monitor.metrics import derive_connection_state
from energy_monitor.models import (
    ConnectionState,
    CycleAssessment,
    CycleStatus,
    CycleSummary,
    QualityStatus,
    TariffEstimate,
    TelemetryReading,
    VolumeClass,
)
from energy_monitor.storage import SQLiteTelemetryStore
from energy_monitor.tariffs import TariffCatalog, estimate_cycle_charge, load_tariff_catalog

CATALOG_PATH = (
    Path(__file__).parents[2] / "config" / "tariffs" / "tnb-domestic-general-rp4.json"
)


class CycleListItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cycle_id: UUID
    cycle_number: int | None = None
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int
    energy_kwh: float
    energy_label: str = "Measured cycle energy"
    charge_text: str
    tariff_estimate: TariffEstimate | None = None
    energy_eligible: bool = True
    effective_volume: VolumeClass
    cycle_label: str | None = None
    record_source: Literal["measured", "manual"]
    source_label: str = "Unverified"
    assessment: CycleAssessment
    status: CycleStatus


class LiveView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal[
        "empty", "idle", "monitoring", "heating", "finalizing", "cycle_complete", "unconfirmed"
    ]
    connection_state: ConnectionState
    updated_at: datetime | None
    received_at: datetime | None = None
    sample_age_seconds: int | None = None
    latest_power_w: float | None
    latest_voltage_v: float | None
    latest_current_a: float | None
    elapsed_seconds: int | None
    energy_so_far_kwh: float | None
    charge_so_far: TariffEstimate | None
    quality_note: str
    recent_readings: list[TelemetryReading]
    cycle_readings: list[TelemetryReading]
    active_cycle: CycleSummary | None
    last_cycle: CycleListItem | None
    start_threshold_w: float = 1000.0
    appliance_rule_name: str = "Electric kettle"


class CycleDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: CycleSummary
    readings: list[TelemetryReading]
    tariff_estimate: TariffEstimate
    effective_volume: VolumeClass
    manual_volume: VolumeClass | None
    cycle_label: str | None
    record_source: Literal["measured", "manual"]
    manual_notes: str | None
    comparison_text: str
    chart_summary: str

    @property
    def energy_label(self) -> str:
        return cycle_energy_label(self.summary, self.record_source)


def cycle_energy_label(summary: CycleSummary, record_source: str) -> str:
    if record_source == "manual":
        return "Entered cycle energy"
    prefix = {"simulated": "Simulated", "sensor": "Measured"}.get(
        summary.data_origin, "Recorded"
    )
    return f"{prefix} cycle energy"


def display_volume(volume: VolumeClass) -> str:
    return {
        VolumeClass.HALF_LITRE: "0.5 L",
        VolumeClass.ONE_LITRE: "1.0 L",
        VolumeClass.ONE_AND_HALF_LITRES: "1.5 L",
        VolumeClass.UNKNOWN: "Unknown",
    }[volume]


def comparison_text(
    selected_seconds: int,
    peer_seconds: Sequence[int],
    label: VolumeClass | str | None,
) -> str:
    if label is None or label is VolumeClass.UNKNOWN or len(peer_seconds) < 3:
        return "Not enough similar labelled cycles for comparison"
    display_label = display_volume(label) if isinstance(label, VolumeClass) else label
    difference = selected_seconds - round(median(peer_seconds))
    if difference == 0:
        return f"Matches the median of your valid {display_label} cycles"
    direction = "longer" if difference > 0 else "shorter"
    return (
        f"{abs(difference)} s {direction} than the median of your valid "
        f"{display_label} cycles"
    )


def _reading_from_row(row: dict[str, object]) -> TelemetryReading:
    return TelemetryReading.model_validate(
        {name: row[name] for name in TelemetryReading.model_fields}
    )


def _summary_from_row(row: dict[str, object]) -> CycleSummary:
    return CycleSummary.model_validate(
        {name: row[name] for name in CycleSummary.model_fields if name in row}
    )


def _monthly_usage_from_env() -> Decimal | None:
    raw = os.environ.get("ENERGY_MONITOR_MONTHLY_KWH", "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return value if value >= 0 else None


def _charge_text(estimate: TariffEstimate) -> str:
    if estimate.amount_rm is not None:
        return f"RM {estimate.amount_rm:.4f}"
    if estimate.amount_range_rm is not None:
        low, high = estimate.amount_range_rm
        return f"RM {low:.3f}–{high:.3f}"
    return "Unavailable"


def _cycle_item(
    row: dict[str, object],
    monthly_household_kwh: Decimal | None,
    catalog: TariffCatalog | None = None,
    *,
    cycle_number: int | None = None,
) -> CycleListItem:
    summary = _summary_from_row(row)
    estimate = estimate_cycle_charge(
        Decimal(str(summary.meter_energy_kwh)),
        summary.started_at,
        monthly_household_kwh,
        catalog or load_tariff_catalog(CATALOG_PATH),
    )
    return CycleListItem(
        cycle_id=summary.cycle_id,
        cycle_number=cycle_number,
        started_at=summary.started_at,
        ended_at=summary.ended_at,
        duration_seconds=summary.duration_seconds,
        energy_kwh=summary.meter_energy_kwh,
        energy_label=cycle_energy_label(summary, str(row.get("record_source", "measured"))),
        charge_text=_charge_text(estimate),
        tariff_estimate=estimate,
        energy_eligible=not (
            summary.invalid_sample_count or summary.missing_sample_count or summary.gap_count
        ),
        effective_volume=VolumeClass(str(row["effective_volume"])),
        cycle_label=(
            None if row.get("custom_label") is None else str(row["custom_label"])
        ),
        record_source=str(row.get("record_source", "measured")),
        source_label=(
            "Manual" if row.get("record_source") == "manual" else {
                "sensor": "Sensor mode", "simulated": "Simulated", "mixed": "Mixed",
                "unknown": "Unverified",
            }[summary.data_origin]
        ),
        assessment=summary.assessment,
        status=summary.status,
    )


def matches_source(origin: str, record_source: str, source_filter: str) -> bool:
    if source_filter == "all":
        return True
    if record_source == "manual":
        return source_filter == "manual"
    if source_filter == "unverified":
        return origin in {"unknown", "mixed"}
    return origin == source_filter


def _has_measured_load(readings: list[TelemetryReading]) -> bool:
    # Display only: the observed idle baseline was 0 W and steady charging was 9.9–18.4 W.
    # Require two votes in three consecutive readings; tolerate a single low sample.
    votes = 0
    newer = None
    for reading in reversed(readings[-3:]):
        if reading.quality_status is not QualityStatus.VALID:
            break
        if newer is not None and (
            newer.sample_sequence != reading.sample_sequence + 1
            or not 0 < (newer.timestamp - reading.timestamp).total_seconds() <= 8
        ):
            break
        votes += reading.active_power_w >= 5.0
        newer = reading
    return votes >= 2


def get_live_view(
    store: SQLiteTelemetryStore, now: datetime, *, source_filter: str = "all",
) -> LiveView:
    rows = store.fetch_recent_readings(limit=500)
    rows = [row for row in rows if matches_source(
        _reading_from_row(row).data_origin, "measured", source_filter)]
    if not rows:
        return LiveView(
            state="empty",
            connection_state=ConnectionState.OFFLINE,
            updated_at=None,
            latest_power_w=None,
            latest_voltage_v=None,
            latest_current_a=None,
            elapsed_seconds=None,
            energy_so_far_kwh=None,
            charge_so_far=None,
            quality_note="No telemetry has been received",
            recent_readings=[],
            cycle_readings=[],
            active_cycle=None,
            last_cycle=None,
        )

    readings = [_reading_from_row(row) for row in rows]
    latest = readings[-1]
    # Live follows the newest stream; never attach another monitor/profile/origin.
    readings = [r for r in readings if (r.device_id, r.profile_id, r.data_origin) ==
                (latest.device_id, latest.profile_id, latest.data_origin)]
    window_start = latest.timestamp - timedelta(minutes=5)
    recent_readings = [
        reading for reading in readings if reading.timestamp >= window_start
    ]
    connection = ConnectionState(derive_connection_state(latest.timestamp, now))
    cycle_rows = [
        row
        for row in store.fetch_cycles(limit=10_000)
        if row.get("record_source", "measured") == "measured"
        and (row["device_id"], row["profile_id"], row.get("data_origin", "unknown")) ==
            (latest.device_id, latest.profile_id, latest.data_origin)
    ]
    active_row = next(
        (row for row in cycle_rows if row["status"] == CycleStatus.ACTIVE.value),
        None,
    )
    active = None if active_row is None else _summary_from_row(active_row)
    rule_name, rule = (
        store.cycle_rule(active.detection_version) if active is not None
        else store.active_appliance_rule(latest.device_id, latest.profile_id, latest.timestamp)
    )
    completed_row = next(
        (row for row in cycle_rows if row["status"] == CycleStatus.COMPLETED.value),
        None,
    )
    monthly_usage = _monthly_usage_from_env()
    last_cycle = (
        None if completed_row is None else _cycle_item(completed_row, monthly_usage)
    )
    chart_cycle_row = active_row or completed_row
    cycle_readings = (
        []
        if chart_cycle_row is None
        else [
            _reading_from_row(item)
            for item in store.fetch_cycle_readings(str(chart_cycle_row["cycle_id"]))
        ]
    )

    if connection is not ConnectionState.ONLINE:
        state = "unconfirmed"
    elif active is not None:
        valid_tail = next(
            (r for r in reversed(readings)
             if r.timestamp >= active.started_at and r.quality_status is QualityStatus.VALID),
            None,
        )
        stop_power = rule.stop_power_w
        state = "finalizing" if valid_tail and valid_tail.active_power_w < stop_power else "heating"
    elif (
        completed_row is not None
        and completed_row.get("completion_seen_at") is not None
        and 0
        <= (now - datetime.fromisoformat(str(completed_row["completion_seen_at"]))).total_seconds()
        <= 30
    ):
        state = "cycle_complete"
    elif _has_measured_load(recent_readings):
        state = "monitoring"
    else:
        state = "idle"

    charge_so_far = None
    elapsed_seconds = None
    energy_so_far = None
    quality_note = "Latest reading is valid"
    if active is not None:
        elapsed_seconds = active.duration_seconds
        energy_so_far = active.meter_energy_kwh
        charge_so_far = estimate_cycle_charge(
            Decimal(str(active.meter_energy_kwh)),
            active.started_at,
            monthly_usage,
            load_tariff_catalog(CATALOG_PATH),
        )
        quality_note = active.quality_note
    if connection is not ConnectionState.ONLINE:
        quality_note = (f"Telemetry is {connection.value}; values are last recorded, "
                        "not a confirmed current load state. " + quality_note)

    received = datetime.fromisoformat(str(rows[-1]["received_at"]))
    return LiveView(
        state=state,
        connection_state=connection,
        updated_at=latest.timestamp,
        received_at=received if received.tzinfo is not None else None,
        sample_age_seconds=max(0, int((now - latest.timestamp).total_seconds())),
        latest_power_w=latest.active_power_w,
        latest_voltage_v=latest.voltage_v,
        latest_current_a=latest.current_a,
        elapsed_seconds=elapsed_seconds,
        energy_so_far_kwh=energy_so_far,
        charge_so_far=charge_so_far,
        quality_note=quality_note,
        recent_readings=recent_readings,
        cycle_readings=cycle_readings,
        active_cycle=active,
        last_cycle=last_cycle,
        start_threshold_w=rule.start_power_w,
        appliance_rule_name=rule_name,
    )


def cycle_start_matches(
    started_at: str | datetime, *, date_filter: str = "All dates",
    date_start: date | None = None, date_end: date | None = None,
    time_start: time | None = None, time_end: time | None = None,
    now: datetime | None = None,
) -> bool:
    """MYT calendar dates inclusive; optional same-day time window [start, end)."""
    zone = ZoneInfo("Asia/Kuala_Lumpur")
    stamp = datetime.fromisoformat(started_at) if isinstance(started_at, str) else started_at
    local = stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp
    local = local.astimezone(zone)
    clock = now or datetime.now(UTC)
    today = (clock.replace(tzinfo=UTC) if clock.tzinfo is None else clock).astimezone(zone).date()
    if date_filter == "All dates":
        start = end = None
    elif date_filter == "Today":
        start = end = today
    elif date_filter == "Yesterday":
        start = end = today - timedelta(days=1)
    elif date_filter == "Last 7 days":
        start, end = today - timedelta(days=6), today
    elif date_filter == "Custom date range":
        start, end = date_start, date_end
        if start is None or end is None or start > end:
            raise ValueError("Start date must be on or before the end date.")
    else:
        raise ValueError("Unsupported date filter")
    if (time_start is None) != (time_end is None):
        raise ValueError("Both time boundaries are required.")
    if time_start is not None and time_end <= time_start:
        raise ValueError("End time must be after start time; overnight windows are not supported.")
    return ((start is None or start <= local.date() <= end)
            and (time_start is None or time_start <= local.time() < time_end))


def complete_sensor_cycle_numbers(rows: list[dict]) -> dict[str, int]:
    """Number genuine Complete runs using the full history in newest-first order."""
    numbered = (
        str(row["cycle_id"]) for row in reversed(rows)
        if row["status"] == CycleStatus.COMPLETED.value
        and row.get("data_origin") == "sensor"
        and row.get("record_source", "measured") == "measured"
        and str(row.get("custom_label") or "").strip().casefold() == "complete"
    )
    return {cycle_id: number for number, cycle_id in enumerate(numbered, start=1)}


def list_cycle_items(
    store: SQLiteTelemetryStore,
    status_filter: str = "all",
    label_filter: str = "all",
    limit: int | None = None,
    *, source_filter: str = "all", date_filter: str = "All dates",
    date_start: date | None = None, date_end: date | None = None,
    time_start: time | None = None, time_end: time | None = None,
    now: datetime | None = None,
) -> list[CycleListItem]:
    now = now or datetime.now(UTC)
    options = dict(date_filter=date_filter, date_start=date_start, date_end=date_end,
                   time_start=time_start, time_end=time_end, now=now)
    cycle_start_matches(now, **options)  # Validate even when history is empty.
    rows = store.fetch_cycles(limit=None)
    cycle_numbers = complete_sensor_cycle_numbers(rows)
    rows = [r for r in rows if matches_source(
        str(r.get("data_origin", "unknown")), str(r.get("record_source", "measured")),
        source_filter)]
    if status_filter == "normal":
        rows = [row for row in rows if row["assessment"] == CycleAssessment.NORMAL.value]
    elif status_filter == "unusual":
        rows = [row for row in rows if row["assessment"] == CycleAssessment.UNUSUAL.value]
    elif status_filter == "incomplete":
        rows = [row for row in rows if row["status"] == CycleStatus.INCOMPLETE.value]
    elif status_filter != "all":
        raise ValueError("unsupported status filter")
    if label_filter == "__unlabelled__":
        rows = [row for row in rows if row.get("custom_label") is None]
    elif label_filter != "all":
        rows = [row for row in rows if row.get("custom_label") == label_filter]
    rows = [row for row in rows if cycle_start_matches(row["started_at"], **options)]
    monthly_usage = _monthly_usage_from_env()
    catalog = load_tariff_catalog(CATALOG_PATH)
    return [
        _cycle_item(
            row, monthly_usage, catalog, cycle_number=cycle_numbers.get(str(row["cycle_id"]))
        )
        for row in rows[:limit]
    ]


def get_cycle_detail(
    store: SQLiteTelemetryStore,
    cycle_id: UUID | str,
    tariff_context: Decimal | None,
) -> CycleDetail:
    row = store.fetch_cycle(cycle_id)
    if row is None:
        raise ValueError("cycle does not exist")
    summary = _summary_from_row(row)
    readings = [
        _reading_from_row(item) for item in store.fetch_cycle_readings(cycle_id)
    ]
    effective_volume = VolumeClass(str(row["effective_volume"]))
    manual_volume = (
        None
        if row.get("manual_volume") is None
        else VolumeClass(str(row["manual_volume"]))
    )
    cycle_label = (
        None if row.get("custom_label") is None else str(row["custom_label"])
    )
    peers = [
        int(peer["duration_seconds"])
        for peer in store.fetch_cycles(status=CycleStatus.COMPLETED.value, limit=10_000)
        if peer["cycle_id"] != str(summary.cycle_id)
        and peer["assessment"] == CycleAssessment.NORMAL.value
        and peer.get("record_source", "measured") == "measured"
        and peer.get("data_origin") == "sensor"
        and summary.data_origin == "sensor"
        and cycle_label is not None
        and peer.get("custom_label") == cycle_label
    ]
    estimate = estimate_cycle_charge(
        Decimal(str(summary.meter_energy_kwh)),
        summary.started_at,
        tariff_context,
        load_tariff_catalog(CATALOG_PATH),
    )
    record_source = str(row.get("record_source", "measured"))
    chart_summary = (
        "Manual record; no raw telemetry available"
        if record_source == "manual"
        else (
            f"{len(readings)} readings from cycle start to cycle end; "
            f"peak {summary.peak_power_w:.0f} W"
        )
    )
    return CycleDetail(
        summary=summary,
        readings=readings,
        tariff_estimate=estimate,
        effective_volume=effective_volume,
        manual_volume=manual_volume,
        cycle_label=cycle_label,
        record_source=record_source,
        manual_notes=(
            None if row.get("manual_notes") is None else str(row["manual_notes"])
        ),
        comparison_text=comparison_text(
            summary.duration_seconds,
            peers,
            cycle_label,
        ),
        chart_summary=chart_summary,
    )
