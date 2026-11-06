-- Prospective, owner-mediated rule revisions. No changes to client grants on existing tables.
create table public.intelligence_appliance_rules (
    version text primary key references public.cycle_detection_versions(version),
    device_id text not null check (device_id ~ '^[a-zA-Z0-9_-]{1,64}$'),
    profile_id text not null references public.appliance_profiles(id),
    appliance_name text not null check (char_length(btrim(appliance_name)) between 1 and 64),
    effective_at timestamptz not null,
    settings jsonb not null check (jsonb_typeof(settings) = 'object'),
    unique (device_id, profile_id, effective_at)
);
alter table public.intelligence_appliance_rules enable row level security;
revoke all on public.intelligence_appliance_rules from public, anon, authenticated, service_role;
grant select on public.intelligence_appliance_rules to service_role;

create function public.append_intelligence_appliance_rule(
    p_device_id text, p_profile_id text, p_appliance_name text, p_settings jsonb
) returns text
language plpgsql security definer set search_path = '' as $$
declare
    rule_version text := 'appliance-' || replace(gen_random_uuid()::text, '-', '');
    fields text[] := array[
        'start_power_w', 'stop_power_w', 'start_confirm_samples', 'stop_confirm_samples',
        'minimum_duration_seconds', 'maximum_duration_seconds', 'warning_gap_seconds',
        'terminating_gap_seconds', 'energy_difference_tolerance'
    ];
    field text;
    latest timestamptz;
    cutoff timestamptz;
    validated jsonb;
begin
    if p_device_id is null or p_device_id !~ '^[a-zA-Z0-9_-]{1,64}$'
        or p_profile_id is null or p_profile_id !~ '^[a-zA-Z0-9_-]{1,64}$'
        or coalesce(char_length(btrim(p_appliance_name)), 0) not between 1 and 64
        or coalesce(jsonb_typeof(p_settings), '') <> 'object'
    then raise exception 'Invalid appliance rule scope or shape'; end if;
    foreach field in array fields loop
        if coalesce(jsonb_typeof(p_settings->field), '') <> 'number' then
            raise exception 'Missing or invalid appliance rule field';
        end if;
    end loop;
    if p_settings - fields <> '{}'::jsonb
        or (p_settings->>'stop_power_w')::numeric <= 0
        or (p_settings->>'start_power_w')::numeric <= (p_settings->>'stop_power_w')::numeric
        or (p_settings->>'start_power_w')::numeric > 25000
        or (p_settings->>'energy_difference_tolerance')::numeric not between 0.000001 and 1
    then raise exception 'Invalid power threshold or energy tolerance'; end if;
    foreach field in array fields[3:8] loop
        if (p_settings->>field) !~ '^[0-9]{1,5}$'
            or (p_settings->>field)::integer not between 1 and 86400
        then raise exception 'Invalid confirmation or duration bound'; end if;
    end loop;
    if (p_settings->>'maximum_duration_seconds')::integer
          <= (p_settings->>'minimum_duration_seconds')::integer
        or (p_settings->>'terminating_gap_seconds')::integer
          <= (p_settings->>'warning_gap_seconds')::integer
    then raise exception 'Appliance rule duration or gap bounds are reversed'; end if;

    perform pg_advisory_xact_lock(hashtextextended(p_device_id || ':' || p_profile_id, 0));
    select max(recorded_at) into latest from public.telemetry
    where device_id = p_device_id and profile_id = p_profile_id;
    if latest is null then raise exception 'No telemetry for that stream'; end if;
    select greatest(clock_timestamp(), latest + interval '1 microsecond',
                    max(effective_at) + interval '1 microsecond') into cutoff
    from public.intelligence_appliance_rules
    where device_id = p_device_id and profile_id = p_profile_id;
    validated := p_settings || jsonb_build_object('version', rule_version);
    insert into public.cycle_detection_versions(version, settings, calibration_status, effective_at)
    values(rule_version, validated, 'simulation_default', cutoff);
    insert into public.intelligence_appliance_rules
    values(rule_version, p_device_id, p_profile_id, btrim(p_appliance_name), cutoff, validated);
    return rule_version;
end;
$$;
revoke all on function public.append_intelligence_appliance_rule(text, text, text, jsonb)
    from public, anon, authenticated, service_role;
grant execute on function public.append_intelligence_appliance_rule(text, text, text, jsonb)
    to service_role;
