from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from energy_monitor.metrics import derive_connection_state
from energy_monitor.models import (
    ConnectionState,
    CycleAssessment,
    CycleStatus,
    CycleSummary,
    TariffEstimate,
    TelemetryReading,
    VolumeClass,
)
from energy_monitor.storage import SQLiteTelemetryStore
from energy_monitor.tariffs import estimate_cycle_charge, load_tariff_catalog

CATALOG_PATH = (
    Path(__file__).parents[2] / "config" / "tariffs" / "tnb-domestic-general-rp4.json"
)


class CycleListItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cycle_id: UUID
    started_at: datetime
    duration_seconds: int
    energy_kwh: float
    energy_label: str = "Measured cycle energy"
    charge_text: str
    effective_volume: VolumeClass
    assessment: CycleAssessment
    status: CycleStatus


class LiveView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["empty", "idle", "heating", "cycle_complete"]
    connection_state: ConnectionState
    updated_at: datetime | None
    latest_power_w: float | None
    latest_voltage_v: float | None
    latest_current_a: float | None
    elapsed_seconds: int | None
    energy_so_far_kwh: float | None
    charge_so_far: TariffEstimate | None
    quality_note: str
    active_cycle: CycleSummary | None
    last_cycle: CycleListItem | None


class CycleDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: CycleSummary
    readings: list[TelemetryReading]
    tariff_estimate: TariffEstimate
    effective_volume: VolumeClass
    manual_volume: VolumeClass | None
    comparison_text: str
    chart_summary: str


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
    volume: VolumeClass,
) -> str:
    if volume is VolumeClass.UNKNOWN or len(peer_seconds) < 3:
        return "Not enough similar labelled cycles for comparison"
    difference = selected_seconds - round(median(peer_seconds))
    if difference == 0:
        return f"Matches the median of your valid {display_volume(volume)} cycles"
    direction = "longer" if difference > 0 else "shorter"
    return (
        f"{abs(difference)} s {direction} than the median of your valid "
        f"{display_volume(volume)} cycles"
    )


def _reading_from_row(row: dict[str, object]) -> TelemetryReading:
    return TelemetryReading.model_validate(
        {name: row[name] for name in TelemetryReading.model_fields}
    )


def _summary_from_row(row: dict[str, object]) -> CycleSummary:
    return CycleSummary.model_validate(
        {name: row[name] for name in CycleSummary.model_fields}
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
        return f"RM {low:.4f}–{high:.4f}"
    return "Unavailable"


def _cycle_item(
    row: dict[str, object],
    monthly_household_kwh: Decimal | None,
) -> CycleListItem:
    summary = _summary_from_row(row)
    estimate = estimate_cycle_charge(
        Decimal(str(summary.meter_energy_kwh)),
        summary.started_at,
        monthly_household_kwh,
        load_tariff_catalog(CATALOG_PATH),
    )
    return CycleListItem(
        cycle_id=summary.cycle_id,
        started_at=summary.started_at,
        duration_seconds=summary.duration_seconds,
        energy_kwh=summary.meter_energy_kwh,
        charge_text=_charge_text(estimate),
        effective_volume=VolumeClass(str(row["effective_volume"])),
        assessment=summary.assessment,
        status=summary.status,
    )


def get_live_view(store: SQLiteTelemetryStore, now: datetime) -> LiveView:
    rows = store.fetch_recent_readings(limit=500)
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
            active_cycle=None,
            last_cycle=None,
        )

    latest = _reading_from_row(rows[-1])
    connection = ConnectionState(derive_connection_state(latest.timestamp, now))
    cycle_rows = store.fetch_cycles(limit=100)
    active_row = next(
        (row for row in cycle_rows if row["status"] == CycleStatus.ACTIVE.value),
        None,
    )
    active = None if active_row is None else _summary_from_row(active_row)
    completed_row = next(
        (row for row in cycle_rows if row["status"] != CycleStatus.ACTIVE.value),
        None,
    )
    monthly_usage = _monthly_usage_from_env()
    last_cycle = (
        None if completed_row is None else _cycle_item(completed_row, monthly_usage)
    )

    if active is not None:
        state: Literal["empty", "idle", "heating", "cycle_complete"] = "heating"
    elif (
        completed_row is not None
        and completed_row.get("ended_at") is not None
        and 0
        <= (now - datetime.fromisoformat(str(completed_row["ended_at"]))).total_seconds()
        <= 30
    ):
        state = "cycle_complete"
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
    elif connection is not ConnectionState.ONLINE:
        quality_note = f"Telemetry is {connection.value}"

    return LiveView(
        state=state,
        connection_state=connection,
        updated_at=latest.timestamp,
        latest_power_w=latest.active_power_w,
        latest_voltage_v=latest.voltage_v,
        latest_current_a=latest.current_a,
        elapsed_seconds=elapsed_seconds,
        energy_so_far_kwh=energy_so_far,
        charge_so_far=charge_so_far,
        quality_note=quality_note,
        active_cycle=active,
        last_cycle=last_cycle,
    )


def list_cycle_items(
    store: SQLiteTelemetryStore,
    status_filter: str = "all",
    volume_filter: str = "all",
    limit: int = 100,
) -> list[CycleListItem]:
    rows = store.fetch_cycles(limit=10_000)
    if status_filter == "normal":
        rows = [row for row in rows if row["assessment"] == CycleAssessment.NORMAL.value]
    elif status_filter == "unusual":
        rows = [row for row in rows if row["assessment"] == CycleAssessment.UNUSUAL.value]
    elif status_filter == "incomplete":
        rows = [row for row in rows if row["status"] == CycleStatus.INCOMPLETE.value]
    elif status_filter != "all":
        raise ValueError("unsupported status filter")
    if volume_filter != "all":
        rows = [row for row in rows if row["effective_volume"] == volume_filter]
    monthly_usage = _monthly_usage_from_env()
    return [_cycle_item(row, monthly_usage) for row in rows[:limit]]


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
    peers = [
        int(peer["duration_seconds"])
        for peer in store.fetch_cycles(status=CycleStatus.COMPLETED.value, limit=10_000)
        if peer["cycle_id"] != str(summary.cycle_id)
        and peer["assessment"] == CycleAssessment.NORMAL.value
        and peer["effective_volume"] == effective_volume.value
    ]
    estimate = estimate_cycle_charge(
        Decimal(str(summary.meter_energy_kwh)),
        summary.started_at,
        tariff_context,
        load_tariff_catalog(CATALOG_PATH),
    )
    chart_summary = (
        f"{len(readings)} readings from cycle start to cycle end; "
        f"peak {summary.peak_power_w:.0f} W"
    )
    return CycleDetail(
        summary=summary,
        readings=readings,
        tariff_estimate=estimate,
        effective_volume=effective_volume,
        manual_volume=manual_volume,
        comparison_text=comparison_text(
            summary.duration_seconds,
            peers,
            effective_volume,
        ),
        chart_summary=chart_summary,
    )
