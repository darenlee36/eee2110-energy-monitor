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
    battery_voltage_v double precision not null check (battery_voltage_v between 2.5 and 4.5),
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

alter table public.appliance_profiles enable row level security;
alter table public.ingestion_batches enable row level security;
alter table public.telemetry enable row level security;

create policy "authenticated users may read appliance profiles"
    on public.appliance_profiles for select to authenticated using (true);

create policy "authenticated users may read telemetry"
    on public.telemetry for select to authenticated using (true);

revoke all on public.ingestion_batches from anon, authenticated;
revoke insert, update, delete on public.telemetry from anon, authenticated;
grant select on public.appliance_profiles, public.telemetry to authenticated;

