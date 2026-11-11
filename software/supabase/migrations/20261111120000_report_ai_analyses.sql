create table public.report_ai_analyses (
    source_report_id uuid not null references public.reports(report_id),
    source_evidence_hash text not null check (source_evidence_hash ~ '^[a-f0-9]{64}$'),
    provider_model text not null check (char_length(provider_model) between 1 and 80),
    schema_version text not null check (char_length(schema_version) between 1 and 64),
    reserved_at timestamptz not null default now(),
    analysis_result jsonb check (
        jsonb_typeof(analysis_result) = 'object' and length(analysis_result::text) <= 12000
    ),
    primary key (source_report_id, source_evidence_hash, provider_model, schema_version)
);

alter table public.report_ai_analyses enable row level security;
revoke all on public.report_ai_analyses from public, anon, authenticated, service_role;
grant select, insert, update on public.report_ai_analyses to service_role;
create index report_ai_analyses_reserved_idx on public.report_ai_analyses (reserved_at);

create function public.read_report_ai_analysis(
    source_id uuid, source_hash text, model_name text, prompt_version text
) returns jsonb
language sql stable security invoker set search_path = '' as $$
    select coalesce(a.analysis_result, '{"pending":true}'::jsonb)
    from public.report_ai_analyses a
    where (a.source_report_id, a.source_evidence_hash, a.provider_model, a.schema_version)
        = (source_id, source_hash, model_name, prompt_version);
$$;

create function public.reserve_report_ai_analysis(
    source_id uuid, source_hash text, model_name text, prompt_version text
) returns text
language plpgsql security invoker set search_path = '' as $$
begin
    -- Lock all reservations so the shared daily cap holds across app sessions.
    perform pg_catalog.pg_advisory_xact_lock(2110, 20261111);
    if not exists (
        select 1 from public.reports r where r.report_id = source_id
        and r.evidence_hash = source_hash and r.engine = 'template' and r.status = 'generated'
    ) then
        return 'invalid_source';
    end if;
    if exists (
        select 1 from public.report_ai_analyses a
        where (a.source_report_id, a.source_evidence_hash, a.provider_model, a.schema_version)
            = (source_id, source_hash, model_name, prompt_version)
    ) then
        return 'existing';
    end if;
    if (select count(*) from public.report_ai_analyses a where a.reserved_at >=
        (date_trunc('day', now() at time zone 'Asia/Kuala_Lumpur') at time zone 'Asia/Kuala_Lumpur')
    ) >= 5 then
        return 'daily_limit';
    end if;
    insert into public.report_ai_analyses (
        source_report_id, source_evidence_hash, provider_model, schema_version
    ) values (source_id, source_hash, model_name, prompt_version);
    return 'reserved';
end;
$$;

create function public.finish_report_ai_analysis(
    source_id uuid, source_hash text, model_name text, prompt_version text, result jsonb
) returns boolean
language sql security invoker set search_path = '' as $$
    with updated as (
        update public.report_ai_analyses a set analysis_result = result
        where (a.source_report_id, a.source_evidence_hash, a.provider_model, a.schema_version)
            = (source_id, source_hash, model_name, prompt_version)
            and a.analysis_result is null and result is not null
        returning 1
    )
    select exists (select 1 from updated);
$$;

revoke all on function public.read_report_ai_analysis(uuid, text, text, text)
    from public, anon, authenticated;
revoke all on function public.reserve_report_ai_analysis(uuid, text, text, text)
    from public, anon, authenticated;
revoke all on function public.finish_report_ai_analysis(uuid, text, text, text, jsonb)
    from public, anon, authenticated;
grant execute on function public.read_report_ai_analysis(uuid, text, text, text) to service_role;
grant execute on function public.reserve_report_ai_analysis(uuid, text, text, text) to service_role;
grant execute on function public.finish_report_ai_analysis(uuid, text, text, text, jsonb)
    to service_role;
