from datetime import UTC, datetime, timedelta

from test_models import valid_reading

from energy_monitor.metrics import (
    build_alerts,
    calculate_cost_rm,
    calculate_energy_used_kwh,
    derive_connection_state,
)
from energy_monitor.models import TelemetryReading


def test_connection_state_uses_explicit_freshness_boundaries() -> None:
    now = datetime(2026, 9, 12, 8, 0, tzinfo=UTC)

    assert derive_connection_state(now - timedelta(seconds=45), now) == "online"
    assert derive_connection_state(now - timedelta(seconds=46), now) == "stale"
    assert derive_connection_state(now - timedelta(seconds=120), now) == "stale"
    assert derive_connection_state(now - timedelta(seconds=121), now) == "offline"


def test_energy_used_sums_positive_deltas_across_controller_reset() -> None:
    values = [1.0, 1.1, 0.01, 0.03]

    assert calculate_energy_used_kwh(values) == 0.12


def test_cost_uses_configured_rate_and_never_claims_a_bill() -> None:
    assert calculate_cost_rm(0.125, 0.60) == 0.075


def test_alerts_include_synthetic_anomaly_and_low_battery_context() -> None:
    reading = TelemetryReading.model_validate(
        valid_reading(
            anomaly_status="anomaly",
            battery_voltage_v=3.44,
            firmware_version="sim-0.1.0",
        )
    )

    alerts = build_alerts(reading, "online")

    assert [(alert.code, alert.level) for alert in alerts] == [
        ("SYNTHETIC_ANOMALY", "warning"),
        ("LOW_BATTERY", "warning"),
    ]
    assert "does not confirm a physical fault" in alerts[0].message


def test_alerts_prioritize_offline_and_data_quality_failures() -> None:
    reading = TelemetryReading.model_validate(valid_reading(quality_status="read_error"))

    alerts = build_alerts(reading, "offline")

    assert [alert.code for alert in alerts] == ["DEVICE_OFFLINE", "DATA_QUALITY"]
