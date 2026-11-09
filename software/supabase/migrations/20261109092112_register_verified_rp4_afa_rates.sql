-- Register the verified RP4 AFA reference data for new cycle estimates.
-- Existing cycle measurements and saved report evidence are independent snapshots.
update public.afa_periods
set exempt_max_monthly_kwh = 800, last_checked_date = date '2026-11-09'
where version = 'tnb-afa-2026-09' and period_month = date '2026-09-01'
  and rate_sen_per_kwh = 3.67 and exempt_max_monthly_kwh in (600, 800);

insert into public.afa_periods (
    version, tariff_version, period_month, rate_sen_per_kwh,
    exempt_max_monthly_kwh, source_url, last_checked_date
)
select version, 'tnb-domestic-general-rp4-2025-07', period_month, rate, 800,
       'https://www.st.gov.my/sites/default/files/2026-11/Notis-Kadar-AFA-bagi-Bulan-November-2026.pdf',
       date '2026-11-09'
from (values
    ('tnb-afa-2026-10', date '2026-10-01', 3.61),
    ('tnb-afa-2026-11', date '2026-11-01', 3.60)
) as verified(version, period_month, rate)
on conflict do nothing;

do $$
begin
    if 3 <> (
        select count(*) from public.afa_periods a
        join (values
            ('tnb-afa-2026-09', date '2026-09-01', 3.67),
            ('tnb-afa-2026-10', date '2026-10-01', 3.61),
            ('tnb-afa-2026-11', date '2026-11-01', 3.60)
        ) as expected(version, period_month, rate)
        on a.version = expected.version and a.period_month = expected.period_month
            and a.rate_sen_per_kwh = expected.rate
        where a.tariff_version = 'tnb-domestic-general-rp4-2025-07'
            and a.exempt_max_monthly_kwh = 800
    ) then
        raise exception 'Existing AFA rows differ from verified RP4 reference data';
    end if;
end $$;
