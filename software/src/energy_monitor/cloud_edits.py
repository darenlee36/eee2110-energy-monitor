from __future__ import annotations

from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from energy_monitor.models import CycleSummary, ManualCycleRecord
from energy_monitor.storage import SQLiteTelemetryStore
from energy_monitor.supabase_sync import SupabaseConfig


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
