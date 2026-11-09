from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from energy_monitor.models import TariffEstimateStatus
from energy_monitor.tariffs import estimate_cycle_charge, load_tariff_catalog

CATALOG_PATH = (
    Path(__file__).parents[1] / "config" / "tariffs" / "tnb-domestic-general-rp4.json"
)


def test_known_300_kwh_household_uses_base_components_and_afa_exemption() -> None:
    estimate = estimate_cycle_charge(
        energy_kwh=Decimal("0.087"),
        occurred_at=datetime(2026, 9, 12, tzinfo=UTC),
        monthly_household_kwh=Decimal("300"),
        catalog=load_tariff_catalog(CATALOG_PATH),
    )

    assert estimate.energy_rate_sen_per_kwh == Decimal("27.03")
    assert estimate.capacity_rate_sen_per_kwh == Decimal("4.55")
    assert estimate.network_rate_sen_per_kwh == Decimal("12.85")
    assert estimate.afa_rate_sen_per_kwh == Decimal("0")
    assert estimate.gross_variable_rate_sen_per_kwh == Decimal("44.43")
    assert estimate.amount_rm == Decimal("0.0387")
    assert estimate.status == TariffEstimateStatus.PARTIAL
    assert "energy_efficiency_incentive" in estimate.excluded_components


@pytest.mark.parametrize(("month", "rate"), [(9, "3.67"), (10, "3.61"), (11, "3.60")])
def test_temporary_afa_exemption_and_published_rates(month, rate) -> None:
    catalog = load_tariff_catalog(CATALOG_PATH)
    when = datetime(2026, month, 12, tzinfo=UTC)
    estimate = estimate_cycle_charge(
        Decimal("0.087"), when, Decimal("800"), catalog,
    )
    assert estimate.afa_rate_sen_per_kwh == Decimal("0")
    assert estimate.amount_rm == Decimal("0.0387")

    applicable = estimate_cycle_charge(Decimal("0.087"), when, Decimal("800.01"), catalog)
    assert applicable.afa_rate_sen_per_kwh == Decimal(rate)
    assert applicable.gross_variable_rate_sen_per_kwh == Decimal("44.43") + Decimal(rate)


def test_tariff_and_afa_use_malaysia_calendar_boundaries() -> None:
    catalog = load_tariff_catalog(CATALOG_PATH)
    november = estimate_cycle_charge(
        Decimal("0.1340"), datetime(2026, 10, 31, 16, tzinfo=UTC), Decimal("900"), catalog,
    )
    assert november.afa_period == "2026-11"
    assert november.amount_rm == Decimal("0.0644")
    december = estimate_cycle_charge(
        Decimal("0.1340"), datetime(2026, 12, 1, tzinfo=UTC), Decimal("800"), catalog,
    )
    assert december.afa_rate_sen_per_kwh == Decimal("0")
    assert december.amount_rm == Decimal("0.0595")
    assert december.afa_version is None
    unknown_rate = estimate_cycle_charge(
        Decimal("0.1340"), datetime(2026, 12, 1, tzinfo=UTC), Decimal("801"), catalog,
    )
    assert unknown_rate.status == TariffEstimateStatus.UNAVAILABLE
    assert unknown_rate.unresolved_components == ("published_afa",)


def test_unknown_household_usage_returns_qualified_rate_range() -> None:
    estimate = estimate_cycle_charge(
        Decimal("0.087"),
        datetime(2026, 9, 12, tzinfo=UTC),
        None,
        load_tariff_catalog(CATALOG_PATH),
    )

    assert estimate.status == TariffEstimateStatus.PARTIAL
    assert estimate.amount_rm is None
    assert estimate.amount_range_rm == (Decimal("0.0387"), Decimal("0.0474"))
    assert "afa_eligibility" in estimate.unresolved_components


def test_household_above_1500_kwh_uses_high_energy_rate() -> None:
    estimate = estimate_cycle_charge(
        Decimal("0.087"),
        datetime(2026, 9, 12, tzinfo=UTC),
        Decimal("1600"),
        load_tariff_catalog(CATALOG_PATH),
    )

    assert estimate.energy_rate_sen_per_kwh == Decimal("37.03")
    assert estimate.gross_variable_rate_sen_per_kwh == Decimal("58.10")


def test_date_outside_published_tariff_period_is_unavailable() -> None:
    estimate = estimate_cycle_charge(
        Decimal("0.087"),
        datetime(2028, 1, 1, tzinfo=UTC),
        Decimal("300"),
        load_tariff_catalog(CATALOG_PATH),
    )

    assert estimate.status is TariffEstimateStatus.UNAVAILABLE
    assert estimate.amount_rm is None
    assert "tariff_version" in estimate.unresolved_components
