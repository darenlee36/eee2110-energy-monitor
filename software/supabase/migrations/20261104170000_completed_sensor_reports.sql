create function public.save_completed_sensor_report(report jsonb) returns text
language plpgsql security definer set search_path = ''
as $$
declare
    cycle jsonb := report #> '{evidence_snapshot,cycles,0}';
    cost jsonb := cycle->'tariff_estimate';
    target_id uuid := (cycle->>'cycle_id')::uuid;
    target_report uuid := (report->>'report_id')::uuid;
begin
    if report->>'kind' is distinct from 'cycle'
       or report->>'engine' is distinct from 'template'
       or report->>'status' is distinct from 'generated'
       or jsonb_array_length(report #> '{evidence_snapshot,cycles}') is distinct from 1
       or cycle->>'record_source' is distinct from 'measured'
       or cycle->>'data_origin' is distinct from 'sensor'
       or cycle->>'status' is distinct from 'completed'
       or cycle->>'device_id' is distinct from report->>'device_id'
       or cycle->>'profile_id' is distinct from report->>'profile_id'
       or (cycle->>'started_at')::timestamptz is distinct from
          (report->>'period_start')::timestamptz
       or (cycle->>'ended_at')::timestamptz is distinct from
          (report->>'period_end')::timestamptz
       or (cost->>'energy_kwh')::numeric is distinct from (cycle->>'meter_energy_kwh')::numeric
    then
        raise exception 'Expected one completed sensor cycle and matching report evidence';
    end if;

    if 2 <> (
        select count(*) from public.telemetry t
        where t.device_id = cycle->>'device_id' and t.profile_id = cycle->>'profile_id'
        and t.device_id not like 'sim-cloud-%'
        and t.firmware_version like 'pzem-%' and t.quality_status = 'valid'
        and (t.sample_sequence, t.recorded_at) in (
            ((cycle->>'start_sequence')::bigint, (cycle->>'started_at')::timestamptz),
            ((cycle->>'end_sequence')::bigint, (cycle->>'ended_at')::timestamptz)
        )
    ) then
        raise exception 'Cycle boundaries do not match stored sensor telemetry';
    end if;

    insert into public.appliance_cycles (
        cycle_id, device_id, profile_id, started_at, ended_at, start_sequence, end_sequence,
        status, assessment, meter_energy_kwh, integrated_energy_kwh, summary, detection_version
    ) values (
        target_id, cycle->>'device_id', cycle->>'profile_id',
        (cycle->>'started_at')::timestamptz, (cycle->>'ended_at')::timestamptz,
        (cycle->>'start_sequence')::bigint, (cycle->>'end_sequence')::bigint,
        'completed', cycle->>'assessment', (cycle->>'meter_energy_kwh')::numeric,
        (cycle->>'integrated_energy_kwh')::numeric,
        cycle - array['tariff_estimate','readings','record_source',
                      'eligible_for_totals','collection_context'],
        cycle->>'detection_version'
    ) on conflict (cycle_id) do nothing;

    if not exists (
        select 1 from public.appliance_cycles c where c.cycle_id = target_id
        and c.record_source = 'measured' and c.status = 'completed'
        and c.device_id = cycle->>'device_id' and c.profile_id = cycle->>'profile_id'
        and c.started_at = (cycle->>'started_at')::timestamptz
        and c.ended_at = (cycle->>'ended_at')::timestamptz
        and c.start_sequence = (cycle->>'start_sequence')::bigint
        and c.end_sequence = (cycle->>'end_sequence')::bigint
        and c.meter_energy_kwh = round((cycle->>'meter_energy_kwh')::numeric, 6)
        and c.integrated_energy_kwh = round((cycle->>'integrated_energy_kwh')::numeric, 6)
    ) then
        raise exception 'Existing cycle differs from report evidence';
    end if;

    insert into public.cycle_cost_estimates (
        id, cycle_id, tariff_version, afa_version, status, energy_kwh,
        gross_variable_rate_sen_per_kwh, amount_rm, amount_range_low_rm,
        amount_range_high_rm, calculation
    ) values (
        md5(target_id::text || ':' || cost::text)::uuid, target_id,
        cost->>'tariff_version', cost->>'afa_version', cost->>'status',
        (cost->>'energy_kwh')::numeric, (cost->>'gross_variable_rate_sen_per_kwh')::numeric,
        (cost->>'amount_rm')::numeric, (cost #>> '{amount_range_rm,0}')::numeric,
        (cost #>> '{amount_range_rm,1}')::numeric, cost
    ) on conflict (id) do nothing;

    insert into public.reports
    select * from jsonb_populate_record(null::public.reports, report)
    on conflict (report_id) do nothing;
    if not exists (
        select 1 from public.reports r
        where r.report_id = target_report and r.evidence_hash = report->>'evidence_hash'
    ) then
        raise exception 'Existing report differs from submitted evidence';
    end if;
    return target_report::text;
end;
$$;

revoke all on function public.save_completed_sensor_report(jsonb)
    from public, anon, authenticated;
grant execute on function public.save_completed_sensor_report(jsonb) to service_role;
