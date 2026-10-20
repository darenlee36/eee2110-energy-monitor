from __future__ import annotations

import re
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from energy_monitor.models import CycleSummary, ManualCycleRecord
from energy_monitor.reporting import ReportDraft, validate_report
from energy_monitor.storage import SQLiteTelemetryStore
from energy_monitor.supabase_sync import SupabaseConfig, SupabaseSyncError


class SupabaseCycleStore(SQLiteTelemetryStore):
    """Keep edits durable in Supabase; use the existing SQLite read interface."""

    def __init__(
        self, database_path: str | Path, config: SupabaseConfig, *, can_edit: bool = False
    ) -> None:
        super().__init__(database_path)
        self.config = config
        self.can_edit = can_edit

    def _rpc(self, name: str, payload: dict[str, object]) -> object:
        try:
            response = httpx.post(
                f"{self.config.url}/rest/v1/rpc/{name}",
                headers={"apikey": self.config.service_role_key},
                json=payload,
                timeout=15,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ValueError(
                "Cloud records unavailable. No edit was confirmed; try again."
            ) from exc

    def _read_reports(self, params: dict[str, str]) -> list[dict[str, object]]:
        try:
            response = httpx.get(
                f"{self.config.url}/rest/v1/reports",
                headers={"apikey": self.config.service_role_key},
                params={"select": "*", **params}, timeout=15,
            )
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError("invalid report list")
            reports = [validate_report(row) for row in rows]
            for report in reports:
                for field in ("report_id", "profile_id", "device_id"):
                    if field in params and "eq." + str(getattr(report, field)) != params[field]:
                        raise ValueError("report scope mismatch")
            for report in reports:
                SQLiteTelemetryStore.save_report(self, report)
            return [report.model_dump(mode="json") for report in reports]
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise SupabaseSyncError(
                "Cloud reports unavailable; cached records may be shown."
            ) from exc

    def save_report(self, report: ReportDraft) -> str:
        if not self.can_edit:
            raise ValueError("Owner login required to generate cloud reports")
        parsed = validate_report(report)
        try:
            response = httpx.post(
                f"{self.config.url}/rest/v1/reports",
                headers={
                    "apikey": self.config.service_role_key,
                    "Prefer": "resolution=ignore-duplicates,return=minimal",
                },
                params={"on_conflict": "report_id"},
                json=parsed.model_dump(mode="json"), timeout=15,
            )
            response.raise_for_status()
            if self.fetch_report(str(parsed.report_id)) is None:
                raise ValueError("report not returned")
        except (httpx.HTTPError, ValueError, SupabaseSyncError) as exc:
            raise SupabaseSyncError(
                "Cloud report save not confirmed. Retry safely; duplicates are prevented."
            ) from exc
        return str(parsed.report_id)

    def fetch_report(self, report_id: str) -> dict[str, object] | None:
        report_id = str(UUID(str(report_id)))
        rows = self._read_reports({"report_id": "eq." + report_id, "limit": "1"})
        if len(rows) > 1:
            raise SupabaseSyncError("Invalid cloud report response")
        return rows[0] if rows else None

    def fetch_reports(
        self, profile_id: str | None = None, device_id: str | None = None, limit: int = 100
    ) -> list[dict[str, object]]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("report history limit must be between 1 and 1000")
        if (profile_id is None) != (device_id is None):
            raise ValueError("report scope requires both device and profile")
        if profile_id is not None and any(
            not isinstance(v, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", v)
            for v in (profile_id, device_id)
        ):
            raise ValueError("invalid report device/profile identifier")
        params = {
            "order": "generated_at.desc,report_id.desc", "limit": str(limit),
        }
        if profile_id is not None:
            params.update(profile_id="eq." + profile_id, device_id="eq." + device_id)
        return self._read_reports(params)

    def refresh_edits(self) -> None:
        state = self._rpc("dashboard_edit_state", {})
        if not isinstance(state, dict):
            raise ValueError("Invalid cloud edit state")
        self.restore_cloud_edits(state)

    def _edit(self, operation: str, payload: dict[str, object]) -> object:
        if not self.can_edit:
            raise ValueError("Owner login required to edit cloud records")
        result = self._rpc("apply_dashboard_edit", {"operation": operation, "payload": payload})
        self.refresh_edits()
        return result

    def add_cycle_label(self, label: str) -> str:
        normalized = self._validated_cycle_label(label)
        return str(self._edit("add_label", {"label": normalized}))

    def archive_cycle_label(self, label: str) -> None:
        self._edit("archive_label", {"label": self._validated_cycle_label(label)})

    def save_cycle_label(self, cycle_id: UUID | str, label: str) -> None:
        cycle = self.fetch_cycle(cycle_id)
        if cycle is None:
            raise ValueError("cycle does not exist")
        summary = CycleSummary.model_validate(
            {key: value for key, value in cycle.items() if key in CycleSummary.model_fields}
        )
        self._edit("assign_label", {
            "label": self._validated_cycle_label(label),
            "summary": summary.model_dump(mode="json"),
            "source": cycle["record_source"],
            "notes": cycle.get("manual_notes"),
        })

    def add_manual_cycle(self, record: ManualCycleRecord) -> UUID:
        # A retry after a lost response must not create a second manual record.
        identity = self.config.url + ":manual:" + record.model_dump_json()
        summary = self.manual_cycle_summary(record, uuid5(NAMESPACE_URL, identity))
        self._edit("manual_cycle", {
            "label": self._validated_cycle_label(record.label),
            "summary": summary.model_dump(mode="json"),
            "source": "manual", "notes": record.notes,
        })
        return summary.cycle_id
