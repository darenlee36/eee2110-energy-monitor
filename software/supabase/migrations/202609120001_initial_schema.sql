create extension if not exists pgcrypto;

create table if not exists public.appliance_profiles (
    id text primary key,
    profile_name text not null,
    manufacturer text,
    model text,
    profile_status text not null default 'draft'
        check (profile_status in ('draft', 'baseline_learning', 'active', 'retired')),
    created_at timestamptz not null default now(),
    notes text
);

insert into public.appliance_profiles (id, profile_name, manufacturer, model, profile_status)
values ('tefal-kettle', 'Tefal Safe''Tea kettle', 'Tefal', 'KO260-series', 'draft')
on conflict (id) do nothing;

create table if not exists public.ingestion_batches (
    batch_id uuid primary key,
    request_hash text not null,
    state text not null default 'processing'
        check (state in ('processing', 'completed', 'failed')),
    accepted integer not null default 0 check (accepted >= 0),
    duplicates integer not null default 0 check (duplicates >= 0),
    received_at timestamptz not null default now(),
    completed_at timestamptz
);

create table if not exists public.telemetry (
    id uuid primary key default gen_random_uuid(),
    device_id text not null check (device_id ~ '^[a-zA-Z0-9_-]{1,64}$'),
    profile_id text not null references public.appliance_profiles(id),
    recorded_at timestamptz not null,
    sample_sequence bigint not null check (sample_sequence >= 0),
    device_uptime_ms bigint not null check (device_uptime_ms >= 0),
    voltage_v double precision not null check (voltage_v between 0 and 300),
    current_a double precision not null check (current_a between 0 and 100),
    active_power_w double precision not null check (active_power_w between 0 and 25000),
    cumulative_energy_kwh double precision not null check (cumulative_energy_kwh >= 0),
    frequency_hz double precision not null check (frequency_hz between 40 and 70),
    power_factor double precision not null check (power_factor between 0 and 1),
    appliance_state text not null check (appliance_state in ('off', 'heating', 'unknown')),
    battery_voltage_v double precision check (battery_voltage_v between 2.5 and 4.5),
    connection_state text not null check (connection_state in ('online', 'stale', 'offline')),
    anomaly_status text not null
        check (anomaly_status in ('not_evaluated', 'normal', 'anomaly')),
    quality_status text not null
        check (quality_status in ('valid', 'read_error', 'communication_gap', 'power_interruption')),
    firmware_version text not null check (char_length(firmware_version) between 1 and 32),
    received_at timestamptz not null default now(),
    unique (device_id, sample_sequence)
);

create index if not exists telemetry_profile_recorded_at_idx
    on public.telemetry (profile_id, recorded_at desc);

create table if not exists public.cycle_detection_versions (
    version text primary key,
    settings jsonb not null,
    calibration_status text not null
        check (calibration_status in ('simulation_default', 'hardware_validated')),
    effective_at timestamptz not null,
    created_at timestamptz not null default now()
);

insert into public.cycle_detection_versions (
    version, settings, calibration_status, effective_at
) values (
    'sim-cycle-v1',
    '{
      "start_power_w": 1000.0,
      "start_confirm_samples": 2,
      "stop_power_w": 100.0,
      "stop_confirm_samples": 3,
      "warning_gap_seconds": 15,
      "terminating_gap_seconds": 60,
      "minimum_duration_seconds": 30,
      "maximum_duration_seconds": 600,
      "energy_difference_tolerance": 0.20
    }'::jsonb,
    'simulation_default',
    '2026-09-12T00:00:00Z'
) on conflict (version) do nothing;

create table if not exists public.appliance_cycles (
    cycle_id uuid primary key,
    device_id text not null check (device_id ~ '^[a-zA-Z0-9_-]{1,64}$'),
    profile_id text not null references public.appliance_profiles(id),
    started_at timestamptz not null,
    ended_at timestamptz,
    start_sequence bigint not null check (start_sequence >= 0),
    end_sequence bigint check (end_sequence >= start_sequence),
    status text not null check (status in ('active', 'completed', 'incomplete')),
    assessment text not null check (
        assessment in ('not_evaluated', 'normal', 'unusual', 'insufficient_data')
    ),
    meter_energy_kwh numeric(12,6) not null check (meter_energy_kwh >= 0),
    integrated_energy_kwh numeric(12,6) not null check (integrated_energy_kwh >= 0),
    summary jsonb not null,
    detection_version text not null references public.cycle_detection_versions(version),
    record_source text not null default 'measured'
        check (record_source in ('measured', 'manual')),
    manual_notes text check (char_length(manual_notes) <= 500),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint appliance_cycles_device_start_unique
        unique (device_id, profile_id, start_sequence)
);

create index if not exists appliance_cycles_started_at_idx
    on public.appliance_cycles(started_at desc);

create table if not exists public.cycle_volume_labels (
    id uuid primary key default gen_random_uuid(),
    cycle_id uuid not null references public.appliance_cycles(cycle_id),
    volume_class text not null
        check (volume_class in ('0.5_l', '1.0_l', '1.5_l', 'unknown')),
    source text not null check (source = 'dashboard'),
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

create unique index if not exists one_active_volume_label_per_cycle
    on public.cycle_volume_labels(cycle_id)
    where is_active;

create table if not exists public.cycle_label_options (
    id uuid primary key default gen_random_uuid(),
    label text not null check (char_length(btrim(label)) between 1 and 40),
    label_key text generated always as (lower(btrim(label))) stored unique,
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

alter table public.cycle_label_options
    add column if not exists is_active boolean not null default true;

create table if not exists public.cycle_label_assignments (
    id uuid primary key default gen_random_uuid(),
    cycle_id uuid not null references public.appliance_cycles(cycle_id),
    label_option_id uuid not null references public.cycle_label_options(id),
    source text not null check (source = 'dashboard'),
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

create unique index if not exists one_active_custom_label_per_cycle
    on public.cycle_label_assignments(cycle_id)
    where is_active;

create table if not exists public.tariff_versions (
    version text primary key,
    provider text not null,
    scheme text not null,
    effective_from date not null,
    effective_to date not null check (effective_to >= effective_from),
    energy_rate_up_to_1500_sen_per_kwh numeric(8,4) not null,
    energy_rate_above_1500_sen_per_kwh numeric(8,4) not null,
    capacity_rate_sen_per_kwh numeric(8,4) not null,
    network_rate_sen_per_kwh numeric(8,4) not null,
    retail_charge_rm_per_month numeric(8,4) not null,
    retail_waiver_max_monthly_kwh numeric(12,3) not null,
    source_url text not null,
    source_published_date date not null,
    last_checked_date date not null
);

insert into public.tariff_versions (
    version, provider, scheme, effective_from, effective_to,
    energy_rate_up_to_1500_sen_per_kwh,
    energy_rate_above_1500_sen_per_kwh,
    capacity_rate_sen_per_kwh, network_rate_sen_per_kwh,
    retail_charge_rm_per_month, retail_waiver_max_monthly_kwh,
    source_url, source_published_date, last_checked_date
) values (
    'tnb-domestic-general-rp4-2025-07', 'TNB', 'Domestic General',
    '2025-07-01', '2027-12-31', 27.03, 37.03, 4.55, 12.85, 10.00, 600,
    'https://myenergystats.st.gov.my/documents/d/guest/tariff-tnb-pdf-1',
    '2025-06-20', '2026-09-12'
) on conflict (version) do nothing;

create table if not exists public.afa_periods (
    version text primary key,
    tariff_version text not null references public.tariff_versions(version),
    period_month date not null unique,
    rate_sen_per_kwh numeric(8,4) not null,
    exempt_max_monthly_kwh numeric(12,3) not null,
    source_url text not null,
    last_checked_date date not null
);

insert into public.afa_periods (
    version, tariff_version, period_month, rate_sen_per_kwh,
    exempt_max_monthly_kwh, source_url, last_checked_date
) values (
    'tnb-afa-2026-09', 'tnb-domestic-general-rp4-2025-07', '2026-09-01',
    3.67, 600,
    'https://www.singlebuyer.com.my/Generation-Info-Tariff/automatic-fuel-adjustment-(afa)',
    '2026-09-12'
) on conflict (version) do nothing;

create table if not exists public.cycle_cost_estimates (
    id uuid primary key default gen_random_uuid(),
    cycle_id uuid not null references public.appliance_cycles(cycle_id),
    tariff_version text references public.tariff_versions(version),
    afa_version text references public.afa_periods(version),
    status text not null check (status in ('complete', 'partial', 'unavailable')),
    energy_kwh numeric(12,6) not null check (energy_kwh >= 0),
    gross_variable_rate_sen_per_kwh numeric(8,4),
    amount_rm numeric(8,4),
    amount_range_low_rm numeric(8,4),
    amount_range_high_rm numeric(8,4),
    calculation jsonb not null,
    created_at timestamptz not null default now()
);

alter table public.appliance_profiles enable row level security;
alter table public.ingestion_batches enable row level security;
alter table public.telemetry enable row level security;
alter table public.cycle_detection_versions enable row level security;
alter table public.appliance_cycles enable row level security;
alter table public.cycle_volume_labels enable row level security;
alter table public.cycle_label_options enable row level security;
alter table public.cycle_label_assignments enable row level security;
alter table public.tariff_versions enable row level security;
alter table public.afa_periods enable row level security;
alter table public.cycle_cost_estimates enable row level security;

create policy "authenticated users may read appliance profiles"
    on public.appliance_profiles for select to authenticated using (true);

create policy "authenticated users may read telemetry"
    on public.telemetry for select to authenticated using (true);

create policy "authenticated users may read cycle detection versions"
    on public.cycle_detection_versions for select to authenticated using (true);

create policy "authenticated users may read appliance cycles"
    on public.appliance_cycles for select to authenticated using (true);

create policy "authenticated users may read cycle volume labels"
    on public.cycle_volume_labels for select to authenticated using (true);

create policy "authenticated users may read cycle label options"
    on public.cycle_label_options for select to authenticated using (true);

create policy "authenticated users may read cycle label assignments"
    on public.cycle_label_assignments for select to authenticated using (true);

create policy "authenticated users may read tariff versions"
    on public.tariff_versions for select to authenticated using (true);

create policy "authenticated users may read afa periods"
    on public.afa_periods for select to authenticated using (true);

create policy "authenticated users may read cycle cost estimates"
    on public.cycle_cost_estimates for select to authenticated using (true);

revoke all on public.ingestion_batches from anon, authenticated;
revoke insert, update, delete on public.telemetry from anon, authenticated;
revoke all on public.cycle_detection_versions, public.appliance_cycles,
    public.cycle_volume_labels, public.cycle_label_options,
    public.cycle_label_assignments, public.tariff_versions, public.afa_periods,
    public.cycle_cost_estimates from anon;
revoke insert, update, delete on public.cycle_detection_versions, public.appliance_cycles,
    public.cycle_volume_labels, public.cycle_label_options,
    public.cycle_label_assignments, public.tariff_versions, public.afa_periods,
    public.cycle_cost_estimates from authenticated;
grant select on public.appliance_profiles, public.telemetry,
    public.cycle_detection_versions, public.appliance_cycles,
    public.cycle_volume_labels, public.cycle_label_options,
    public.cycle_label_assignments, public.tariff_versions, public.afa_periods,
    public.cycle_cost_estimates to authenticated;

