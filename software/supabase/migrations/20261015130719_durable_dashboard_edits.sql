insert into public.appliance_profiles (id, profile_name, notes)
values ('manual-entry', 'Manual entry', 'User-entered records without sensor telemetry')
on conflict (id) do nothing;

insert into public.cycle_detection_versions (version, settings, calibration_status, effective_at)
values ('manual-entry-v1', '{"detection_applied":false}', 'simulation_default', now())
on conflict (version) do nothing;

grant select, insert, update on public.appliance_cycles, public.cycle_label_options,
    public.cycle_label_assignments to service_role;

create or replace function public.dashboard_edit_state() returns jsonb
language sql stable security invoker set search_path = public, pg_temp as $$
    select jsonb_build_object(
        'labels', coalesce((select jsonb_agg(jsonb_build_object(
            'label', label, 'is_active', is_active)) from public.cycle_label_options), '[]'),
        'manual_cycles', coalesce((select jsonb_agg(jsonb_build_object(
            'summary', summary, 'notes', manual_notes)) from public.appliance_cycles
            where record_source = 'manual'), '[]'),
        'assignments', coalesce((select jsonb_agg(jsonb_build_object(
            'cycle_id', a.cycle_id, 'label', o.label)) from public.cycle_label_assignments a
            join public.cycle_label_options o on o.id = a.label_option_id
            where a.is_active), '[]')
    );
$$;

create or replace function public.apply_dashboard_edit(operation text, payload jsonb)
returns jsonb language plpgsql security invoker set search_path = public, pg_temp as $$
declare
    label_name text := btrim(payload->>'label');
    option_id uuid;
    cycle jsonb := payload->'summary';
    target_id uuid;
    source_name text;
begin
    if operation not in ('add_label', 'archive_label', 'assign_label', 'manual_cycle') then
        raise exception 'Unsupported edit operation';
    end if;
    if label_name is null or char_length(label_name) not between 1 and 40 then
        raise exception 'Label must contain 1 to 40 characters';
    end if;
    if operation = 'archive_label' then
        update public.cycle_label_options set is_active = false
        where label_key = lower(label_name) and is_active;
        if not found then raise exception 'Active label does not exist'; end if;
        return to_jsonb(label_name);
    end if;
    if operation in ('add_label', 'manual_cycle') then
        insert into public.cycle_label_options(label, is_active) values(label_name, true)
        on conflict(label_key) do update set is_active = true
        returning id, label into option_id, label_name;
    else
        select id, label into option_id, label_name from public.cycle_label_options
        where label_key = lower(label_name) and is_active for update;
        if option_id is null then raise exception 'Active label does not exist'; end if;
    end if;
    if operation = 'add_label' then return to_jsonb(label_name); end if;

    target_id := (cycle->>'cycle_id')::uuid;
    source_name := case when payload->>'source' = 'manual' then 'manual' else 'measured' end;
    if operation = 'manual_cycle' and (source_name <> 'manual'
        or cycle->>'device_id' <> 'manual-entry'
        or cycle->>'detection_version' <> 'manual-entry-v1'
        or (cycle->>'started_at')::timestamptz > now()) then
        raise exception 'Invalid manual record';
    end if;
    insert into public.appliance_cycles(
        cycle_id, device_id, profile_id, started_at, ended_at, start_sequence, end_sequence,
        status, assessment, meter_energy_kwh, integrated_energy_kwh, summary,
        detection_version, record_source, manual_notes
    ) values (
        target_id, cycle->>'device_id', cycle->>'profile_id',
        (cycle->>'started_at')::timestamptz, (cycle->>'ended_at')::timestamptz,
        (cycle->>'start_sequence')::bigint, (cycle->>'end_sequence')::bigint,
        cycle->>'status', cycle->>'assessment', (cycle->>'meter_energy_kwh')::numeric,
        (cycle->>'integrated_energy_kwh')::numeric, cycle, cycle->>'detection_version',
        source_name, nullif(payload->>'notes', '')
    ) on conflict(cycle_id) do update set summary = excluded.summary,
        status = excluded.status, assessment = excluded.assessment,
        ended_at = excluded.ended_at, end_sequence = excluded.end_sequence,
        updated_at = now();

    -- Serialize assignments for this cycle so two edits cannot leave two active labels.
    perform 1 from public.appliance_cycles where cycle_id = target_id for update;
    update public.cycle_label_assignments set is_active = false where cycle_id = target_id;
    insert into public.cycle_label_assignments(cycle_id, label_option_id, source, is_active)
    values(target_id, option_id, 'dashboard', true);
    return to_jsonb(target_id::text);
end;
$$;

revoke all on function public.dashboard_edit_state() from public, anon, authenticated;
revoke all on function public.apply_dashboard_edit(text, jsonb) from public, anon, authenticated;
grant execute on function public.dashboard_edit_state() to service_role;
grant execute on function public.apply_dashboard_edit(text, jsonb) to service_role;
