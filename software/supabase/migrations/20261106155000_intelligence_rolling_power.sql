-- A cycle contributes once to later comparisons, after Complete and a trace audit.
create function public.read_intelligence_rolling_power(
    p_cycle_id uuid, p_frozen_ids uuid[]
) returns jsonb
language sql stable security definer set search_path = '' as $$
    select jsonb_build_object(
        'cycle_count', count(power_w),
        'mean_power_w', avg(power_w)
    )
    from (
        select case when f.cycle_id is not null
                then (f.evidence::jsonb #>> '{features,average_power_w}')::numeric
                else (prior.summary->>'average_power_w')::numeric
            end as power_w
        from public.appliance_cycles target
        join public.appliance_cycles prior
          on prior.device_id = target.device_id and prior.profile_id = target.profile_id
          and prior.ended_at < target.started_at
        left join public.intelligence_first_scores f on f.cycle_id = prior.cycle_id
        join public.cycle_label_assignments a on a.cycle_id = prior.cycle_id and a.is_active
        join public.cycle_label_options o on o.id = a.label_option_id and o.label_key = 'complete'
        where target.cycle_id = p_cycle_id and target.status = 'completed'
          and target.record_source = 'measured' and target.summary->>'data_origin' = 'sensor'
          and target.detection_version = 'sim-cycle-v1'
          and prior.status = 'completed' and prior.record_source = 'measured'
          and prior.summary->>'data_origin' = 'sensor'
          and prior.detection_version = target.detection_version
          and (f.cycle_id is not null or prior.cycle_id = any(p_frozen_ids))
    ) audited
    where power_w > 0 and power_w <= 25000;
$$;
revoke all on function public.read_intelligence_rolling_power(uuid, uuid[])
    from public, anon, authenticated, service_role;
grant execute on function public.read_intelligence_rolling_power(uuid, uuid[])
    to service_role;
