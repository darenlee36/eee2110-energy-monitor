alter table public.cycle_label_assignments
    add column annotation_note text check (char_length(annotation_note) <= 500),
    add column public_annotation boolean not null default false;

create function public.dashboard_cycle_annotation(p_cycle_id uuid) returns jsonb
language sql stable security invoker set search_path = '' as $$
    select coalesce((
        select jsonb_build_object('id', a.id, 'label', o.label, 'note', a.annotation_note,
                                  'public', a.public_annotation)
        from public.cycle_label_assignments a
        join public.cycle_label_options o on o.id = a.label_option_id
        where a.cycle_id = p_cycle_id and a.is_active
    ), '{}'::jsonb);
$$;

create function public.save_public_cycle_annotation(
    p_cycle_id uuid, p_device_id text, p_profile_id text, p_origin text,
    p_label text, p_note text, p_expected_id uuid
) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare
    label_value text := btrim(regexp_replace(p_label, '[[:space:]]+', ' ', 'g'));
    note_value text := nullif(btrim(p_note), '');
    current_annotation jsonb;
    option_id uuid;
begin
    if label_value is null or char_length(label_value) not between 1 and 40
       or label_value ~ '[[:cntrl:]]'
       or coalesce(char_length(note_value), 0) > 500
       or coalesce(replace(replace(replace(note_value, chr(10), ''), chr(13), ''), chr(9), ''), '')
          ~ '[[:cntrl:]]' then
        raise exception 'Invalid cycle annotation';
    end if;

    -- Share the same cycle lock as the legacy assignment path.
    perform 1 from public.appliance_cycles c
    where c.cycle_id = p_cycle_id and c.device_id = p_device_id and c.profile_id = p_profile_id
      and c.status = 'completed' and c.summary->>'data_origin' = p_origin
    for update;
    if not found then
        return jsonb_build_object('status', 'unavailable');
    end if;
    current_annotation := public.dashboard_cycle_annotation(p_cycle_id);
    if lower(current_annotation->>'label') = lower(label_value)
       and (current_annotation->>'note') is not distinct from note_value then
        return jsonb_build_object('status', 'saved');
    end if;
    if (current_annotation->>'id')::uuid is distinct from p_expected_id then
        return jsonb_build_object('status', 'conflict');
    end if;
    if (select count(*) from public.cycle_label_assignments
        where cycle_id = p_cycle_id and public_annotation
        and created_at > now() - interval '1 minute') >= 10 then
        return jsonb_build_object('status', 'limited');
    end if;

    insert into public.cycle_label_options(label) values (label_value)
    on conflict (label_key) do nothing;
    select id into option_id from public.cycle_label_options
    where label_key = lower(label_value) and is_active for share;
    if option_id is null then
        return jsonb_build_object('status', 'unavailable');
    end if;
    update public.cycle_label_assignments set is_active = false
    where cycle_id = p_cycle_id and is_active;
    insert into public.cycle_label_assignments(
        cycle_id, label_option_id, source, annotation_note, public_annotation
    ) values (p_cycle_id, option_id, 'dashboard', note_value, true);
    return jsonb_build_object('status', 'saved');
end;
$$;

revoke all on function public.dashboard_cycle_annotation(uuid) from public, anon, authenticated;
revoke all on function public.save_public_cycle_annotation(uuid,text,text,text,text,text,uuid)
    from public, anon, authenticated;
grant execute on function public.dashboard_cycle_annotation(uuid) to service_role;
grant execute on function public.save_public_cycle_annotation(uuid,text,text,text,text,text,uuid)
    to service_role;
