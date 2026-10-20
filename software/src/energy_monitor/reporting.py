"""Deterministic evidence and template reports; no AI or background side effects."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from energy_monitor.dashboard_queries import _monthly_usage_from_env, get_cycle_detail
from energy_monitor.models import CycleSummary
from energy_monitor.storage import SQLiteTelemetryStore

REPORTING_VERSION = "cycle-report-template-v1"


class ReportDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: UUID
    profile_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    device_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    kind: Literal["cycle", "batch"]
    cycle_count: int = Field(strict=True)
    period_start: AwareDatetime
    period_end: AwareDatetime
    generated_at: AwareDatetime
    reporting_version: str
    engine: Literal["template"] = "template"
    status: Literal["generated"] = "generated"
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_snapshot: dict[str, object]
    report_text: str


def build_report(
    store: SQLiteTelemetryStore, *, profile_id: str, device_id: str,
    cycle_id: str | None = None, cycle_count: int = 1,
) -> ReportDraft:
    if type(cycle_count) is not int or cycle_count not in (1, 5, 10, 20):
        raise ValueError("cycle count must be 1, 5, 10 or 20")
    for identifier in (profile_id, device_id):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identifier):
            raise ValueError("invalid device/profile identifier")
    if cycle_count == 1:
        if cycle_id is None:
            raise ValueError("one-cycle reports require a completed cycle ID")
        row = store.fetch_cycle(str(UUID(str(cycle_id))))
        if row is None:
            raise ValueError("completed cycle not found")
        if row["profile_id"] != profile_id or row["device_id"] != device_id:
            raise ValueError("cycle does not match device/profile")
        if row["status"] != "completed":
            raise ValueError("only completed cycles can be reported")
        rows = [row]
    else:
        if cycle_id is not None:
            raise ValueError("batch count cannot be combined with a cycle ID")
        rows = store.fetch_report_cycles(profile_id, device_id, limit=cycle_count)
        if len(rows) != cycle_count:
            raise ValueError(f"{len(rows)} of {cycle_count} completed cycles available")
        rows.reverse()

    cycles = []
    totals: dict[str, dict[str, object]] = {}
    source_counts: Counter[str] = Counter()
    excluded = 0
    limitations = {
        "Sensor origin is not calibration evidence.",
        "Isolation Forest is not trained; anomaly evaluation is not evaluated.",
        "Unusual electrical behaviour does not confirm a physical fault.",
        "Costs are qualified appliance estimates, not an official household bill.",
    }
    monthly_usage = _monthly_usage_from_env()
    for row in rows:
        summary = CycleSummary.model_validate({
            k: v for k, v in row.items() if k in CycleSummary.model_fields
        })
        detail = get_cycle_detail(store, summary.cycle_id, monthly_usage)
        source = "manual" if detail.record_source == "manual" else summary.data_origin
        source_counts[source] += 1
        eligible = not (
            summary.invalid_sample_count or summary.missing_sample_count or summary.gap_count
        )
        estimate = detail.tariff_estimate
        cycles.append({
            **summary.model_dump(mode="json"),
            "record_source": detail.record_source,
            "eligible_for_totals": eligible,
            "tariff_estimate": estimate.model_dump(mode="json"),
        })
        limitations.update(
            f"Unresolved tariff component: {c}." for c in estimate.unresolved_components
        )
        limitations.update(f"Excluded tariff component: {c}." for c in estimate.excluded_components)
        if not eligible:
            excluded += 1
            continue
        group = totals.setdefault(source, {
            "cycle_count": 0, "energy_kwh": Decimal(0),
            "charge_low_rm": Decimal(0), "charge_high_rm": Decimal(0),
            "unavailable_charge_count": 0,
        })
        group["cycle_count"] += 1
        group["energy_kwh"] += Decimal(str(summary.meter_energy_kwh))
        bounds = estimate.amount_range_rm
        if bounds is None and estimate.amount_rm is not None:
            bounds = (estimate.amount_rm, estimate.amount_rm)
        if bounds is None:
            group["unavailable_charge_count"] += 1
        else:
            group["charge_low_rm"] += bounds[0]
            group["charge_high_rm"] += bounds[1]

    for group in totals.values():
        group["energy_kwh"] = str(group["energy_kwh"])
        missing_charge = group["unavailable_charge_count"]
        for key in ("charge_low_rm", "charge_high_rm"):
            group[key] = None if missing_charge else str(group[key])
    period_start = min(datetime.fromisoformat(c["started_at"]) for c in cycles)
    period_end = max(datetime.fromisoformat(c["ended_at"]) for c in cycles)
    kind = "cycle" if cycle_count == 1 else "batch"
    evidence = {
        "reporting_version": REPORTING_VERSION, "profile_id": profile_id,
        "device_id": device_id, "kind": kind, "cycle_count": cycle_count,
        "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
        "source_counts": dict(source_counts), "cycles": cycles,
        "excluded_quality_count": excluded, "totals_by_source": totals,
        "model": {"status": "not_trained", "anomaly_evaluation": "not_evaluated"},
        "limitations": sorted(limitations),
    }
    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False)
    evidence_hash = hashlib.sha256(canonical.encode()).hexdigest()
    report_id = uuid5(NAMESPACE_URL, f"template:{REPORTING_VERSION}:{evidence_hash}")
    return ReportDraft(
        report_id=report_id, profile_id=profile_id, device_id=device_id, kind=kind,
        cycle_count=cycle_count, period_start=period_start, period_end=period_end,
        generated_at=datetime.now(UTC), reporting_version=REPORTING_VERSION,
        evidence_hash=evidence_hash, evidence_snapshot=evidence,
        report_text=render_report(evidence),
    )


def render_report(evidence: dict[str, object]) -> str:
    """Render fixed text; user labels/notes are not interpreted as Markdown."""
    cycle_count = evidence["cycle_count"]
    profile_id, device_id = evidence["profile_id"], evidence["device_id"]
    source_counts = evidence["source_counts"]
    totals = evidence["totals_by_source"]
    excluded = evidence["excluded_quality_count"]
    limitations = evidence["limitations"]
    lines = [
        "# Energy Monitor report", "", f"Template report · {cycle_count} completed cycle(s)",
        f"Profile: {profile_id} · Device: {device_id}", "",
        "## Evidence and qualified totals", "",
    ]
    for source, count in sorted(source_counts.items()):
        group = totals.get(source)
        lines.append(f"- Source {source}: {count} selected cycle(s).")
        if group:
            lines.append(f"  Included {source} energy: {group['energy_kwh']} kWh.")
            if group["charge_low_rm"] is not None:
                lines.append(
                    f"  Estimated gross charge: RM {group['charge_low_rm']}–"
                    f"{group['charge_high_rm']}; tariff qualifications below apply."
                )
            else:
                lines.append("  Charge total unavailable; some cycle charges cannot be estimated.")
    lines.extend(["", f"Cycles excluded from totals for quality errors: {excluded}.", ""])
    lines.extend(f"- {item}" for item in sorted(limitations))
    return "\n".join(lines)


def validate_report(report: ReportDraft | dict[str, object]) -> ReportDraft:
    """Revalidate frozen copies and nested evidence before persistence or display."""
    value = report.model_dump(mode="json") if isinstance(report, ReportDraft) else report
    parsed = ReportDraft.model_validate(value)
    evidence = parsed.evidence_snapshot
    try:
        canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        expected_id = uuid5(
            NAMESPACE_URL, f"{parsed.engine}:{parsed.reporting_version}:{digest}"
        )
        for name in ("profile_id", "device_id", "kind", "cycle_count", "reporting_version"):
            if evidence[name] != getattr(parsed, name):
                raise ValueError("report metadata differs from evidence")
        for name in ("period_start", "period_end"):
            if datetime.fromisoformat(evidence[name]) != getattr(parsed, name):
                raise ValueError("report coverage differs from evidence")
        counts = (1,) if parsed.kind == "cycle" else (5, 10, 20)
        if parsed.cycle_count not in counts or len(evidence["cycles"]) != parsed.cycle_count:
            raise ValueError("report cycle count differs from evidence")
        cycle_ids = set()
        for cycle in evidence["cycles"]:
            if (
                cycle["profile_id"] != parsed.profile_id
                or cycle["device_id"] != parsed.device_id
                or cycle["status"] != "completed"
                or cycle["cycle_id"] in cycle_ids
            ):
                raise ValueError("report cycle scope differs from evidence")
            cycle_ids.add(cycle["cycle_id"])
        if parsed.period_end < parsed.period_start:
            raise ValueError("report evidence coverage is reversed")
        if (
            digest != parsed.evidence_hash or expected_id != parsed.report_id
            or parsed.report_text != render_report(evidence)
        ):
            raise ValueError("report text or identity differs from evidence")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("invalid report evidence") from exc
    return parsed


def generate_missing_reports(
    store: SQLiteTelemetryStore, *, profile_id: str, device_id: str
) -> int:
    generated = 0
    for cycle in reversed(store.fetch_report_cycles(profile_id, device_id)):
        report = build_report(
            store, profile_id=profile_id, device_id=device_id, cycle_id=str(cycle["cycle_id"])
        )
        if store.fetch_report(str(report.report_id)) is None:
            store.save_report(report)
            generated += 1
    return generated


def main(argv: list[str] | None = None) -> int:
    """One-shot trusted laptop worker; never schedules work or calls an AI API."""
    import argparse

    from dotenv import load_dotenv

    from energy_monitor.cloud_edits import SupabaseCycleStore
    from energy_monitor.supabase_sync import (
        SupabaseConfig,
        SupabaseSyncError,
        sync_supabase_telemetry,
    )

    parser = argparse.ArgumentParser(description="Generate evidence-backed template reports")
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--source", choices=("local", "supabase", "auto"))
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--generate-missing", action="store_true")
    action.add_argument("--cycle-id", type=UUID)
    action.add_argument("--cycle-count", type=int, choices=(5, 10, 20))
    args = parser.parse_args(argv)
    root = Path(__file__).parents[2]
    load_dotenv(root / ".env")
    mode = args.source or os.environ.get("ENERGY_MONITOR_SOURCE", "auto").strip().lower()
    if mode not in ("auto", "local", "demo", "supabase"):
        parser.error("invalid source mode")
    config = SupabaseConfig.from_environment()
    cloud = mode == "supabase" or (mode == "auto" and config is not None)
    if cloud and config is None:
        print("Cloud credentials missing; no reports generated.", file=sys.stderr)
        return 1
    default = root / "data" / ("supabase-cache.db" if cloud else "telemetry.db")
    database = args.database or Path(os.environ.get("ENERGY_MONITOR_DB", default))
    try:
        store = (
            SupabaseCycleStore(database, config, can_edit=True)
            if cloud else SQLiteTelemetryStore(database)
        )
        if cloud:
            while True:
                before = store.cloud_sync_cursor(config.url)
                sync_supabase_telemetry(store, config)
                if store.cloud_sync_cursor(config.url) == before:
                    break
            store.refresh_edits()
        if args.generate_missing:
            count = generate_missing_reports(
                store, profile_id=args.profile_id, device_id=args.device_id
            )
            print(f"{count} new individual template report(s) confirmed.")
        else:
            report = build_report(
                store, profile_id=args.profile_id, device_id=args.device_id,
                cycle_id=str(args.cycle_id) if args.cycle_id else None,
                cycle_count=args.cycle_count or 1,
            )
            print(f"Template report confirmed: {store.save_report(report)}")
        return 0
    except (SupabaseSyncError, OSError, ValueError):
        print("Report generation failed; no new save is confirmed. Check data and cloud access; "
              "retry is duplicate-safe.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # Use the canonical module's model class at the storage validation boundary.
    from energy_monitor.reporting import main as run_main

    raise SystemExit(run_main())
