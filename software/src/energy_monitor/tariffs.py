from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from energy_monitor.models import TariffEstimate, TariffEstimateStatus

MONEY_QUANTUM = Decimal("0.0001")
LOW_USE_MAX_KWH = Decimal("1500")
MALAYSIA_TZ = ZoneInfo("Asia/Kuala_Lumpur")
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
    rate_sen_per_kwh: Decimal | None
    exempt_max_monthly_kwh: Decimal = Field(ge=0)
    source_url: str
    last_checked_date: date
    exemption_source_url: str | None = None


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


def _base_rates(tariff: TariffVersion, *, high_usage: bool) -> tuple[Decimal, Decimal, Decimal]:
    return (
        tariff.energy_rate_above_1500_sen_per_kwh if high_usage
        else tariff.energy_rate_up_to_1500_sen_per_kwh,
        tariff.capacity_rate_sen_per_kwh,
        tariff.network_rate_sen_per_kwh,
    )


def tariff_calculation_rates(
    estimate: TariffEstimate, catalog: TariffCatalog,
) -> list[tuple[str, dict[str, Decimal]]]:
    """Show only rate components that reproduce the stored estimate."""
    names = ("Energy", "Capacity", "Network")
    if estimate.amount_rm is not None:
        rates = (estimate.energy_rate_sen_per_kwh, estimate.capacity_rate_sen_per_kwh,
                 estimate.network_rate_sen_per_kwh)
        if any(rate is None for rate in rates):
            return []
        components = dict(zip(names, rates, strict=True))
        if "afa" in estimate.included_components:
            if estimate.afa_rate_sen_per_kwh is None:
                return []
            components["AFA"] = estimate.afa_rate_sen_per_kwh
        total_rate = sum(components.values(), Decimal(0))
        if (total_rate != estimate.gross_variable_rate_sen_per_kwh
                or _amount(estimate.energy_kwh, total_rate) != estimate.amount_rm):
            return []
        return [("Applicable household tier", components)]
    if estimate.amount_range_rm is None or estimate.included_components != (
        "energy", "capacity", "network",
    ):
        return []
    tariff = next((item for item in catalog.tariff_versions
                   if item.version == estimate.tariff_version), None)
    if tariff is None or (
        tariff.capacity_rate_sen_per_kwh, tariff.network_rate_sen_per_kwh,
    ) != (estimate.capacity_rate_sen_per_kwh, estimate.network_rate_sen_per_kwh):
        return []
    cases = [
        ("Up to 1,500 kWh/month", _base_rates(tariff, high_usage=False)),
        ("Above 1,500 kWh/month", _base_rates(tariff, high_usage=True)),
    ]
    bounds = tuple(_amount(estimate.energy_kwh, sum(rates)) for _, rates in cases)
    if bounds != estimate.amount_range_rm:
        return []
    return [(label, dict(zip(names, rates, strict=True))) for label, rates in cases]


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

    local_date = occurred_at.astimezone(MALAYSIA_TZ).date()
    month = local_date.strftime("%Y-%m")
    tariff = next(
        (
            item
            for item in catalog.tariff_versions
            if item.effective_from <= local_date <= item.effective_to
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

    base_sources = (tariff.source_url,)
    included = ("energy", "capacity", "network")

    if monthly_household_kwh is None:
        base_low = sum(_base_rates(tariff, high_usage=False))
        base_high = sum(_base_rates(tariff, high_usage=True))
        return TariffEstimate(
            status=TariffEstimateStatus.PARTIAL,
            provider=catalog.provider,
            scheme=catalog.scheme,
            tariff_version=tariff.version,
            tariff_effective_from=tariff.effective_from.isoformat(),
            tariff_effective_to=tariff.effective_to.isoformat(),
            afa_version=None,
            afa_period=month,
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

    rates = _base_rates(
        tariff, high_usage=monthly_household_kwh > LOW_USE_MAX_KWH,
    )
    afa = next((item for item in catalog.afa_periods if item.month == month), None)
    exempt_max = (
        afa.exempt_max_monthly_kwh if afa is not None else tariff.retail_waiver_max_monthly_kwh
    )
    published_rate = afa.rate_sen_per_kwh if afa is not None else None
    afa_rate = Decimal(0) if monthly_household_kwh <= exempt_max else published_rate
    if afa_rate is None:
        return _unavailable(
            energy_kwh,
            occurred_at,
            monthly_household_kwh,
            catalog,
            ("published_afa",),
            tariff,
        )

    gross_rate = sum(rates) + afa_rate
    sources = base_sources if afa is None else (*base_sources, afa.source_url)
    if afa is not None and afa.exemption_source_url is not None:
        sources += (afa.exemption_source_url,)
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
        afa_version=None if afa is None or published_rate is None else afa.version,
        afa_period=month,
        occurred_at=occurred_at,
        energy_kwh=energy_kwh,
        monthly_household_kwh=monthly_household_kwh,
        energy_rate_sen_per_kwh=rates[0],
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
