create table public.reports (
  report_id uuid primary key,
  profile_id text not null references public.appliance_profiles(id),
  device_id text not null check (device_id ~ '^[a-zA-Z0-9_-]{1,64}$'),
  kind text not null check (kind in ('cycle', 'batch')),
  cycle_count integer not null check (
    (kind = 'cycle' and cycle_count = 1) or
    (kind = 'batch' and cycle_count in (5, 10, 20))
  ),
  period_start timestamptz not null,
  period_end timestamptz not null check (period_end >= period_start),
  generated_at timestamptz not null,
  reporting_version text not null check (char_length(reporting_version) between 1 and 64),
  engine text not null check (engine in ('template', 'codex')),
  status text not null check (status in ('generated', 'validation_failed', 'error')),
  evidence_hash text not null check (evidence_hash ~ '^[a-f0-9]{64}$'),
  evidence_snapshot jsonb not null check (jsonb_typeof(evidence_snapshot) = 'object'),
  report_text text not null check (char_length(report_text) between 1 and 100000)
);
create index reports_scope_generated_idx
  on public.reports (profile_id, device_id, generated_at desc, report_id desc);
alter table public.reports enable row level security;
revoke all on public.reports from public, anon, authenticated, service_role;
grant select, insert on public.reports to service_role;
comment on table public.reports is
  'Immutable evidence-backed report snapshots. Server-only reads/writes; public viewing is through Streamlit. No client RLS policies by design.';
