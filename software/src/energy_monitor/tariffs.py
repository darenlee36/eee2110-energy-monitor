from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from energy_monitor.models import TariffEstimate, TariffEstimateStatus

MONEY_QUANTUM = Decimal("0.0001")
LOW_USE_MAX_KWH = Decimal("1500")
EXCLUDED_COMPONENTS = (
    "retail_charge",
    "energy_efficiency_incentive",
    "taxes",
    "renewable_energy_fund",
    "rebates",
    "complete_bill_rounding",
)


class TariffVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    effective_from: date
    effective_to: date
    energy_rate_up_to_1500_sen_per_kwh: Decimal = Field(ge=0)
    energy_rate_above_1500_sen_per_kwh: Decimal = Field(ge=0)
    capacity_rate_sen_per_kwh: Decimal = Field(ge=0)
    network_rate_sen_per_kwh: Decimal = Field(ge=0)
    retail_charge_rm_per_month: Decimal = Field(ge=0)
    retail_waiver_max_monthly_kwh: Decimal = Field(ge=0)
    source_url: str
    source_published_date: date
    last_checked_date: date


class AfaPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    rate_sen_per_kwh: Decimal
    exempt_max_monthly_kwh: Decimal = Field(ge=0)
    source_url: str
    last_checked_date: date


class TariffCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    scheme: str
    currency: str
    tariff_versions: tuple[TariffVersion, ...]
    afa_periods: tuple[AfaPeriod, ...]


def load_tariff_catalog(path: Path) -> TariffCatalog:
    return TariffCatalog.model_validate_json(path.read_text(encoding="utf-8"))


def _amount(energy_kwh: Decimal, rate_sen_per_kwh: Decimal) -> Decimal:
    return (energy_kwh * rate_sen_per_kwh / Decimal("100")).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _unavailable(
    energy_kwh: Decimal,
    occurred_at: datetime,
    monthly_household_kwh: Decimal | None,
    catalog: TariffCatalog,
    unresolved: tuple[str, ...],
    tariff: TariffVersion | None = None,
) -> TariffEstimate:
    return TariffEstimate(
        status=TariffEstimateStatus.UNAVAILABLE,
        provider=catalog.provider,
        scheme=catalog.scheme,
        tariff_version=None if tariff is None else tariff.version,
        tariff_effective_from=(
            None if tariff is None else tariff.effective_from.isoformat()
        ),
        tariff_effective_to=None if tariff is None else tariff.effective_to.isoformat(),
        afa_version=None,
        afa_period=None,
        occurred_at=occurred_at,
        energy_kwh=energy_kwh,
        monthly_household_kwh=monthly_household_kwh,
        energy_rate_sen_per_kwh=None,
        capacity_rate_sen_per_kwh=(
            None if tariff is None else tariff.capacity_rate_sen_per_kwh
        ),
        network_rate_sen_per_kwh=(
            None if tariff is None else tariff.network_rate_sen_per_kwh
        ),
        afa_rate_sen_per_kwh=None,
        gross_variable_rate_sen_per_kwh=None,
        amount_rm=None,
        amount_range_rm=None,
        included_components=(),
        excluded_components=EXCLUDED_COMPONENTS,
        unresolved_components=unresolved,
        source_urls=() if tariff is None else (tariff.source_url,),
        last_checked_date=(
            None if tariff is None else tariff.last_checked_date.isoformat()
        ),
    )


def estimate_cycle_charge(
    energy_kwh: Decimal,
    occurred_at: datetime,
    monthly_household_kwh: Decimal | None,
    catalog: TariffCatalog,
) -> TariffEstimate:
    if energy_kwh < 0:
        raise ValueError("energy_kwh must be non-negative")
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("occurred_at must include a timezone")

    tariff = next(
        (
            item
            for item in catalog.tariff_versions
            if item.effective_from <= occurred_at.date() <= item.effective_to
        ),
        None,
    )
    if tariff is None:
        return _unavailable(
            energy_kwh,
            occurred_at,
            monthly_household_kwh,
            catalog,
            ("tariff_version",),
        )

    base_low = (
        tariff.energy_rate_up_to_1500_sen_per_kwh
        + tariff.capacity_rate_sen_per_kwh
        + tariff.network_rate_sen_per_kwh
    )
    base_high = (
        tariff.energy_rate_above_1500_sen_per_kwh
        + tariff.capacity_rate_sen_per_kwh
        + tariff.network_rate_sen_per_kwh
    )
    base_sources = (tariff.source_url,)
    included = ("energy", "capacity", "network")

    if monthly_household_kwh is None:
        return TariffEstimate(
            status=TariffEstimateStatus.PARTIAL,
            provider=catalog.provider,
            scheme=catalog.scheme,
            tariff_version=tariff.version,
            tariff_effective_from=tariff.effective_from.isoformat(),
            tariff_effective_to=tariff.effective_to.isoformat(),
            afa_version=None,
            afa_period=occurred_at.strftime("%Y-%m"),
            occurred_at=occurred_at,
            energy_kwh=energy_kwh,
            monthly_household_kwh=None,
            energy_rate_sen_per_kwh=None,
            capacity_rate_sen_per_kwh=tariff.capacity_rate_sen_per_kwh,
            network_rate_sen_per_kwh=tariff.network_rate_sen_per_kwh,
            afa_rate_sen_per_kwh=None,
            gross_variable_rate_sen_per_kwh=None,
            amount_rm=None,
            amount_range_rm=(_amount(energy_kwh, base_low), _amount(energy_kwh, base_high)),
            included_components=included,
            excluded_components=EXCLUDED_COMPONENTS,
            unresolved_components=("household_usage_tier", "afa_eligibility"),
            source_urls=base_sources,
            last_checked_date=tariff.last_checked_date.isoformat(),
        )

    energy_rate = (
        tariff.energy_rate_up_to_1500_sen_per_kwh
        if monthly_household_kwh <= LOW_USE_MAX_KWH
        else tariff.energy_rate_above_1500_sen_per_kwh
    )
    base_rate = (
        energy_rate
        + tariff.capacity_rate_sen_per_kwh
        + tariff.network_rate_sen_per_kwh
    )
    afa = next(
        (item for item in catalog.afa_periods if item.month == occurred_at.strftime("%Y-%m")),
        None,
    )
    if afa is None and monthly_household_kwh > tariff.retail_waiver_max_monthly_kwh:
        return _unavailable(
            energy_kwh,
            occurred_at,
            monthly_household_kwh,
            catalog,
            ("published_afa",),
            tariff,
        )

    afa_rate = (
        Decimal("0")
        if afa is None or monthly_household_kwh <= afa.exempt_max_monthly_kwh
        else afa.rate_sen_per_kwh
    )
    gross_rate = base_rate + afa_rate
    sources = base_sources if afa is None else (*base_sources, afa.source_url)
    last_checked = max(
        tariff.last_checked_date,
        tariff.last_checked_date if afa is None else afa.last_checked_date,
    )
    return TariffEstimate(
        status=TariffEstimateStatus.PARTIAL,
        provider=catalog.provider,
        scheme=catalog.scheme,
        tariff_version=tariff.version,
        tariff_effective_from=tariff.effective_from.isoformat(),
        tariff_effective_to=tariff.effective_to.isoformat(),
        afa_version=None if afa is None else afa.version,
        afa_period=occurred_at.strftime("%Y-%m"),
        occurred_at=occurred_at,
        energy_kwh=energy_kwh,
        monthly_household_kwh=monthly_household_kwh,
        energy_rate_sen_per_kwh=energy_rate,
        capacity_rate_sen_per_kwh=tariff.capacity_rate_sen_per_kwh,
        network_rate_sen_per_kwh=tariff.network_rate_sen_per_kwh,
        afa_rate_sen_per_kwh=afa_rate,
        gross_variable_rate_sen_per_kwh=gross_rate,
        amount_rm=_amount(energy_kwh, gross_rate),
        amount_range_rm=None,
        included_components=(*included, "afa"),
        excluded_components=EXCLUDED_COMPONENTS,
        unresolved_components=(),
        source_urls=sources,
        last_checked_date=last_checked.isoformat(),
    )
