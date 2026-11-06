from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from energy_monitor.models import CycleSummary, ManualCycleRecord
from energy_monitor.reporting import ReportDraft, build_report, validate_report
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
            payload = parsed.model_dump(mode="json")
            cycle = parsed.evidence_snapshot["cycles"][0]
            if (parsed.kind == "cycle" and parsed.engine == "template"
                    and cycle["record_source"] == "measured" and cycle["data_origin"] == "sensor"):
                confirmed = self._rpc("save_completed_sensor_report", {"report": payload})
                if confirmed != str(parsed.report_id):
                    raise ValueError("Cloud cycle and report receipt does not match")
            else:
                response = httpx.post(
                    f"{self.config.url}/rest/v1/reports",
                    headers={
                        "apikey": self.config.service_role_key,
                        "Prefer": "resolution=ignore-duplicates,return=minimal",
                    },
                    params={"on_conflict": "report_id"},
                    json=payload, timeout=15,
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

    def fetch_first_assessment(self, cycle_id: str) -> dict | None:
        value = self._rpc("read_intelligence_first_score", {
            "p_cycle_id": str(UUID(cycle_id)),
        })
        if value is None:
            return None
        return self._decode_first_assessment(value)

    def append_first_assessment(self, row: dict) -> dict:
        result = self._rpc("append_intelligence_first_score", {
            "p_evidence": json.dumps(row, allow_nan=False),
        })
        return self._decode_first_assessment(result)

    def fetch_rolling_power(self, cycle_id: str, frozen_ids: list[str]) -> dict:
        result = self._rpc("read_intelligence_rolling_power", {
            "p_cycle_id": str(UUID(cycle_id)), "p_frozen_ids": frozen_ids,
        })
        if not isinstance(result, dict):
            raise ValueError("Prior cycle average is unavailable.")
        return result

    @staticmethod
    def _decode_first_assessment(value: object) -> dict:
        if isinstance(value, str):
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("Invalid cloud first-score receipt.")

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

    def fetch_cycle_annotation(self, cycle_id: UUID | str) -> dict[str, object]:
        annotation = self._rpc(
            "dashboard_cycle_annotation", {"p_cycle_id": str(UUID(str(cycle_id)))}
        )
        if not isinstance(annotation, dict):
            raise ValueError("Cycle annotation could not be loaded.")
        return annotation

    def save_cycle_annotation(
        self, cycle_id: UUID | str, *, label: str, note: str, expected_id: object = None,
    ) -> None:
        cycle_id = str(UUID(str(cycle_id)))
        cycle = self.fetch_cycle(cycle_id)
        if cycle is None or cycle["status"] != "completed":
            raise ValueError("Only completed cycles can be annotated.")
        label, note = self._validated_annotation(label, note)
        payload = {
            "p_cycle_id": cycle_id,
            "p_device_id": cycle["device_id"],
            "p_profile_id": cycle["profile_id"],
            "p_origin": cycle["data_origin"],
            "p_label": label,
            "p_note": note,
            "p_expected_id": str(UUID(str(expected_id))) if expected_id is not None else None,
        }
        result = self._rpc("save_public_cycle_annotation", payload)
        status = result.get("status") if isinstance(result, dict) else None
        if (status == "unavailable" and cycle["data_origin"] == "sensor"
                and cycle["record_source"] == "measured"):
            try:
                response = httpx.get(
                    f"{self.config.url}/rest/v1/appliance_cycles",
                    headers={"apikey": self.config.service_role_key},
                    params={"select": "cycle_id", "cycle_id": "eq." + cycle_id}, timeout=15,
                )
                response.raise_for_status()
                cloud_cycles = response.json()
                if not isinstance(cloud_cycles, list):
                    raise ValueError("Invalid cloud cycle response")
                if not cloud_cycles:
                    # A freshly detected cycle may exist only in SQLite. The existing
                    # server RPC validates its sensor boundaries and saves it atomically.
                    report = build_report(self, profile_id=cycle["profile_id"],
                                          device_id=cycle["device_id"], cycle_id=cycle_id)
                    confirmed = self._rpc(
                        "save_completed_sensor_report", {"report": report.model_dump(mode="json")}
                    )
                    if confirmed != str(report.report_id):
                        raise ValueError("Cloud cycle receipt does not match")
                    result = self._rpc("save_public_cycle_annotation", payload)
                    status = result.get("status") if isinstance(result, dict) else None
            except (httpx.HTTPError, ValueError) as exc:
                raise ValueError(
                    "Cycle cloud save could not be confirmed. Refresh and retry the annotation."
                ) from exc
        if status != "saved":
            raise ValueError({
                "conflict": "This annotation changed. Refresh before editing.",
                "unavailable": "Cycle scope mismatch or archived label. Check your selection.",
                "limited": "Too many recent edits to this cycle. Try again in a minute.",
            }.get(status, "The annotation save could not be confirmed."))
        self.refresh_edits()

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
