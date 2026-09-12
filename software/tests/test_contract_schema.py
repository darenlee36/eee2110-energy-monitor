import json
from pathlib import Path

import pytest
from jsonschema import FormatChecker, ValidationError, validate
from test_api import make_payload

SCHEMA_PATH = Path(__file__).parents[1] / "shared" / "telemetry.schema.json"
MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "supabase"
    / "migrations"
    / "202609120001_initial_schema.sql"
)


def load_schema() -> dict[str, object]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_json_schema_accepts_the_reference_batch() -> None:
    payload = make_payload("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

    validate(payload, load_schema(), format_checker=FormatChecker())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("device_id", "contains spaces"),
        ("timestamp", "not-a-time"),
        ("power_factor", 1.2),
        ("connection_state", "connected"),
        ("anomaly_status", "faulty"),
    ],
)
def test_json_schema_rejects_invalid_contract_values(field: str, value: object) -> None:
    payload = make_payload("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    payload["readings"][0][field] = value

    with pytest.raises(ValidationError):
        validate(payload, load_schema(), format_checker=FormatChecker())


def test_json_schema_rejects_more_than_six_readings() -> None:
    payload = make_payload("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
    payload["readings"] = payload["readings"] * 4

    with pytest.raises(ValidationError):
        validate(payload, load_schema(), format_checker=FormatChecker())


def test_supabase_migration_contains_cycle_first_tables_and_rls() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8").lower()
    for table in (
        "cycle_detection_versions",
        "appliance_cycles",
        "cycle_volume_labels",
        "tariff_versions",
        "afa_periods",
        "cycle_cost_estimates",
    ):
        assert f"create table if not exists public.{table}" in sql
        assert f"alter table public.{table} enable row level security" in sql
    assert "unique (device_id, profile_id, start_sequence)" in sql
    assert "one_active_volume_label_per_cycle" in sql
