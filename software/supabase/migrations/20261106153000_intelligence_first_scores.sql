-- Immutable first experimental assessments for completed, labelled sensor cycles.
create table public.intelligence_first_scores (
    cycle_id uuid primary key references public.appliance_cycles(cycle_id),
    evidence text not null check (octet_length(evidence) between 2 and 32768),
    created_at timestamptz not null default now()
);
alter table public.intelligence_first_scores enable row level security;
revoke all on public.intelligence_first_scores from public, anon, authenticated, service_role;

create function public.read_intelligence_first_score(p_cycle_id uuid) returns text
language sql stable security definer set search_path = '' as $$
    select evidence from public.intelligence_first_scores where cycle_id = p_cycle_id;
$$;

create function public.append_intelligence_first_score(p_evidence text) returns text
language plpgsql security definer set search_path = '' as $$
declare
    score jsonb;
    identity uuid;
    stored text;
begin
    if coalesce(octet_length(p_evidence), 0) not between 2 and 32768 then
        raise exception 'Invalid first-score evidence size';
    end if;
    score := p_evidence::jsonb;
    identity := (score->>'cycle_id')::uuid;
    if coalesce(score->>'model_artifact_sha256', '') !~ '^[0-9a-f]{64}$'
        or coalesce(score->>'trace_sha256', '') !~ '^[0-9a-f]{64}$'
        or coalesce(score->>'artifact_sha256', '') !~ '^[0-9a-f]{64}$'
        or coalesce(jsonb_typeof(score->'score'), '') <> 'number'
        or (score->>'score')::numeric not between 0 and 100
        or score->'reference_member' is distinct from 'false'::jsonb
        or score->>'classification' is distinct from 'not_evaluated'
    then raise exception 'Invalid first-score evidence'; end if;

    -- Share the annotation lock so a simultaneous label edit cannot authorize an insert.
    perform 1 from public.appliance_cycles c
    where c.cycle_id = identity and c.status = 'completed'
        and c.record_source = 'measured' and c.summary->>'data_origin' = 'sensor'
        and c.detection_version = 'sim-cycle-v1'
        and c.started_at = (score->>'started_at')::timestamptz
        and c.ended_at = (score->>'ended_at')::timestamptz
    for update;
    if not found then raise exception 'No matching completed sensor cycle'; end if;

    select evidence into stored from public.intelligence_first_scores where cycle_id = identity;
    if found then return stored; end if;
    if lower(public.dashboard_cycle_annotation(identity)->>'label') is distinct from 'complete'
    then raise exception 'Complete annotation required for first scoring'; end if;

    insert into public.intelligence_first_scores(cycle_id, evidence)
    values (identity, p_evidence);
    return p_evidence;
end;
$$;

revoke all on function public.read_intelligence_first_score(uuid)
    from public, anon, authenticated, service_role;
revoke all on function public.append_intelligence_first_score(text)
    from public, anon, authenticated, service_role;
grant execute on function public.read_intelligence_first_score(uuid),
    public.append_intelligence_first_score(text) to service_role;
