-- Shared comparison pool for a monitored appliance and its detection version.
create or replace function public.read_intelligence_current_power(
    p_cycle_id uuid, p_frozen_ids uuid[]
) returns jsonb
language sql stable security definer set search_path = '' as $$
    select jsonb_build_object(
        'cycle_count', count(power_w),
        'mean_power_w', avg(power_w),
        'training_rows', coalesce(jsonb_agg(jsonb_build_object(
            'cycle_id', cycle_id, 'average_power_w', power_w,
            'duration_seconds', duration_seconds
        ) order by cycle_id), '[]'::jsonb)
    )
    from (
        select member.cycle_id,
            case when f.cycle_id is not null
                then (f.evidence::jsonb #>> '{features,duration_seconds}')::numeric
                else (member.summary->>'duration_seconds')::numeric
            end as duration_seconds,
            case when f.cycle_id is not null
            then (f.evidence::jsonb #>> '{features,average_power_w}')::numeric
            else (member.summary->>'average_power_w')::numeric
        end as power_w
        from public.appliance_cycles target
        join public.appliance_cycles member
          on member.device_id = target.device_id and member.profile_id = target.profile_id
         and member.detection_version = target.detection_version
        left join public.intelligence_first_scores f on f.cycle_id = member.cycle_id
        where target.cycle_id = p_cycle_id and target.status = 'completed'
          and target.record_source = 'measured' and target.summary->>'data_origin' = 'sensor'
          and target.detection_version = 'sim-cycle-v1'
          and member.status = 'completed' and member.record_source = 'measured'
          and member.summary->>'data_origin' = 'sensor'
          and (f.cycle_id is not null or member.cycle_id = any(p_frozen_ids))
          and exists (
              select 1 from public.cycle_label_assignments a
              join public.cycle_label_options o on o.id = a.label_option_id
              where a.cycle_id = member.cycle_id and a.is_active and o.label_key = 'complete'
          )
    ) audited
    where power_w > 0 and power_w <= 25000 and duration_seconds > 0;
$$;
